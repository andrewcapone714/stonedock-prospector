#!/usr/bin/env python3
"""StoneDock Prospector.

Finds likely prospects for StoneDock Connect, three ways:
  Low-rated apps  - health, wellness, telehealth and pharmacy companies whose own
                    apps are rated 3 stars or lower on the App Store.
  New apps        - health companies whose first App Store app launched recently.
  Newly funded    - health startups incorporated in the last five years that filed
                    an SEC Form D (raised private money) recently.
  New providers   - pharmacies and clinics registered in the federal NPI registry in
                    the last 30-180 days, with the owner/official named.
  Medicare-rated  - home health agencies and dialysis centers Medicare rates 3 stars
                    or lower (CMS Care Compare).
Google Play ratings are merged into the app lists (by developer name).

Pebble, the AI helper (pebble.js on the page), researches companies, writes outreach emails, answers questions about
the prospects and double-checks fit scores, through Claude Code (Claude plan) or Codex (ChatGPT plan), this computer only.
Serves the tool at http://localhost:8770. Standard library only, no API keys.

While it runs, it re-runs both searches on a schedule (Settings: every 1-24 hours,
default 6) with the last terms used, records when each company first appeared so
the page can badge newcomers, and can add strong new fits to the pipeline.

Cloud mode: when this folder is a git repository with a GitHub remote, GitHub
Actions (.github/workflows/update.yml) runs `python server.py --update-once` every
2 hours, which runs whichever search is due and pushes the results. The local
server then stops searching on its own schedule and instead syncs with GitHub at
start, every 15 minutes, and shortly after pipeline or settings changes. The SEC
contact email stays on this computer (data/local.json, never uploaded); the cloud
reads it from the SEC_CONTACT repository secret.

    python server.py            # then open http://localhost:8770
    python server.py --no-open  # don't open a browser tab

Sources (no accounts needed):
  Apple iTunes Search API - apps, ratings, review counts, developer and website.
                            Apple allows about 20 calls a minute, so searches run
                            one term every 3 s and each term is cached for a day.
  Google Play             - search and app pages (no official API; Google's terms forbid
                            automated access, so this may break or be blocked). One page
                            every 1.5 s; each app page reused for a week (data/play.json).
  NPI Registry API        - CMS's public provider registry. Searched by ZIP prefix (its
                            1,200-result cap is split around); about 1,500 requests, daily.
  CMS Provider Data       - Medicare Care Compare datasets, weekly.
  Company websites        - home, about, team and contact pages for public emails
                            and LinkedIn links.
  SEC EDGAR               - Form D filings (private fundraising). The search works
                            as is; filing details (officers, amount raised) need a
                            contact email in Settings, because SEC requires one in
                            the User-Agent of automated requests. The newly funded
                            scan reads up to 400 filings at ~5 a second (SEC allows
                            10) and caches each one for good, since filings don't change.
LinkedIn is never scraped: the UI only builds search links you open yourself.
"""
import csv
import html
import http.server
import io
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

PORT = int(os.environ.get("PROSPECTOR_PORT", "8770"))
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "cache")
RESULTS_FILE = os.path.join(DATA, "results.json")
LEADS_FILE = os.path.join(DATA, "leads.json")
SETTINGS_FILE = os.path.join(DATA, "settings.json")
LOCAL_FILE = os.path.join(DATA, "local.json")      # this computer only, never uploaded
LOCAL_KEYS = {"sec_contact"}
FUNDED_FILE = os.path.join(DATA, "funded.json")
SEC_CACHE = os.path.join(CACHE, "sec")
SEEN_FILE = os.path.join(DATA, "seen.json")    # "source:id" -> when it first appeared (0 = before tracking)
AUTO_FILE = os.path.join(DATA, "auto.json")    # when each search last finished
TOOL_UA = "StoneDockProspector/1.0"  # every request says what it is; sources that refuse it are left out

ITUNES_GAP = 3.0                 # seconds between iTunes calls (~20/min limit)
TERM_CACHE_SECONDS = 24 * 3600
HEALTH_GENRES = {6013: "Health & Fitness", 6020: "Medical"}

DEFAULT_TERMS = [
    "telehealth", "telemedicine", "online pharmacy", "prescription delivery",
    "GLP-1", "weight loss program", "peptides", "hormone therapy", "men's health",
    "women's health", "lab results", "health coaching", "virtual primary care",
    "functional medicine", "longevity", "wellness clinic", "IV therapy", "med spa",
    "nutrition coaching", "chronic care", "remote patient monitoring", "mental health therapy",
]

DEFAULT_TEMPLATE = """Subject: Powering {app} with a health data and AI engine

Hi {first_name},

I came across {app} while researching {category} apps. I'm with StoneDock, and we build StoneDock Connect: a white-label engine that runs inside a partner's own app, under their brand.

Connect is built on FHIR, the data standard hospitals and EHRs like Epic use. It's HIPAA-ready with patient consent on every request, it reads lab PDFs and turns them into health scores and coaching, and it already runs payment, prescription, EHR sync and refills for our live partner, MyWellness App. Patients never leave your app.

Would you be open to a 20-minute demo? We can show you the sandbox and how it would plug into {company}'s app.

Best,
{sender}"""

DEFAULT_SETTINGS = {"sec_contact": "", "sender": "", "template": DEFAULT_TEMPLATE,
                    "auto_update": True, "auto_every_hours": 6, "auto_add_min": 0,
                    "play_enabled": True, "provider_days": 90, "ai_model": "sonnet"}

# SEC full-text search only matches words in a filing (in practice, the company name),
# so these are words health startups tend to put in their names
FUNDED_TERMS = ["health", "medical", "care", "clinic", "wellness", "telehealth", "pharmacy",
                "rx", "therapy", "longevity", "nutrition", "med"]
FUNDED_MAX_FILINGS = 400
# Form D industries a Connect customer could file under
FUNDED_INDUSTRIES = {"Other Health Care", "Hospitals and Physicians", "Health Insurance", "Pharmaceuticals",
                     "Biotechnology", "Other Technology", "Computers", "Telecommunications", "Other"}
FUND_NAME = re.compile(r"\b(fund|spv|series of|l\.?p\.?|lp|capital|ventures|investments?|partners|"
                       r"reit|realty|properties|apartments|holdings llc)\b", re.I)

lock = threading.RLock()
job = {"running": False, "done": 0, "total": 0, "term": "", "found": 0, "errors": [], "finished_at": None}
fjob = {"running": False, "done": 0, "total": 0, "stage": "", "found": 0, "errors": [], "finished_at": None}
pjob = {"running": False, "done": 0, "total": 0, "stage": "", "found": 0, "errors": [], "finished_at": None}
auto = {"version": 0, "last_new": 0, "last_new_at": None, "last_added": 0, "error": ""}


# ---------- storage ----------

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:  # same bytes on Windows and GitHub
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(load_json(SETTINGS_FILE, {}))
    s.update(load_json(LOCAL_FILE, {}))
    if os.environ.get("SEC_CONTACT"):  # the cloud runner's repository secret
        s["sec_contact"] = os.environ["SEC_CONTACT"]
    return s


def save_settings(s):
    save_json(SETTINGS_FILE, {k: v for k, v in s.items() if k not in LOCAL_KEYS})
    save_json(LOCAL_FILE, {k: v for k, v in s.items() if k in LOCAL_KEYS})


def live_leads():
    """The pipeline without deleted leads (those stay as markers so a sync can't bring them back)."""
    return {k: v for k, v in load_json(LEADS_FILE, {}).items() if not v.get("deleted")}


# ---------- http helpers ----------

def fetch(url, ua=TOOL_UA, timeout=15, limit=2_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit)
        charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace"), r.geturl()


_itunes_last = 0.0
_itunes_lock = threading.Lock()


def itunes(params):
    """Throttled call to the iTunes Search/Lookup API, retried once on a refusal."""
    global _itunes_last
    path = "lookup" if "id" in params else "search"
    url = f"https://itunes.apple.com/{path}?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        with _itunes_lock:
            wait = _itunes_last + ITUNES_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            _itunes_last = time.time()
        try:
            text, _ = fetch(url, limit=30_000_000)
            return json.loads(text).get("results", [])
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 2:
                time.sleep(20 * (attempt + 1))
                continue
            raise


def search_term(term, country, max_age=TERM_CACHE_SECONDS):
    key = re.sub(r"[^a-z0-9]+", "_", f"{country}_{term}".lower())
    path = os.path.join(CACHE, key + ".json")
    cached = load_json(path, None)
    if cached and time.time() - cached["at"] < max_age:
        return cached["results"]
    results = itunes({"term": term, "entity": "software", "country": country, "limit": 200})
    save_json(path, {"at": time.time(), "results": results})
    return results


# ---------- scoring ----------

def parse_date(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def years_since(d):
    return (datetime.now(timezone.utc) - d).days / 365.25 if d else None


# What StoneDock Connect does: an app that mentions these is a closer fit
RELEVANT = {
    "telehealth": r"tele(health|medicine)|virtual (visit|care|clinic)|video visit",
    "prescriptions": r"prescription|pharmac|refill|\brx\b",
    "labs": r"lab (result|test|work)|blood (test|work)|\blabs\b",
    "GLP-1 / weight loss": r"glp-?1|semaglutide|tirzepatide|weight loss|weight-loss",
    "peptides / hormones": r"peptide|hormone|\btrt\b|testosterone|menopause",
    "wellness / coaching": r"wellness|longevity|health coach|coaching",
    "patient care": r"primary care|patient|provider|clinic",
}
# Names that point to a big, established organization rather than a startup
LARGE_ORG = re.compile(
    r"\b(university|hospital|memorial|health plan|health ?system|medical center|healthcare system|society|association|college|"
    r"department|county|state of|government|ministry|stores|supermarket|kaiser|cvs|walgreens|united ?health(care)?|"
    r"cigna|aetna|humana|anthem|elevance|blue cross|bcbs|optum|commonspirit|providence|ascension|"
    r"fresenius|davita|u\.?s\.? renal|hca|tenet|uspi|surgery partners)\b", re.I)


COMPANY_WORDS = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|gmbh|ag|bv|pbc|pllc|plc|pty|sas|sa|srl|lp|llp|"
    r"health|healthcare|medical|medicine|clinic|pharmacy|labs?|group|technologies|technology|tech|software|"
    r"solutions|systems|apps?|studios?|digital|care|wellness|services|partners|institute|foundation|"
    r"network|enterprises)\b", re.I)


def looks_individual(name):
    """App Store sellers that are a person's name ("Jane Doe") are solo developers, not companies."""
    words = (name or "").split()
    return 2 <= len(words) <= 4 and not COMPANY_WORDS.search(name) and not re.search(r"[&\d]", name) and all(w[0].isupper() for w in words if w[0].isalpha())


def relevance_hits(apps):
    text = " ".join(f"{a['name']} {a.get('description', '')}" for a in apps).lower()
    return [k for k, pat in RELEVANT.items() if re.search(pat, text)]


def score_new_app(c):
    """Fit score for a company whose first health app launched recently (any rating)."""
    apps = list(c["apps"].values())
    why = []
    months = years_since(parse_date(c["first_released"])) * 12
    new = 35 if months <= 6 else 28 if months <= 12 else 20 if months <= 18 else 12
    why.append(f"+{new} first app launched {months:.0f} months ago")

    hits = relevance_hits(apps)
    rel = [0, 10, 18, 25][min(len(hits), 3)]
    why.append(f"+{rel} " + (f"apps mention {', '.join(hits)}" if hits else "no telehealth, pharmacy or lab focus"))

    total = c["total_ratings"]
    size = 20 if total < 100 else 12 if total < 1000 else 4
    why.append(f"+{size} {total:,} ratings ({'early user base' if total < 1000 else 'already growing fast'})")

    idle = years_since(parse_date(c["last_updated"]))
    active = 10 if idle is not None and idle <= .25 else 6 if idle is not None and idle <= .5 else 2 if idle is not None and idle <= 1 else 0
    if idle is not None:
        why.append(f"+{active} last update {idle * 12:.0f} months ago")

    rated = [a for a in apps if a["count"] >= 3 and a["rating"]]
    worst = min(rated, key=lambda a: a["rating"]) if rated else None
    if not worst:
        unhappy = 5
        why.append("+5 not enough ratings yet")
    else:
        unhappy = 10 if worst["rating"] <= 3.5 else 0
        why.append(f"+{unhappy} rated {worst['rating']:.1f}★" + (" (users unhappy)" if unhappy else ""))

    big = 30 if LARGE_ORG.search(c["company"] or "") else 0
    if big:
        why.append(f"−{big} name suggests a large established organization")
    solo = 40 if looks_individual(c["company"]) else 0
    if solo:
        why.append(f"−{solo} seller looks like an individual developer, not a company")

    c["score"] = max(0, round(new + rel + size + active + unhappy - big - solo))
    c["solo"] = bool(solo)
    c["why"] = why


def new_apps(months):
    """Companies from past App Store searches whose first health app launched within `months`."""
    res = load_json(RESULTS_FILE, {"prospects": {}})
    out = []
    for c in res["prospects"].values():
        apps = list(c["apps"].values()) if isinstance(c["apps"], dict) else c["apps"]
        first = min((a["released"] for a in apps if a.get("released")), default=None)
        age = years_since(parse_date(first))
        if age is None or age * 12 > months:
            continue
        n = {k: c.get(k) for k in ("id", "company", "developer_url", "website", "terms")}
        n.update(apps={a["id"]: a for a in apps}, first_released=first,
                 last_updated=max((a["updated"] for a in apps if a.get("updated")), default=None),
                 total_ratings=sum(a["count"] for a in apps))
        score_new_app(n)
        n["apps"] = sorted(apps, key=lambda a: a.get("released") or "", reverse=True)
        n["worst"] = n["apps"][0]  # the newest app is the one shown in the table
        out.append(n)
    out.sort(key=lambda p: -p["score"])
    return out


def score_prospect(p, max_rating):
    worst = p["worst"]
    why = []
    r = worst["rating"]
    need = 12 + 23 * (max_rating - r) / max(max_rating - 1, 0.5)
    need = max(0, min(35, need))
    why.append(f"+{need:.0f} worst app rated {r:.1f}★")

    hits = relevance_hits(p["apps"].values())
    rel = [0, 8, 14, 20][min(len(hits), 3)]
    why.append(f"+{rel} " + (f"apps mention {', '.join(hits)}" if hits else "no telehealth, pharmacy or lab focus"))

    total = p["total_ratings"]
    if total < 20:
        size, note = 8, "very small user base"
    elif total <= 5000:
        size, note = 20, "startup-sized audience"
    elif total <= 50000:
        size, note = 12, "mid-sized audience"
    else:
        size, note = 0, "large audience, likely established"
    why.append(f"+{size} {total:,} ratings ({note})")

    age = years_since(parse_date(p["first_released"]))
    if age is None:
        young = 0
    elif age <= 3:
        young = 15
    elif age <= 6:
        young = 9
    else:
        young = 3
    if age is not None:
        why.append(f"+{young} first app launched {age:.1f} yrs ago")

    idle = years_since(parse_date(p["last_updated"]))
    if idle is None:
        active = 0
    elif idle <= 1:
        active = 10
    elif idle <= 2:
        active = 5
    else:
        active = 0
    if idle is not None:
        why.append(f"+{active} last update {idle:.1f} yrs ago" + ("" if active else " (may be inactive)"))

    big = 30 if LARGE_ORG.search(p["company"] or "") else 0
    if big:
        why.append(f"−{big} name suggests a large established organization")
    solo = 30 if looks_individual(p["company"]) else 0
    if solo:
        why.append(f"−{solo} seller looks like an individual developer, not a company")

    p["score"] = max(0, round(need + rel + size + young + active - big - solo))
    p["solo"] = bool(solo)
    p["why"] = why


def run_search(terms, max_rating, min_reviews, country, merge, max_age=TERM_CACHE_SECONDS):
    with lock:
        prev = load_json(RESULTS_FILE, {"prospects": {}})
        companies = prev["prospects"] if merge else {}
    for i, term in enumerate(terms):
        with lock:
            job.update(term=term, done=i)
        try:
            apps = search_term(term, country, max_age)
        except Exception as e:
            with lock:
                job["errors"].append(f"{term}: {e}")
            continue
        for a in apps:
            if a.get("primaryGenreId") not in HEALTH_GENRES and not set(map(int, a.get("genreIds", []))) & set(HEALTH_GENRES):
                continue
            cid = str(a.get("artistId"))
            c = companies.setdefault(cid, {
                "id": cid, "company": a.get("sellerName") or a.get("artistName"),
                "developer_url": a.get("artistViewUrl"), "website": a.get("sellerUrl") or "",
                "apps": {}, "terms": [],
            })
            if not c["website"] and a.get("sellerUrl"):
                c["website"] = a["sellerUrl"]
            if term not in c["terms"]:
                c["terms"].append(term)
            c["apps"][str(a["trackId"])] = {
                "id": str(a["trackId"]), "store": "apple", "name": a.get("trackName"),
                "rating": round(a.get("averageUserRating") or 0, 2),
                "count": a.get("userRatingCount") or 0,
                "genre": HEALTH_GENRES.get(a.get("primaryGenreId"), a.get("primaryGenreName")),
                "released": a.get("releaseDate"), "updated": a.get("currentVersionReleaseDate"),
                "url": (a.get("trackViewUrl") or "").split("?")[0], "icon": a.get("artworkUrl60"),
                "price": a.get("formattedPrice"), "description": (a.get("description") or "")[:600],
            }
        with lock:
            job["found"] = sum(1 for c in companies.values() if qualifying(c, max_rating, min_reviews))
    settings = get_settings()
    if settings.get("play_enabled", True):
        def report(stage, i, error=None):
            with lock:
                if stage:
                    job.update(term=stage, done=len(terms) + i, total=len(terms) * 2)
                if error:
                    job["errors"].append(error)
        play_into(companies, terms, report)
    prospects = {}
    for cid, c in companies.items():
        q = qualifying(c, max_rating, min_reviews)
        if not q:
            continue
        apps = list(c["apps"].values())
        c["worst"] = min(q, key=lambda x: x["rating"])
        c["total_ratings"] = sum(x["count"] for x in apps)
        c["first_released"] = min((x["released"] for x in apps if x["released"]), default=None)
        c["last_updated"] = max((x["updated"] for x in apps if x["updated"]), default=None)
        score_prospect(c, max_rating)
        prospects[cid] = c
    # keep non-qualifying companies too when merging, so a later run with looser filters can use them
    store = {cid: c for cid, c in companies.items()}
    for cid, p in prospects.items():
        store[cid] = p
    with lock:
        save_json(RESULTS_FILE, {"prospects": store, "filters": {
            "max_rating": max_rating, "min_reviews": min_reviews, "country": country},
            "terms": terms, "updated": time.time()})
        mark_ran("apps")
    with lock:
        job.update(running=False, done=job["total"], term="", found=len(prospects), finished_at=time.time())
    track_new()


def qualifying(c, max_rating, min_reviews):
    return [a for a in c["apps"].values() if a["count"] >= min_reviews and 0 < a["rating"] <= max_rating]


def current_prospects():
    res = load_json(RESULTS_FILE, {"prospects": {}, "filters": {}})
    f = res.get("filters") or {}
    out = [p for p in res["prospects"].values() if "score" in p
           and qualifying(p, f.get("max_rating", 3), f.get("min_reviews", 5))]
    for p in out:
        p["apps"] = sorted(p["apps"].values(), key=lambda a: a["rating"]) if isinstance(p["apps"], dict) else p["apps"]
    out.sort(key=lambda p: -p["score"])
    return out, f, res.get("updated")


# ---------- website enrichment ----------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$|@(sentry|wixpress|example|domain|email)\.|^(u00|name@|user@|your)", re.I)
PAGE_HINT = re.compile(r"about|team|leadership|company|contact|people|founder|who-we-are|our-story", re.I)


def site_root(url):
    u = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    return f"{u.scheme or 'https'}://{u.netloc}"


def website_from_store(app_url):
    """Fallback: the first non-Apple link on the App Store page (usually privacy or support)."""
    text, _ = fetch(app_url)
    for href in re.findall(r'href="(https?://[^"]+)"', text):
        host = urllib.parse.urlparse(href).netloc.lower()
        if host and not re.search(r"apple\.com|mzstatic\.com|apple\.co$", host):
            return site_root(href)
    return ""


def enrich_site(website):
    root = site_root(website)
    host = urllib.parse.urlparse(root).netloc.lower().removeprefix("www.")
    emails, linkedin, checked, errors = {}, set(), [], []
    queue, seen = [root + "/"], set()
    while queue and len(checked) < 5:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            text, final = fetch(url, timeout=12)
        except Exception as e:
            errors.append(f"{url}: {e}")
            continue
        checked.append(final)
        body = html.unescape(text)
        for m in re.findall(r"mailto:([^\"'?>\s]+)", body) + EMAIL_RE.findall(body):
            m = urllib.parse.unquote(m).strip(".").lower()
            if EMAIL_RE.fullmatch(m) and not BAD_EMAIL.search(m):
                emails.setdefault(m, final)
        for li in re.findall(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/[A-Za-z0-9_%\-.]+", body):
            linkedin.add(li.rstrip("/."))
        if len(checked) == 1:
            for href in re.findall(r'href=["\']([^"\'#]+)', body):
                full = urllib.parse.urljoin(final, href)
                p = urllib.parse.urlparse(full)
                if p.netloc.lower().removeprefix("www.") == host and PAGE_HINT.search(p.path) and full not in queue:
                    queue.append(full.split("?")[0])
            queue = queue[:8]
    return {
        "website": root, "checked": checked, "errors": errors[:3],
        "emails": [{"email": e, "page": p} for e, p in emails.items()][:15],
        "linkedin": sorted(linkedin)[:10], "at": time.time(),
    }


# ---------- SEC Form D ----------

SUFFIX_RE = re.compile(r"[,.]|\b(inc|llc|l\.l\.c|ltd|corp|corporation|co|company|pbc|pllc|pc|lp|holdings|group|technologies|technology|health|labs)\b", re.I)


def norm_name(s):
    return " ".join(SUFFIX_RE.sub(" ", s or "").lower().split())


def sec_search(name):
    q = urllib.parse.quote(f'"{name}"')
    text, _ = fetch(f"https://efts.sec.gov/LATEST/search-index?q={q}&forms=D")
    latest = {}  # one row per company: its newest Form D
    target = norm_name(name)
    for h in json.loads(text).get("hits", {}).get("hits", []):
        s = h["_source"]
        display = (s.get("display_names") or [""])[0]
        clean = re.sub(r"\s*\(CIK \d+\)\s*$", "", display)
        cik = (s.get("ciks") or [""])[0]
        if cik in latest and (latest[cik]["file_date"] or "") >= (s.get("file_date") or ""):
            continue
        latest[cik] = {
            "name": clean, "cik": cik, "adsh": s.get("adsh"), "file_date": s.get("file_date"),
            "location": ", ".join(s.get("biz_locations") or []), "inc_state": ", ".join(s.get("inc_states") or []),
            "exact": norm_name(clean) == target and bool(target),
            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=D",
        }
    hits = list(latest.values())
    exact = sorted([x for x in hits if x["exact"]], key=lambda x: x["file_date"] or "", reverse=True)
    return exact + [x for x in hits if not x["exact"]][:6]


def tag(el):
    return el.tag.split("}")[-1]


def find_text(root, name):
    for el in root.iter():
        if tag(el) == name and (el.text or "").strip():
            return el.text.strip()
    return ""


def child_value(root, parent):
    """Text of the <value> under the first <parent> element (Form D wraps dates and years this way)."""
    for el in root.iter():
        if tag(el) == parent:
            return find_text(el, "value")
    return ""


def sec_filing(cik, adsh, contact):
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/primary_doc.xml"
    text, _ = fetch(url, ua=f"StoneDock Prospector {contact}")
    if "Undeclared Automated Tool" in text:
        raise RuntimeError("SEC refused the request; check the contact email in Settings")
    root = ET.fromstring(text.encode("utf-8"))
    people = []
    for rp in root.iter():
        if tag(rp) != "relatedPersonInfo":
            continue
        first = last = ""
        roles = []
        for el in rp.iter():
            t = tag(el)
            if t == "firstName":
                first = (el.text or "").strip()
            elif t == "lastName":
                last = (el.text or "").strip()
            elif t == "relationship":
                roles.append((el.text or "").strip())
            elif t == "relationshipClarification" and (el.text or "").strip():
                roles.append(el.text.strip())
        people.append({"name": f"{first} {last}".strip(), "roles": roles})
    return {
        "filing_url": url.replace("primary_doc.xml", ""),
        "entity": find_text(root, "entityName"), "entity_type": find_text(root, "entityType"),
        # Form D gives the exact year only for companies under five years old
        "year_of_inc": child_value(root, "yearOfInc") or (
            "over 5 yrs before filing" if find_text(root, "overFiveYears") == "true" else ""),
        "industry": find_text(root, "industryGroupType"),
        "offering_total": find_text(root, "totalOfferingAmount"),
        "amount_sold": find_text(root, "totalAmountSold"),
        "first_sale": child_value(root, "dateOfFirstSale"),
        "people": people,
    }


# ---------- newly funded startups (SEC Form D) ----------

def efts_page(term, start, end, offset):
    params = {"q": f'"{term}"', "forms": "D", "dateRange": "custom",
              "startdt": start.isoformat(), "enddt": end.isoformat(), "from": offset}
    text, _ = fetch("https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(params))
    return json.loads(text).get("hits", {}).get("hits", [])


def cached_filing(cik, adsh, contact):
    path = os.path.join(SEC_CACHE, adsh + ".json")
    cached = load_json(path, None)
    if cached is not None:
        return cached
    detail = sec_filing(cik, adsh, contact)
    save_json(path, detail)
    return detail


NAME_FIT = re.compile(r"tele|virtual|digital|\brx\b|pharm|clinic|care|wellness|health|medic|therap|"
                      r"longevity|nutrition|weight|hormone|peptide|\blabs?\b", re.I)


def score_funded(f):
    why = []
    age = date.today().year - int(f["year_of_inc"])
    new = 30 if age <= 0 else 26 if age == 1 else 20 if age == 2 else 15 if age == 3 else 10
    why.append(f"+{new} incorporated in {f['year_of_inc']}")

    ind = f.get("industry") or "not given"
    fit = {"Other Health Care": 25, "Hospitals and Physicians": 22, "Health Insurance": 18,
           "Pharmaceuticals": 14, "Other Technology": 14, "Computers": 12, "Biotechnology": 5}.get(ind, 8)
    why.append(f"+{fit} industry: {ind}" + (" (drug development, weaker fit)" if ind == "Biotechnology" else ""))

    try:
        sold = float(f.get("amount_sold") or 0)
    except ValueError:
        sold = 0
    if 250_000 <= sold <= 25_000_000:
        cash, note = 20, "seed or Series A-sized raise: has budget, still building"
    elif sold > 25_000_000:
        cash, note = 10, "large raise; may build in-house"
    elif sold > 0:
        cash, note = 8, "small raise"
    else:
        cash, note = 5, "amount not disclosed yet"
    why.append(f"+{cash} " + (f"raised ${sold:,.0f} ({note})" if sold else note))

    days = (date.today() - date.fromisoformat(f["file_date"])).days
    fresh = 15 if days <= 45 else 10 if days <= 120 else 5
    why.append(f"+{fresh} filed {days} days ago")

    named = 10 if NAME_FIT.search(f["company"]) else 0
    why.append(f"+{named} " + ("name points to a health or care business" if named else "name doesn't say health"))

    big = 40 if LARGE_ORG.search(f["company"]) else 0
    if big:
        why.append(f"−{big} part of a large established organization, not a startup")

    f["score"] = max(0, new + fit + cash + fresh + named - big)
    f["why"] = why


def run_funded(days, terms):
    contact = get_settings().get("sec_contact", "").strip()
    end = date.today()
    start = end - timedelta(days=days)
    found = {}
    try:
        for i, term in enumerate(terms):
            with lock:
                fjob.update(stage=f'Searching filings for "{term}"', done=i, total=len(terms))
            for offset in range(0, 1000, 100):
                try:
                    hits = efts_page(term, start, end, offset)
                except Exception as e:
                    with lock:
                        fjob["errors"].append(f"{term}: {e}")
                    break
                for h in hits:
                    s = h["_source"]
                    cik = (s.get("ciks") or [""])[0]
                    name = re.sub(r"\s*\(CIK \d+\)\s*$", "", (s.get("display_names") or [""])[0]).strip()
                    if not cik or FUND_NAME.search(name):
                        continue
                    prev = found.get(cik)
                    if prev and prev["file_date"] >= (s.get("file_date") or ""):
                        if term not in prev["terms"]:
                            prev["terms"].append(term)
                        continue
                    terms_hit = prev["terms"] if prev else []
                    if term not in terms_hit:
                        terms_hit.append(term)
                    found[cik] = {"id": f"sec-{int(cik)}", "kind": "funded", "company": name, "cik": cik,
                                  "adsh": s.get("adsh"), "file_date": s.get("file_date"),
                                  "location": ", ".join(s.get("biz_locations") or []), "terms": terms_hit,
                                  "edgar_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=D"}
                if len(hits) < 100:
                    break
                time.sleep(.2)

        todo = sorted(found.values(), key=lambda f: f["file_date"] or "", reverse=True)[:FUNDED_MAX_FILINGS]
        kept = []
        for i, f in enumerate(todo):
            with lock:
                fjob.update(stage="Reading filings", done=i, total=len(todo), found=len(kept))
            try:
                d = cached_filing(f["cik"], f["adsh"], contact)
            except Exception as e:
                with lock:
                    fjob["errors"].append(f"{f['company']}: {e}")
                    if len(fjob["errors"]) > 20:
                        raise RuntimeError("too many SEC errors; stopped") from e
                continue
            finally:
                time.sleep(.2)
            # Form D gives the exact year only for companies under five years old
            if not re.fullmatch(r"\d{4}", d.get("year_of_inc") or "") or d.get("industry") not in FUNDED_INDUSTRIES:
                continue
            f.update(company=d.get("entity") or f["company"], year_of_inc=d["year_of_inc"], industry=d["industry"],
                     entity_type=d.get("entity_type"), amount_sold=d.get("amount_sold"),
                     offering_total=d.get("offering_total"), first_sale=d.get("first_sale"),
                     people=d.get("people", []), filing_url=d.get("filing_url"))
            score_funded(f)
            kept.append(f)
        kept.sort(key=lambda f: -f["score"])
        with lock:
            save_json(FUNDED_FILE, {"funded": kept, "days": days, "terms": terms, "updated": time.time()})
            mark_ran("funded")
            fjob.update(found=len(kept), done=len(todo), total=len(todo))
    except Exception as e:
        with lock:
            fjob["errors"].append(str(e))
    finally:
        with lock:
            fjob.update(running=False, stage="", finished_at=time.time())
        track_new()


def current_funded():
    res = load_json(FUNDED_FILE, {"funded": []})
    for f in res["funded"]:
        score_funded(f)  # "days ago" points move with the calendar
    res["funded"].sort(key=lambda f: -f["score"])
    return res


# ---------- Google Play (merged into the app lists) ----------

PLAY_FILE = os.path.join(DATA, "play.json")
PLAY_GAP = 1.5                  # seconds between Google Play requests
PLAY_APP_MAX_AGE = 7 * 86400    # re-read an app page weekly
PLAY_HEALTH = {"MEDICAL": "Medical", "HEALTH_AND_FITNESS": "Health & Fitness"}
_play_last = 0.0


def play_get(url):
    global _play_last
    wait = _play_last + PLAY_GAP - time.time()
    if wait > 0:
        time.sleep(wait)
    _play_last = time.time()
    text, _ = fetch(url, limit=3_000_000)
    return text


def play_search_ids(term):
    text = play_get("https://play.google.com/store/search?" + urllib.parse.urlencode(
        {"q": term, "c": "apps", "hl": "en", "gl": "US"}))
    return list(dict.fromkeys(re.findall(r"/store/apps/details\?id=([\w.]+)", text)))[:30]


def play_date(s):
    try:
        return datetime.strptime(s.strip(), "%b %d, %Y").replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, AttributeError):
        return None


def play_app(pkg):
    """One Google Play app page -> the same shape as an App Store app (plus developer, website, email)."""
    text = play_get(f"https://play.google.com/store/apps/details?id={pkg}&hl=en&gl=US")
    info = {}
    for m in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', text, re.S):
        try:
            j = json.loads(m)
        except ValueError:
            continue
        if j.get("@type") == "SoftwareApplication" or "applicationCategory" in j:
            info = j
    rating = info.get("aggregateRating") or {}
    author = info.get("author") or {}
    icon = re.search(r'<meta property="og:image" content="([^"]+)"', text)
    email = next((e for e in re.findall(r"mailto:([\w.+-]+@[\w-]+\.[\w.-]+)", text)), "")
    dev = re.search(r'href="/store/apps/dev(?:eloper)?\?id=([^"&]+)', text)
    return {
        "id": f"gp:{pkg}", "pkg": pkg, "store": "google", "name": html.unescape(info.get("name") or pkg),
        "rating": round(float(rating.get("ratingValue") or 0), 2), "count": int(float(rating.get("ratingCount") or 0)),
        "genre": PLAY_HEALTH.get(info.get("applicationCategory"), info.get("applicationCategory")),
        "health": info.get("applicationCategory") in PLAY_HEALTH,
        "released": None, "updated": play_date(next(iter(re.findall(r"Updated on</div><div[^>]*>([^<]+)", text)), "")),
        "url": f"https://play.google.com/store/apps/details?id={pkg}", "icon": icon.group(1) if icon else "",
        "developer": html.unescape(author.get("name") or ""), "website": author.get("url") or "",
        "email": email, "dev_id": urllib.parse.unquote_plus(dev.group(1)) if dev else "",
        "description": html.unescape(info.get("description") or "")[:600], "at": time.time(),
    }


def play_into(companies, terms, report):
    """Search Google Play for each term and merge its health apps into `companies` (keyed like App Store ones)."""
    store = load_json(PLAY_FILE, {"apps": {}, "search": {}})
    by_name = {norm_name(c["company"]): cid for cid, c in companies.items()}
    failures = 0
    for i, term in enumerate(terms):
        report(f"Google Play: {term}", i)
        try:
            hit = store["search"].get(term)
            if not hit or time.time() - hit["at"] > TERM_CACHE_SECONDS:
                hit = store["search"][term] = {"at": time.time(), "ids": play_search_ids(term)}
            for pkg in hit["ids"]:
                app = store["apps"].get(pkg)
                if not app or time.time() - app["at"] > PLAY_APP_MAX_AGE:
                    app = store["apps"][pkg] = play_app(pkg)
                if not app["health"] or not app["developer"]:
                    continue
                key = norm_name(app["developer"])
                cid = by_name.get(key) or f"gp-{re.sub(r'[^a-z0-9]+', '-', key or app['dev_id'].lower())[:60]}"
                by_name[key] = cid
                c = companies.setdefault(cid, {"id": cid, "company": app["developer"], "website": app["website"],
                                               "developer_url": f"https://play.google.com/store/apps/developer?id={urllib.parse.quote_plus(app['dev_id'])}",
                                               "apps": {}, "terms": []})
                if not c.get("website") and app["website"]:
                    c["website"] = app["website"]
                if term not in c["terms"]:
                    c["terms"].append(term)
                c["apps"][app["id"]] = {k: v for k, v in app.items() if k not in ("at", "health", "pkg")}
            failures = 0
        except Exception as e:
            failures += 1
            report(None, None, f"Google Play {term}: {e}")
            if failures >= 3:
                report(None, None, "Google Play stopped answering; skipped the rest this run")
                break
        save_json(PLAY_FILE, store)


# ---------- new pharmacies & clinics (NPI registry) ----------

PROVIDERS_FILE = os.path.join(DATA, "providers.json")
NPI_QUERIES = ["Pharmacy", "Clinic/Center"]
NPI_EVERY = 24 * 3600
CHAINS = re.compile(r"\b(cvs|walgreens|rite ?aid|wal-?mart|kroger|publix|safeway|albertsons|costco|sam'?s club|h-?e-?b|"
                    r"meijer|hy-?vee|giant eagle|wegmans|amazon|express scripts|optum|accredo|alto pharmacy|capsule|"
                    r"minuteclinic|carbon health|one medical|cityblock|oak street|centerwell|vca|banfield|davita|"
                    r"fresenius|concentra|u\.?s\.? physical therapy|athletico|select physical)\b", re.I)
STORE_NO = re.compile(r"#\s*\d+|\bstore\s*\d+|\b\d{4,}\b")
PROVIDER_FIT = re.compile(r"wellness|weight|hormone|peptide|longevity|med ?spa|\biv\b|infusion|tele|virtual|\brx\b|"
                          r"aesthetic|men'?s health|women'?s health|functional|integrative|anti-?aging|vitality|"
                          r"optimal|concierge|direct primary|compound", re.I)
DECIDER = re.compile(r"owner|ceo|chief exec|president|founder|managing|member|principal|director|partner", re.I)


def npi_page(query, prefix, skip):
    url = "https://npiregistry.cms.hhs.gov/api/?" + urllib.parse.urlencode({
        "version": "2.1", "enumeration_type": "NPI-2", "taxonomy_description": query,
        "postal_code": prefix + "*", "limit": 200, "skip": skip})
    for attempt in range(3):
        try:
            text, _ = fetch(url, timeout=30)
            return json.loads(text).get("results", [])
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def npi_scan(query, prefix, keep, cutoff, report, count):
    """All organizations matching `query` in ZIP codes starting with `prefix`; splits the prefix when
    it holds more than the registry's 1,200-result cap."""
    rows = []
    for skip in range(0, 1200, 200):
        page = npi_page(query, prefix, skip)
        count[0] += 1
        rows += page
        if len(page) < 200:
            break
        time.sleep(.15)
    else:
        if len(prefix) < 5:
            for d in "0123456789":
                npi_scan(query, prefix + d, keep, cutoff, report, count)
            return
    for x in rows:
        b = x.get("basic", {})
        if b.get("status") != "A" or (b.get("enumeration_date") or "") < cutoff:
            continue
        keep[x["number"]] = x


def provider_record(x):
    b = x["basic"]
    prim = next((t for t in x.get("taxonomies", []) if t.get("primary")), (x.get("taxonomies") or [{}])[0])
    loc = next((a for a in x.get("addresses", []) if a.get("address_purpose") == "LOCATION"), {})
    dba = next((o.get("organization_name") for o in x.get("other_names", []) if o.get("organization_name")), "")
    official = " ".join(filter(None, [b.get("authorized_official_first_name"), b.get("authorized_official_last_name")]))
    return {
        "id": f"npi-{x['number']}", "kind": "provider", "npi": str(x["number"]), "company": b.get("organization_name") or dba,
        "dba": dba, "type": prim.get("desc") or "", "registered": b.get("enumeration_date"),
        "city": (loc.get("city") or "").title(), "state": loc.get("state") or "", "zip": (loc.get("postal_code") or "")[:5],
        "address": (loc.get("address_1") or "").title(), "phone": loc.get("telephone_number") or "",
        "official": official.title(), "official_title": (b.get("authorized_official_title_or_position") or "").title(),
        "official_phone": b.get("authorized_official_telephone_number") or "",
        "npi_url": f"https://npiregistry.cms.hhs.gov/provider-view/{x['number']}",
    }


def score_provider(p):
    why = []
    days = (date.today() - date.fromisoformat(p["registered"])).days
    new = 35 if days <= 30 else 30 if days <= 60 else 25 if days <= 90 else 18 if days <= 180 else 10
    why.append(f"+{new} registered {days} days ago")
    t = p["type"].lower()
    fit = (30 if "compounding" in t else 26 if any(k in t for k in ("specialty", "mail order", "infusion")) else
           18 if t.startswith("pharmacy") else 22 if "medical specialty" in t else 18 if "primary care" in t or "multi" in t
           else 12)
    why.append(f"+{fit} type: {p['type']}")
    name = f"{p['company']} {p['dba']}"
    rel = 20 if PROVIDER_FIT.search(name) else 0
    why.append(f"+{rel} " + ("name points to wellness, weight loss, hormones, IV or compounding" if rel else "name doesn't point to a Connect niche"))
    decider = 15 if DECIDER.search(p["official_title"]) else 8 if p["official"] else 0
    if p["official"]:
        why.append(f"+{decider} decision-maker named: {p['official']}" + (f", {p['official_title']}" if p["official_title"] else ""))
    chain = 40 if CHAINS.search(name) or LARGE_ORG.search(name) else 30 if STORE_NO.search(name) else 0
    if chain:
        why.append(f"−{chain} looks like part of a chain")
    p["score"] = max(0, new + fit + rel + decider - chain)
    p["why"] = why


# ---------- Medicare star ratings (CMS Care Compare) ----------

MEDICARE_FILE = os.path.join(DATA, "medicare.json")
MEDICARE_EVERY = 7 * 24 * 3600
CMS_SETS = [  # dataset, kind, name field, rating field, Care Compare page
    ("6jpm-sxkc", "Home health agency", "provider_name", "quality_of_patient_care_star_rating", "home-health"),
    ("23ew-n7w9", "Dialysis center", "facility_name", "five_star", "dialysis-facility"),
]
CARE_CHAINS = re.compile(r"\b(amedisys|lhc|enhabit|kindred|centerwell|bayada|interim|gentiva|aveanna|encompass|"
                         r"davita|fresenius|u\.?s\.? renal|american renal|dialysis clinic|satellite|innovative renal|"
                         r"atlantic dialysis|northwest kidney)\b", re.I)


def cms_rows(dataset):
    out, offset = [], 0
    while True:
        text, _ = fetch(f"https://data.cms.gov/provider-data/api/1/datastore/query/{dataset}/0?limit=1000&offset={offset}",
                        timeout=60, limit=40_000_000)
        rows = json.loads(text).get("results", [])
        out += rows
        if len(rows) < 1000:
            return out
        offset += 1000
        time.sleep(.3)


def score_medicare(p, max_rating):
    why = []
    r = p["rating"]
    need = 12 + 23 * (max_rating - r) / max(max_rating - 1, .5)
    need = max(0, min(35, need))
    why.append(f"+{need:.0f} Medicare rates it {r:g}★")
    years = (date.today() - date.fromisoformat(p["certified"])).days / 365.25 if p.get("certified") else None
    new = 25 if years is not None and years <= 3 else 15 if years is not None and years <= 6 else 8 if years is not None and years <= 10 else 3
    if years is not None:
        why.append(f"+{new} Medicare-certified {years:.0f} yrs ago")
    own = (p.get("ownership") or "").lower()
    o = (10 if own.strip(" -") == "" else 4 if "government" in own else 12 if "non" in own and "profit" in own else 20)
    why.append(f"+{o} ownership: {p.get('ownership').strip(' -') or 'not given'}")
    fit = 20 if p["type"] == "Home health agency" else 10
    why.append(f"+{fit} {'home health: patients managed at home, a good fit for an app' if fit == 20 else 'dialysis: weaker fit'}")
    chain = 40 if CARE_CHAINS.search(p["company"]) or LARGE_ORG.search(p["company"]) else 0
    if chain:
        why.append(f"−{chain} part of a large chain")
    p["score"] = max(0, round(need + new + o + fit - chain))
    p["why"] = why


def fetch_medicare(max_rating, report):
    out = []
    for i, (ds, kind, name_f, rate_f, page) in enumerate(CMS_SETS):
        report(f"Medicare: {kind.lower()}s", i, total=len(CMS_SETS))
        for row in cms_rows(ds):
            try:
                r = float(row.get(rate_f) or "x")
            except ValueError:
                continue
            if r > max_rating:
                continue
            ccn = row.get("cms_certification_number_ccn") or row.get("ccn") or ""
            p = {"id": f"cms-{ccn}", "kind": "medicare", "type": kind, "company": (row.get(name_f) or "").title(),
                 "rating": r, "ccn": ccn, "city": (row.get("citytown") or row.get("city_town") or "").title(),
                 "state": row.get("state") or "", "zip": (row.get("zip_code") or "")[:5],
                 "address": (row.get("address") or row.get("address_line_1") or "").title(),
                 "phone": row.get("telephone_number") or "",
                 "ownership": (row.get("type_of_ownership") or row.get("profit_or_nonprofit") or row.get("ownership_type") or "").title(),
                 "certified": (row.get("certification_date") or "")[:10] or None,
                 "compare_url": f"https://www.medicare.gov/care-compare/details/{page}/{ccn}"}
            if p["certified"] and not re.fullmatch(r"\d{4}-\d\d-\d\d", p["certified"]):
                try:
                    p["certified"] = datetime.strptime(p["certified"], "%m/%d/%Y").date().isoformat()
                except ValueError:
                    p["certified"] = None
            score_medicare(p, max_rating)
            out.append(p)
    out.sort(key=lambda p: -p["score"])
    return out


# ---------- the providers job (NPI + Medicare) ----------

PROVIDER_TYPES_KEEP = re.compile(r"^(pharmacy|clinic/center)", re.I)


def run_providers(days, which, force=False):
    """which: subset of {"npi", "medicare"}. Each part only runs when due unless forced."""
    try:
        if "npi" in which and (force or time.time() - last_ran("npi") >= NPI_EVERY - 3600):
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            keep, count = {}, [0]
            prefixes = [f"{i:02d}" for i in range(100)]
            total = len(prefixes) * len(NPI_QUERIES)
            for qi, query in enumerate(NPI_QUERIES):
                for pi, prefix in enumerate(prefixes):
                    with lock:
                        pjob.update(stage=f"Registry: {query.lower()} · ZIP {prefix}xxx", done=qi * len(prefixes) + pi,
                                    total=total, found=len(keep))
                    try:
                        npi_scan(query, prefix, keep, cutoff, None, count)
                    except Exception as e:
                        with lock:
                            pjob["errors"].append(f"{query} {prefix}: {e}")
            recs = [provider_record(x) for x in keep.values()]
            recs = [r for r in recs if PROVIDER_TYPES_KEEP.search(r["type"])]
            for r in recs:
                score_provider(r)
            recs.sort(key=lambda r: -r["score"])
            with lock:
                save_json(PROVIDERS_FILE, {"providers": recs, "days": days, "updated": time.time(), "requests": count[0]})
                mark_ran("npi")
        if "medicare" in which and (force or time.time() - last_ran("medicare") >= MEDICARE_EVERY - 3600):
            max_rating = float((load_json(RESULTS_FILE, {}).get("filters") or {}).get("max_rating", 3))

            def report(stage, i, total=None, error=None):
                with lock:
                    if stage:
                        pjob.update(stage=stage, done=i, total=total or pjob["total"])
            recs = fetch_medicare(max_rating, report)
            with lock:
                save_json(MEDICARE_FILE, {"medicare": recs, "max_rating": max_rating, "updated": time.time()})
                mark_ran("medicare")
    except Exception as e:
        with lock:
            pjob["errors"].append(str(e))
    finally:
        with lock:
            prov = load_json(PROVIDERS_FILE, {}).get("providers", [])
            med = load_json(MEDICARE_FILE, {}).get("medicare", [])
            pjob.update(running=False, stage="", found=len(prov) + len(med), finished_at=time.time())
        track_new()


def current_providers():
    res = load_json(PROVIDERS_FILE, {"providers": []})
    for p in res["providers"]:
        score_provider(p)  # "days ago" points move with the calendar
    res["providers"].sort(key=lambda p: -p["score"])
    return res


def current_medicare():
    return load_json(MEDICARE_FILE, {"medicare": []})

# ---------- auto-update ----------

def mark_ran(kind):
    st = load_json(AUTO_FILE, {})
    st[f"{kind}_last"] = time.time()
    save_json(AUTO_FILE, st)


def last_ran(kind):
    """When a search last finished; before auto.json existed, the results file's own timestamp."""
    st = load_json(AUTO_FILE, {})
    if f"{kind}_last" in st:
        return st[f"{kind}_last"]
    return load_json(RESULTS_FILE if kind == "apps" else FUNDED_FILE, {}).get("updated", 0)


def nice_name(n):
    return n.title() if n and n.isupper() else (n or "")


def lead_from(src, it):
    """The same pipeline record the page makes with "Add to pipeline"."""
    kind = {"funded": "funded", "prov": "provider", "cms": "medicare"}.get(src, "app")
    item = dict(it, kind=kind, source=src)
    if isinstance(item.get("apps"), list):
        item["apps"] = [{k: v for k, v in a.items() if k != "description"} for a in item["apps"]]
    lead = {"id": it["id"], "company": it["company"], "source": src, "kind": kind, "score": it["score"],
            "why": it["why"], "item": item, "website": it.get("website") or "", "status": "New",
            "notes": f"Added automatically on {date.today():%b %d, %Y} (fit {it['score']}).", "contacts": [],
            "added": time.time(), "updated": time.time(), "auto": True}
    if kind == "funded":
        lead.update(founded=it.get("year_of_inc"), raised=it.get("amount_sold"), category="health", app="",
                    contacts=[{"name": nice_name(p["name"]), "title": ", ".join(p["roles"]), "source": "SEC Form D"}
                              for p in it.get("people", [])])
    elif kind in ("provider", "medicare"):
        who = it.get("official")
        lead.update(category="health", app="", contacts=[{"name": who, "title": it.get("official_title", ""),
                    "phone": it.get("official_phone") or it.get("phone", ""), "source": "NPI registry"}] if who else [])
    else:
        w = it["worst"]
        lead.update(app=w.get("name"), rating=w.get("rating"), ratings=it.get("total_ratings"), app_url=w.get("url"),
                    icon=w.get("icon"), category=w.get("genre"), first_released=it.get("first_released"),
                    last_updated=it.get("last_updated"))
    return lead


def track_new():
    """Record companies seen for the first time; optionally add strong new ones to the pipeline."""
    with lock:
        seen = load_json(SEEN_FILE, None)
        baseline = seen is None
        seen = seen or {}
        now = time.time()
        fresh = []
        sources = (("low", current_prospects()[0]), ("new", new_apps(24)), ("funded", current_funded()["funded"]),
                   ("prov", current_providers()["providers"]), ("cms", current_medicare()["medicare"]))
        for src, items in sources:
            # a source's first results are its starting point, not news (the same as the very first run)
            first = baseline or not any(k.startswith(src + ":") for k in seen)
            for it in items:
                key = f"{src}:{it['id']}"
                if key not in seen:
                    seen[key] = 0 if first else now
                    if not first and not it.get("solo"):
                        fresh.append((src, it))
        save_json(SEEN_FILE, seen)
        added = 0
        threshold = int(get_settings().get("auto_add_min") or 0)
        if threshold and fresh:
            leads = load_json(LEADS_FILE, {})
            for src, it in fresh:
                if it["score"] >= threshold and it["id"] not in leads:
                    leads[it["id"]] = lead_from(src, it)
                    added += 1
            if added:
                save_json(LEADS_FILE, leads)
        auto["version"] += 1
        if not baseline:
            auto.update(last_new=len({it["id"] for _, it in fresh}), last_new_at=now, last_added=added)
            # both searches can finish between two page checks, so the page sums these
            auto["events"] = (auto.get("events", []) + [{"at": now, "new": auto["last_new"], "added": added}])[-20:]


def auto_status():
    s = get_settings()
    every = float(s.get("auto_every_hours") or 6) * 3600
    with lock:
        out = dict(auto, events=list(auto.get("events", [])))
    apps_last, funded_last = last_ran("apps"), last_ran("funded")
    out.update(on=bool(s.get("auto_update", True)), every_hours=every / 3600,
               apps_last=apps_last, funded_last=funded_last,
               apps_next=apps_last + every, funded_next=funded_last + every,
               funded_ready=bool(EMAIL_RE.search(s.get("sec_contact", ""))) or cloud["on"],
               cloud=cloud["on"], synced_at=cloud["synced_at"], sync_error=cloud["error"],
               cloud_errors=load_json(AUTO_FILE, {}).get("last_errors") if cloud["on"] else None)
    return out


def scheduler():
    """Every minute: start whichever search is due. Both reuse the last terms and filters."""
    time.sleep(30)
    while True:
        try:
            s = get_settings()
            if s.get("auto_update", True) and not cloud["on"]:
                every = float(s.get("auto_every_hours") or 6) * 3600
                now = time.time()
                with lock:
                    start_apps = not job["running"] and now - last_ran("apps") >= every
                    if start_apps:
                        res = load_json(RESULTS_FILE, {})
                        f = res.get("filters") or {}
                        terms = res.get("terms") or DEFAULT_TERMS
                        job.update(running=True, done=0, total=len(terms), term="", found=0, errors=[], finished_at=None)
                if start_apps:
                    # a fresh look each time, but no more often than the cache allows
                    threading.Thread(target=run_search, daemon=True, args=(
                        terms, float(f.get("max_rating", 3)), int(f.get("min_reviews", 5)), f.get("country", "us"),
                        True, min(every * .9, TERM_CACHE_SECONDS))).start()
                with lock:
                    start_funded = (not fjob["running"] and bool(EMAIL_RE.search(s.get("sec_contact", "")))
                                    and now - last_ran("funded") >= every)
                    if start_funded:
                        fres = load_json(FUNDED_FILE, {})
                        fterms = fres.get("terms") or FUNDED_TERMS
                        fjob.update(running=True, done=0, total=len(fterms), stage="Starting", found=0, errors=[],
                                    finished_at=None)
                if start_funded:
                    threading.Thread(target=run_funded, args=(int(fres.get("days") or 180), fterms),
                                     daemon=True).start()
                with lock:
                    start_prov = not pjob["running"] and (now - last_ran("npi") >= NPI_EVERY
                                                          or now - last_ran("medicare") >= MEDICARE_EVERY)
                    if start_prov:
                        pjob.update(running=True, done=0, total=1, stage="Starting", found=0, errors=[], finished_at=None)
                if start_prov:
                    threading.Thread(target=run_providers, daemon=True,
                                     args=(int(s.get("provider_days") or 90), {"npi", "medicare"})).start()
            auto["error"] = ""
        except Exception as e:
            auto["error"] = str(e)
        time.sleep(60)

# ---------- cloud mode (GitHub) ----------

cloud = {"on": False, "synced_at": None, "error": "", "due": 0}
sync_lock = threading.Lock()
SYNC_EVERY = 15 * 60
CLOUD_GRACE = 15 * 60   # the cloud runs every 2 h; run a search if it's due within this


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, timeout=180,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check and r.returncode:
        raise RuntimeError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def cloud_enabled():
    try:
        return os.path.isdir(os.path.join(HERE, ".git")) and bool(git("remote", check=False))
    except (OSError, subprocess.SubprocessError):
        return False


def move_secret_out_of_settings():
    """Older versions kept the SEC email in settings.json, which is uploaded; move it to local.json."""
    shared = load_json(SETTINGS_FILE, {})
    if any(k in shared for k in LOCAL_KEYS):
        local = load_json(LOCAL_FILE, {})
        for k in LOCAL_KEYS:
            if shared.get(k):
                local.setdefault(k, shared[k])
            shared.pop(k, None)
        save_json(LOCAL_FILE, local)
        save_json(SETTINGS_FILE, shared)


def combine_leads(a, b):
    """Union of two pipelines by id; where both have a lead, the one changed last wins (deletions included)."""
    out = dict(a)
    for k, v in b.items():
        if k not in out or (v.get("updated") or 0) > (out[k].get("updated") or 0):
            out[k] = v
    return out


def sync(prefer_remote=True, message="Local changes"):
    """Commit this copy's data, merge GitHub's, combine the two pipelines, push.

    prefer_remote: the local app lets the cloud's search results win; the cloud runner keeps its own.
    Returns how many companies arrived as new (for the page's pop-up)."""
    with sync_lock:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        remote_exists = bool(git("ls-remote", "--heads", "origin", branch))
        if remote_exists:
            git("fetch", "-q", "origin", branch)
        arrived = 0
        with lock:
            mine = load_json(LEADS_FILE, {})
            seen_before = set(load_json(SEEN_FILE, {}))
            git("add", "-A", "data")
            if git("status", "--porcelain", "data"):
                git("commit", "-q", "-m", message)
            if remote_exists:
                before = git("rev-parse", "HEAD")
                r = subprocess.run(["git", "merge", "-q", "--no-edit", "-X", "theirs" if prefer_remote else "ours",
                                    f"origin/{branch}"], cwd=HERE, capture_output=True, text=True, timeout=180,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode:
                    git("merge", "--abort", check=False)
                    raise RuntimeError(f"couldn't combine with GitHub's copy: {(r.stderr or r.stdout).strip()[:200]}")
                merged = load_json(LEADS_FILE, {})
                combined = combine_leads(merged, mine)
                if combined != merged:
                    save_json(LEADS_FILE, combined)
                    git("add", LEADS_FILE)
                    git("commit", "-q", "-m", "Combine pipeline changes")
                if git("rev-parse", "HEAD") != before:
                    seen = load_json(SEEN_FILE, {})
                    arrived = len({k.split(":", 1)[1] for k, v in seen.items() if v and k not in seen_before})
                    auto["version"] += 1
                    if arrived:
                        auto["events"] = (auto.get("events", []) + [{"at": time.time(), "new": arrived, "added": 0}])[-20:]
        if not remote_exists or git("rev-list", "--count", f"origin/{branch}..HEAD") != "0":
            git("push", "-q", "-u", "origin", f"HEAD:{branch}")
        cloud.update(synced_at=time.time(), error="")
        return arrived


def sync_soon(delay=20):
    """Pipeline or settings changed: sync shortly, so several quick edits go up together."""
    if cloud["on"]:
        cloud["due"] = min(cloud["due"] or float("inf"), time.time() + delay)


def sync_loop():
    cloud["due"] = time.time()
    while True:
        if time.time() >= cloud["due"]:
            cloud["due"] = time.time() + SYNC_EVERY
            try:
                sync()
            except Exception as e:
                cloud["error"] = str(e)
                print("Sync problem:", e)
        time.sleep(5)


def update_once():
    """What GitHub Actions runs: whichever search is due (all with --force; --only=apps,funded,npi,medicare
    limits it), then push. Errors are printed, kept in auto.json and named in the commit message."""
    s = get_settings()
    force = "--force" in sys.argv
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), "")
    only = {w.strip() for w in only.split(",") if w.strip()}
    wanted = lambda part: not only or part in only
    if not s.get("auto_update", True) and not force:
        print("Auto-update is off in Settings; nothing to do.")
        return
    if not os.path.exists(SEEN_FILE):
        track_new()
    every = float(s.get("auto_every_hours") or 6) * 3600
    ran = []
    if wanted("apps") and (force or time.time() - last_ran("apps") >= every - CLOUD_GRACE):
        res = load_json(RESULTS_FILE, {})
        f = res.get("filters") or {}
        terms = res.get("terms") or DEFAULT_TERMS
        print(f"App Store search: {len(terms)} terms")
        job.update(running=True, done=0, total=len(terms), term="", found=0, errors=[], finished_at=None)
        run_search(terms, float(f.get("max_rating", 3)), int(f.get("min_reviews", 5)), f.get("country", "us"), True, 0)
        ran.append(f"App Store: {job['found']} low-rated" + (f", {len(job['errors'])} errors" if job["errors"] else ""))
    if wanted("funded") and EMAIL_RE.search(s.get("sec_contact", "")) and (
            force or time.time() - last_ran("funded") >= every - CLOUD_GRACE):
        fres = load_json(FUNDED_FILE, {})
        fterms = fres.get("terms") or FUNDED_TERMS
        print(f"SEC scan: {len(fterms)} name words, last {fres.get('days') or 180} days")
        fjob.update(running=True, done=0, total=len(fterms), stage="Starting", found=0, errors=[], finished_at=None)
        run_funded(int(fres.get("days") or 180), fterms)
        ran.append(f"SEC: {fjob['found']} startups" + (f", {len(fjob['errors'])} errors" if fjob["errors"] else ""))
    elif not EMAIL_RE.search(s.get("sec_contact", "")):
        print("No SEC_CONTACT secret set, so the SEC scan was skipped.")
    due_npi = wanted("npi") and (force or time.time() - last_ran("npi") >= NPI_EVERY - CLOUD_GRACE)
    due_med = wanted("medicare") and (force or time.time() - last_ran("medicare") >= MEDICARE_EVERY - CLOUD_GRACE)
    if due_npi or due_med:
        print("Providers:", ", ".join(k for k, d in (("NPI registry", due_npi), ("Medicare", due_med)) if d))
        pjob.update(running=True, done=0, total=1, stage="Starting", found=0, errors=[], finished_at=None)
        run_providers(int(s.get("provider_days") or 90), {k for k, d in (("npi", due_npi), ("medicare", due_med)) if d},
                      force)
        ran.append(f"providers: {pjob['found']}" + (f", {len(pjob['errors'])} errors" if pjob["errors"] else ""))
    if not ran:
        print("Nothing due yet.")
        return
    errors = [f"App Store/Play: {e}" for e in job["errors"]] + [f"SEC: {e}" for e in fjob["errors"]] + \
             [f"Providers: {e}" for e in pjob["errors"]]
    for e in errors[:30]:
        print("Problem:", e)
    st = load_json(AUTO_FILE, {})
    st["last_errors"] = {"at": time.time(), "errors": errors[:30]}
    save_json(AUTO_FILE, st)
    new = auto.get("events", [])
    summary = "; ".join(ran) + f"; {sum(e['new'] for e in new)} new, {sum(e['added'] for e in new)} auto-added"
    if errors:
        summary += f" | first problem: {errors[0][:150]}"
    print(summary)
    if cloud_enabled():
        sync(prefer_remote=False, message=f"Cloud update: {summary}")
        print("Pushed.")

# ---------- AI (Pebble) through Claude Code (owner's Claude plan) or Codex (owner's ChatGPT plan); no API keys ----------
# The same bridge as Atlas Terminal's ASK: Claude Code in print mode, in an empty folder outside the project (Claude Code
# reads CLAUDE.md files from parent folders), nothing saved, MCP servers off. Research may use WebSearch and WebFetch
# (LinkedIn, Google Maps and Yelp included, at the user's request); everything else gets no tools at all.
# Only works on this computer: GitHub's cloud runs have no Claude sign-in. Every call uses the owner's Claude usage.
AI_FILE = os.path.join(DATA, "ai.json")
CHATS_FILE = os.path.join(DATA, "chats.json")  # saved Pebble conversations: {id: {title, tab, messages, created, updated}}
CLAUDE_EXE = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")
AI_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "StoneDockProspector", "ai_sandbox")
AI_LOCK = threading.Lock()
AI_STATE = {}  # per assistant: {"auth": ..., "at": when it was checked}
AI_MODELS = ("sonnet", "opus", "haiku")

STONEDOCK = """StoneDock (formerly OnePATH Health) sells StoneDock Connect: a white-label health data and AI engine that runs \
inside a partner's own app, under the partner's brand. It is built on FHIR, the data standard Epic and other EHRs use; it is \
HIPAA-ready with patient consent on every request and a full audit trail; its AI reads lab PDFs and turns them into health \
scores, insights and coaching; it already runs a medication workflow (payment, prescription, EHR sync, refills); patients never \
leave the partner's app; partners get SDKs and a no-account sandbox demo at onepathhealth.com. Price: $8 per member per month. \
Live client: MyWellness App, a telehealth app for peptides and GLP-1s supplied by the Wellvi pharmacy. Best-fit customers: \
telehealth companies, online and compounding pharmacies, GLP-1/weight-loss, hormone, peptide, longevity, wellness and \
coaching companies, and clinics or care organizations that want a patient app without building the health-data and AI stack."""


def claude_exe():
    import shutil
    return CLAUDE_EXE if os.path.exists(CLAUDE_EXE) else shutil.which("claude")


CODEX_HOME = os.path.join(os.path.expanduser("~"), ".codex")
CODEX_FALLBACK = [("gpt-6.1-sol", "GPT-6.1-Sol"), ("gpt-6-astra", "GPT-6-Astra"), ("gpt-6-luna", "GPT-6-Luna")]


def codex_exe():
    """The native Codex CLI (npm installs a .cmd wrapper; the real program sits in the package's vendor folder)."""
    import glob
    import shutil
    npm = os.path.join(os.environ.get("APPDATA") or "", "npm", "node_modules", "@openai", "codex")
    hits = glob.glob(os.path.join(npm, "node_modules", "@openai", "codex-win32-*", "vendor", "*", "bin", "codex.exe"))
    return hits[0] if hits else shutil.which("codex.exe") or shutil.which("codex")


def codex_models():
    """The models the owner's Codex offers, from Codex's own model list (falls back to a known few)."""
    try:
        with open(os.path.join(CODEX_HOME, "models_cache.json"), encoding="utf-8") as f:
            got = [(m["slug"], m.get("display_name") or m["slug"]) for m in json.load(f).get("models", [])
                   if m.get("visibility", "list") == "list" and m.get("slug")]
        return got or CODEX_FALLBACK
    except Exception:
        return CODEX_FALLBACK


def ai_models():
    claude = [{"id": "sonnet", "label": "Claude Sonnet", "provider": "claude"},
              {"id": "opus", "label": "Claude Opus", "provider": "claude"},
              {"id": "haiku", "label": "Claude Haiku", "provider": "claude"}]
    return claude + [{"id": slug, "label": name, "provider": "codex"} for slug, name in codex_models()]


def ai_model():
    """The chosen model and which assistant runs it: Claude Code (Claude plan) or Codex (ChatGPT plan)."""
    m = get_settings().get("ai_model") or "sonnet"
    if m in AI_MODELS:
        return m, "claude"
    if m in {slug for slug, _ in codex_models()} or m.startswith(("gpt", "codex", "o3", "o4")):
        return m, "codex"
    return "sonnet", "claude"


def provider_status(provider):
    """Is that assistant installed and signed in? (checked at most once a minute each)"""
    exe = codex_exe() if provider == "codex" else claude_exe()
    if not exe:
        return {"installed": False, "loggedIn": False}
    st = AI_STATE.setdefault(provider, {"auth": None, "at": 0})
    if st["auth"] is None or time.time() - st["at"] > 60:
        try:
            if provider == "codex":
                r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=30, encoding="utf-8",
                                   errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                out = ((r.stdout or "") + (r.stderr or "")).lower()
                st["auth"] = {"loggedIn": r.returncode == 0 and "logged in" in out and "not logged" not in out}
            else:
                r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=30, encoding="utf-8",
                                   errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                st["auth"] = json.loads(r.stdout or "{}")
        except Exception as e:
            st["auth"] = {"loggedIn": False, "error": str(e)[:120]}
        st["at"] = time.time()
    return {"installed": True, "loggedIn": bool((st["auth"] or {}).get("loggedIn"))}


def ai_status():
    model, provider = ai_model()
    return {**provider_status(provider), "busy": AI_LOCK.locked(), "model": model, "provider": provider,
            "models": ai_models(), "providers": {p: provider_status(p) for p in ("claude", "codex")}}


def ask_claude(system, prompt, web=False, turns=None):
    """One request to Pebble's chosen model. Returns {"ok", "text", "seconds", "error"}."""
    model, provider = ai_model()
    if provider == "codex":
        return ask_codex(system, prompt, model, web)
    exe = claude_exe()
    if not exe:
        return {"ok": False, "error": "Claude Code isn't installed on this computer."}
    if not AI_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "Pebble is still working on the last request. Try again when it's done."}
    try:
        os.makedirs(AI_DIR, exist_ok=True)
        tools = "WebSearch,WebFetch" if web else ""
        cmd = [exe, "-p", "--output-format", "json", "--model", model,
               "--tools", tools, "--strict-mcp-config", "--disallowedTools", "mcp__*", "--no-session-persistence",
               "--max-turns", str(turns or (18 if web else 3)), "--system-prompt", system]
        if web:  # pre-approved, so print mode never stops to ask
            cmd[cmd.index("--tools") + 2:cmd.index("--tools") + 2] = ["--allowedTools", tools]
        t0 = time.time()
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=420 if web else 180, cwd=AI_DIR,
                           encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        secs = round(time.time() - t0, 1)
        try:
            out = json.loads(r.stdout or "{}")
        except ValueError:
            out = {}
        text = out.get("result") or ""
        if r.returncode != 0 or out.get("is_error") or not text:
            msg = text or (r.stderr or r.stdout or "").strip()[:400] or f"exit code {r.returncode}"
            if re.search(r"log ?in|not logged|authenticat|/login", msg, re.I):
                AI_STATE.pop("claude", None)
                msg = "Claude Code isn't signed in on this computer. Run `claude` in a terminal and sign in."
            elif re.search(r"usage limit|rate limit|limit reached", msg, re.I):
                msg = "Your Claude usage limit is reached for now. Pebble can try again once it resets."
            return {"ok": False, "error": msg, "seconds": secs}
        return {"ok": True, "text": text, "seconds": secs, "turns": out.get("num_turns")}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Pebble took longer than {7 if web else 3} minutes, so it stopped."}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    finally:
        AI_LOCK.release()


def ask_codex(system, prompt, model, web=False):
    """The same request through Codex (OpenAI) on the owner's ChatGPT plan: `codex exec`, read-only, in the same empty
    folder, nothing saved, the owner's Codex config ignored (no plugins, hooks or rules). Codex has no separate system
    prompt in exec mode, so the instructions go first in the message. Research gets Codex's live web search."""
    exe = codex_exe()
    if not exe:
        return {"ok": False, "error": "Codex isn't installed on this computer."}
    if not AI_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "Pebble is still working on the last request. Try again when it's done."}
    out_file = os.path.join(AI_DIR, f"codex-{os.getpid()}-{int(time.time() * 1000)}.txt")
    try:
        os.makedirs(AI_DIR, exist_ok=True)
        full = (f"<instructions>\n{system}\n\nWork from your own knowledge{' and web search' if web else ''} only: "
                f"don't run shell commands or read or change files.\n</instructions>\n\n{prompt}")
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
               "-s", "read-only", "-m", model, "-c", 'web_search="%s"' % ("live" if web else "disabled"),
               "-C", AI_DIR, "-o", out_file, "--color", "never", "-"]
        t0 = time.time()
        r = subprocess.run(cmd, input=full, capture_output=True, text=True, timeout=420 if web else 180, cwd=AI_DIR,
                           encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        secs = round(time.time() - t0, 1)
        text = ""
        if os.path.exists(out_file):
            with open(out_file, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        if r.returncode != 0 or not text:
            log = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
            msg = next((l for l in reversed(log.splitlines()) if re.search(r"error|fail|limit|denied|unauthor", l, re.I)),
                       log[-400:]) or f"exit code {r.returncode}"
            if re.search(r"not logged|log ?in|unauthor|401|authenticat", msg, re.I):
                AI_STATE.pop("codex", None)
                msg = "Codex isn't signed in on this computer. Run `codex login` in a terminal and sign in."
            elif re.search(r"usage limit|rate limit|limit reached|quota|429", msg, re.I):
                msg = "Your ChatGPT/Codex usage limit is reached for now. Pick a Claude model, or try again once it resets."
            return {"ok": False, "error": msg[:400], "seconds": secs}
        return {"ok": True, "text": text, "seconds": secs}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Pebble took longer than {7 if web else 3} minutes, so it stopped."}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    finally:
        try:
            os.remove(out_file)
        except OSError:
            pass
        AI_LOCK.release()


def json_from(text):
    """The first JSON object in a reply (models sometimes wrap it in a sentence or code fence)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("Pebble's answer wasn't in the expected format")
    return json.loads(m.group(0))


def all_items():
    """(source, item) for everything the app has found, newest lists first; pipeline snapshots fill the gaps."""
    out = [("low", p) for p in current_prospects()[0]] + [("new", p) for p in new_apps(36)] + \
          [("funded", p) for p in current_funded()["funded"]] + [("prov", p) for p in current_providers()["providers"]] + \
          [("cms", p) for p in current_medicare()["medicare"]]
    seen = {p["id"] for _, p in out}
    for lead in live_leads().values():
        if lead["id"] not in seen and lead.get("item"):
            out.append((lead.get("source", "low"), lead["item"]))
    return out


def web_item(item_id, r):
    """A company Pebble researched that isn't in any list (added from chat), shaped like the other items."""
    return {"id": item_id, "kind": "web", "company": r.get("company") or "", "website": r.get("website") or "",
            "link": r.get("link") or "", "location": r.get("location") or "", "type": r.get("type") or "",
            "why_found": r.get("why_found") or "", "score": int(r.get("fit_score") or 0),
            "why": [r["fit_score_reason"]] if r.get("fit_score_reason") else []}


def find_item(item_id):
    hit = next(((s, p) for s, p in all_items() if p["id"] == item_id), None)
    if hit:
        return hit
    r = ai_store()["research"].get(item_id) if item_id.startswith("web-") else None
    return ("web", web_item(item_id, r)) if r else (None, None)


def find_by_name(name):
    """A listed company whose name matches (ignoring Inc/LLC and punctuation), so a chat request reuses it."""
    want = clean_key(name)
    return next(((s, p) for s, p in all_items() if want and clean_key(p.get("company") or "") == want
                 or want and clean_key(p.get("dba") or "") == want), (None, None))


def clean_key(name):
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    n = re.sub(r"\b(inc|llc|l l c|corp|corporation|co|ltd|pllc|pc|lp|the|company)\b", " ", n)
    return " ".join(n.split())


def describe(src, p, full=True):
    """What the app knows about one company, as plain lines for a prompt."""
    lines = [f"Company: {p.get('company')}"]
    if p.get("dba"):
        lines.append(f"Also known as: {p['dba']}")
    if src in ("low", "new") or p.get("apps"):
        if p.get("website"):
            lines.append(f"Website: {p['website']}")
        for a in (p.get("apps") or [])[:6 if full else 2]:
            lines.append(f"App: {a.get('name')} ({'Google Play' if a.get('store') == 'google' else 'App Store'}), "
                         f"{a.get('rating') or 'no'} stars from {a.get('count', 0)} ratings, {a.get('genre') or ''}, "
                         f"launched {(a.get('released') or '?')[:10]}, updated {(a.get('updated') or '?')[:10]}")
            if full and a.get("description"):
                lines.append(f"  Description: {a['description'][:300]}")
    if src == "funded":
        lines.append(f"SEC Form D: founded {p.get('year_of_inc')}, industry {p.get('industry')}, raised "
                     f"${float(p.get('amount_sold') or 0):,.0f}, filed {p.get('file_date')}, based in {p.get('location')}")
        if p.get("people"):
            lines.append("People in the filing: " + "; ".join(f"{x['name'].title()} ({', '.join(x['roles'])})" for x in p["people"]))
    if src == "web":
        lines += [f"{k}: {p[f]}" for k, f in (("Website", "website"), ("Link the user gave", "link"),
                                               ("Location", "location"), ("Type", "type"),
                                               ("Why Pebble suggested it", "why_found")) if p.get(f)]
        lines.append("Not in the Prospector's lists: the user asked Pebble to add it.")
    if src in ("prov", "cms"):
        lines.append(f"Type: {p.get('type')}; located in {p.get('city')}, {p.get('state')}; phone {p.get('phone')}")
        if src == "prov":
            lines.append(f"Registered in the NPI registry {p.get('registered')}; authorized official: {p.get('official')}, {p.get('official_title')}")
        else:
            lines.append(f"Medicare rating {p.get('rating')} stars; ownership {p.get('ownership')}; certified {p.get('certified')}")
    lines.append(f"Prospector fit score: {p.get('score')} ({'; '.join(p.get('why') or [])})")
    lead = live_leads().get(p["id"])
    if full and lead:
        if lead.get("contacts"):
            lines.append("Contacts saved: " + "; ".join(f"{c.get('name') or c.get('email')} {c.get('title') or ''}".strip()
                                                       for c in lead["contacts"]))
        if lead.get("notes"):
            lines.append(f"Notes: {lead['notes'][:400]}")
    return "\n".join(lines)


def ai_store():
    return load_json(AI_FILE, {"research": {}, "rank": {}})


def ai_save(update):
    with lock:
        store = ai_store()
        for k, v in update.items():
            store.setdefault(k, {}).update(v)
        save_json(AI_FILE, store)
    sync_soon()


RESEARCH_SYSTEM = f"""You are Pebble, the research assistant inside StoneDock Prospector, a tool StoneDock's sales team uses \
to find companies that could buy StoneDock Connect.

{STONEDOCK}

Research the company you're given using web search. Always search for: the company's own website; its founders and \
leaders on LinkedIn (for example `site:linkedin.com "Company" founder OR CEO`); its reviews and rating on Google Maps and \
Yelp; recent news, funding and press releases. You may open the company's website, news articles, LinkedIn, Google Maps \
and Yelp pages; if a page is blocked, behind a sign-in, or empty, use what the search results show instead. Never invent \
people, emails, numbers or quotes: everything must come from a source you saw, and say "not found" when you can't find \
something. Keep the research to what a salesperson needs before a first call.

Answer with ONLY a JSON object, no other text:
{{"summary": "2-3 sentences: what the company does and who it serves",
 "website": "the company's main website, or empty",
 "fit": "2-3 specific sentences on why they would (or wouldn't) need StoneDock Connect",
 "pain_points": ["3-5 short, specific likely pain points Connect could solve"],
 "decision_makers": [{{"name": "...", "title": "...", "source": "URL where you saw it"}}],
 "ratings": "what you found about their Google, Yelp or app-store ratings and reviews, with numbers, or empty",
 "news": [{{"title": "...", "date": "YYYY-MM or YYYY-MM-DD if known", "url": "..."}}],
 "questions": ["4-6 discovery-call questions tailored to them"],
 "fit_adjust": an integer from -20 to 20 to adjust the Prospector's fit score after what you learned,
 "fit_reason": "one sentence explaining fit_adjust",
 "sources": ["URLs you used"]}}"""

# for a company that isn't in the lists (the user asked for it in chat), there's no automatic score to adjust
RESEARCH_WEB_EXTRA = """

This company is NOT in the Prospector's lists, so it has no automatic fit score: set fit_adjust to 0 and add these fields \
to the JSON object:
 "company": "the company's proper name as it presents itself",
 "location": "city, state/country of its headquarters, or empty",
 "type": "a few words, e.g. GLP-1 telehealth, compounding pharmacy, med spa",
 "fit_score": an integer 0-100 for how good a StoneDock Connect customer it is (80+ only for a clear, reachable fit),
 "fit_score_reason": "one sentence explaining fit_score"
If you can't find the company at all, say so in summary, set fit_score to 0 and leave the rest empty."""


def ai_research_any(name, link="", why=""):
    """Research a company the user named in chat (or Pebble found on the web). A company already in the lists is
    researched as itself; anything else is kept as a 'web-' entry that only lives in the Research tab."""
    name = (name or "").strip()[:150]
    link = (link or "").strip()[:500]
    if not name and not link:
        return {"ok": False, "error": "Tell Pebble a company name or a link."}
    src, p = find_by_name(name)
    if p:
        return {**ai_research(p["id"]), "id": p["id"], "source": src, "existing": True}
    key = clean_key(name) or clean_key(re.sub(r"^https?://(www\.)?", "", link).split("/")[0].replace(".", " "))
    item_id = "web-" + re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:60]
    old = ai_store()["research"].get(item_id) or {}
    item = web_item(item_id, {**old, "company": old.get("company") or name or link, "link": link or old.get("link"),
                               "why_found": why or old.get("why_found")})
    return {**ai_research(item_id, ("web", item)), "id": item_id, "source": "web"}


def ai_research(item_id, found=None):
    src, p = found or find_item(item_id)
    if not p:
        return {"ok": False, "error": "That company isn't in the Prospector's lists any more."}
    system = RESEARCH_SYSTEM + (RESEARCH_WEB_EXTRA if src == "web" else "")
    r = ask_claude(system, "Research this company:\n\n" + describe(src, p), web=True)
    if not r["ok"]:
        return r
    try:
        data = json_from(r["text"])
    except ValueError as e:
        return {"ok": False, "error": str(e), "raw": r["text"][:500]}
    data["fit_adjust"] = max(-20, min(20, int(data.get("fit_adjust") or 0))) if src != "web" else 0
    if src == "web":
        data["fit_score"] = max(0, min(100, int(data.get("fit_score") or 0)))
        data.update(link=p.get("link") or "", why_found=p.get("why_found") or "",
                    company=data.get("company") or p.get("company"))
    # source and model too, so the Research tab can say where the company came from even after it leaves the lists
    data.update(at=time.time(), seconds=r["seconds"], source=src, model=ai_model()[0])
    data.setdefault("company", p.get("company"))
    ai_save({"research": {item_id: data}})
    return {"ok": True, "research": data}


EMAIL_SYSTEM = f"""You are Pebble, who writes first-touch sales emails for StoneDock.

{STONEDOCK}

Write one short, warm, specific cold email (under 170 words) from the sender to the contact, inviting them to a 20-minute \
demo of StoneDock Connect. Open with something specific to their company from the research (not flattery), connect one or \
two of their likely pain points to what Connect does, and end with a clear, low-pressure ask. Plain text, no markdown, no \
emojis, no placeholders left unfilled; if you don't know the contact's first name, greet with "Hi there". Never invent facts.
Answer with ONLY a JSON object: {{"subject": "...", "body": "..."}}"""


def ai_email(item_id, contact):
    src, p = find_item(item_id)
    if not p:
        return {"ok": False, "error": "That company isn't in the Prospector's lists any more."}
    s = get_settings()
    research = ai_store()["research"].get(item_id)
    prompt = "Company:\n" + describe(src, p) + "\n\n"
    if research:
        prompt += "Research Pebble did earlier:\n" + json.dumps({k: research.get(k) for k in (
            "summary", "fit", "pain_points", "ratings", "news")}, ensure_ascii=False) + "\n\n"
    prompt += (f"Contact: {contact.get('name') or 'unknown name'}, {contact.get('title') or 'title unknown'}\n"
               f"Sender: {s.get('sender') or 'the StoneDock team'}, StoneDock\n\n"
               f"The team's usual template, for tone only:\n{s.get('template', '')[:1200]}")
    r = ask_claude(EMAIL_SYSTEM, prompt)
    if not r["ok"]:
        return r
    try:
        data = json_from(r["text"])
    except ValueError as e:
        return {"ok": False, "error": str(e), "raw": r["text"][:500]}
    return {"ok": True, "subject": data.get("subject", ""), "body": data.get("body", ""), "seconds": r["seconds"]}


SOURCE_NAMES = {"low": "Low-rated apps", "new": "New apps", "funded": "Newly funded startups",
                "prov": "New pharmacies and clinics", "cms": "Medicare-rated providers", "pipe": "Pipeline"}


def snapshot(tab, limit=120):
    """The rows Pebble can see for a chat question: the best-scoring ones of the tab (or every tab's top rows)."""
    items = all_items()
    leads = live_leads()
    if tab == "pipe":
        rows = [(s, p) for s, p in items if p["id"] in leads]
    elif tab in SOURCE_NAMES:
        rows = [(s, p) for s, p in items if s == tab and not p.get("solo")]
    else:
        rows = [(s, p) for s, p in items if not p.get("solo")]
    rows.sort(key=lambda sp: -(sp[1].get("score") or 0))
    rows = rows[:limit]
    lines = []
    for s, p in rows:
        lead = leads.get(p["id"])
        extra = (f" | pipeline: {lead.get('status')}" if lead else "")
        lines.append(f"[{SOURCE_NAMES.get(s, s)}] " + describe(s, p, full=False).replace("\n", " | ") + extra)
    return rows, "\n".join(lines)


CHAT_SYSTEM = f"""You are Pebble, the friendly AI helper inside StoneDock Prospector, the tool StoneDock's sales team uses \
to find companies that could buy StoneDock Connect.

{STONEDOCK}

You're given the prospects the user is looking at. Answer their question using those rows (and web search, if it's \
available and the question needs it). Be concise and practical: short paragraphs or a short list, plain language. Always \
write company names exactly as they appear in the rows, so the app can link them. Don't invent facts about companies; say \
when the data doesn't show something.

You can do one thing in the app: add companies to the user's Research tab (the app then researches each one in depth and \
saves the brief there). Do it when the user asks you to add, save or research specific companies (by name or link), or to \
find new companies and add them. For "find" requests, use web search (LinkedIn, Google Maps, Yelp, company sites, news) to \
find REAL companies that exist; never invent one, and skip big chains and giant brands. At most 5 companies per answer. \
To add them, write your normal short answer (say which ones you're adding and why), then put this as the very LAST line:
ADD_TO_RESEARCH: [{{"name": "Company name", "link": "its website, LinkedIn, Google Maps or Yelp URL if you have one, else empty", "why": "one short sentence"}}]
Only include that line when the user wants companies added or researched. You can't change anything else in the app; for \
anything else, suggest what the user can do."""

# questions that ask Pebble to add or find companies get web search even when the box isn't ticked
ADD_ASK = re.compile(r"research tab|\badd\b.*\bresearch|\b(find|look for|search for|discover)\b.*\b(compan|clinic|pharmac|"
                     r"startup|practice|provider|business|spa|telehealth|app)", re.I)


def ai_chat(question, tab, history, web):
    rows, table = snapshot(tab)
    convo = "".join(f"{'User' if m.get('role') == 'user' else 'Pebble'}: {m.get('text', '')[:1500]}\n"
                    for m in (history or [])[-8:])
    prompt = (f"The user is on the '{SOURCE_NAMES.get(tab, 'all prospects')}' view. Prospects ({len(rows)} best by fit score):\n"
              f"{table}\n\nConversation so far:\n{convo}\nUser: {question}")
    web = web or bool(ADD_ASK.search(question))
    r = ask_claude(CHAT_SYSTEM, prompt, web=web, turns=14 if web else 3)
    if not r["ok"]:
        return r
    text, add = r["text"], []
    m = re.search(r"^\s*ADD_TO_RESEARCH:\s*(\[.*\])\s*$", text, re.M | re.S)
    if m:
        try:
            add = [{"name": str(x.get("name") or "")[:150], "link": str(x.get("link") or "")[:500],
                    "why": str(x.get("why") or "")[:300]} for x in json.loads(m.group(1)) if isinstance(x, dict)]
            add = [x for x in add if x["name"] or x["link"]][:5]
        except ValueError:
            add = []
        text = text[:m.start()].rstrip()
    low = text.lower()
    mentions = []
    for s, p in rows:
        name = p.get("company") or ""
        if len(name) > 3 and name.lower() in low and p["id"] not in {m["id"] for m in mentions}:
            mentions.append({"id": p["id"], "name": name, "source": s})
    return {"ok": True, "text": text, "mentions": mentions, "add": add, "seconds": r["seconds"]}


RANK_SYSTEM = f"""You are Pebble, who double-checks the StoneDock Prospector's automatic fit scores.

{STONEDOCK}

For each company, read everything given (names, app descriptions, ratings, funding, type, location) and decide how much \
the automatic score over- or under-rates it as a StoneDock Connect customer. Look for things keyword rules miss: a giant \
brand hiding behind a small name, a company that isn't really in healthcare, a perfect-fit telehealth or compounding \
business, a product that already has its own health-data stack, and so on.
Answer with ONLY a JSON object: {{"items": [{{"id": "...", "adjust": integer -20 to 20, "reason": "one short sentence"}}]}}"""


def ai_rank(tab, count=25):
    rows, _ = snapshot(tab, count)
    if not rows:
        return {"ok": False, "error": "There's nothing to rank in this view yet."}
    prompt = "Companies:\n\n" + "\n\n".join(f"id: {p['id']}\n" + describe(s, p) for s, p in rows)
    r = ask_claude(RANK_SYSTEM, prompt, turns=3)
    if not r["ok"]:
        return r
    try:
        items = json_from(r["text"]).get("items") or []
    except ValueError as e:
        return {"ok": False, "error": str(e), "raw": r["text"][:500]}
    known = {p["id"] for _, p in rows}
    out = {}
    for it in items:
        if it.get("id") in known:
            out[it["id"]] = {"adjust": max(-20, min(20, int(it.get("adjust") or 0))), "reason": it.get("reason", ""),
                             "at": time.time()}
    ai_save({"rank": out})
    return {"ok": True, "rank": out, "seconds": r["seconds"], "count": len(out)}

# ---------- CSV ----------

def leads_csv():
    leads = live_leads()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Company", "Found by", "Status", "Score", "App", "Rating", "Ratings", "Founded", "Raised", "Website",
                "Contact name", "Title", "Email", "Phone", "LinkedIn", "Contact source", "Notes", "App Store"])
    source = {"low": "Low-rated app", "new": "New app", "funded": "Newly funded", "prov": "New provider",
              "cms": "Medicare-rated"}
    for lead in sorted(leads.values(), key=lambda l: -(l.get("score") or 0)):
        base = [lead.get("company"), source.get(lead.get("source"), "Low-rated app"), lead.get("status"),
                lead.get("score"), lead.get("app"), lead.get("rating"), lead.get("ratings"),
                lead.get("founded"), lead.get("raised"), lead.get("website")]
        tail = [lead.get("notes", ""), lead.get("app_url")]
        contacts = lead.get("contacts") or [{}]
        for c in contacts:
            w.writerow(base + [c.get("name", ""), c.get("title", ""), c.get("email", ""), c.get("phone", ""),
                               c.get("linkedin", ""), c.get("source", "")] + tail)
    return out.getvalue()


# ---------- server ----------

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else (
            body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html")
        if path == "/api/state":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            months = int((q.get("months") or ["24"])[0])
            prospects, filters, updated = current_prospects()
            funded = current_funded()
            newer = new_apps(months)
            provs, med = current_providers(), current_medicare()
            seen = load_json(SEEN_FILE, {})
            for src, items in (("low", prospects), ("new", newer), ("funded", funded["funded"]),
                               ("prov", provs["providers"]), ("cms", med["medicare"])):
                for it in items:
                    it["first_seen"] = seen.get(f"{src}:{it['id']}", 0)
            with lock:
                j, fj, pj = dict(job), dict(fjob), dict(pjob)
            s = get_settings()
            return self.send(200, {"prospects": prospects, "filters": filters, "updated": updated, "job": j,
                                   "ai": ai_store(),
                                   "new_apps": newer, "funded": funded["funded"], "auto": auto_status(),
                                   "funded_days": funded.get("days"), "funded_updated": funded.get("updated"),
                                   "fjob": fj, "pjob": pj, "providers": provs["providers"], "providers_days": provs.get("days"),
                                   "medicare": med["medicare"], "leads": live_leads(), "settings": s,
                                   "default_terms": DEFAULT_TERMS, "funded_terms": FUNDED_TERMS})
        if path == "/pebble.js":
            with open(os.path.join(HERE, "pebble.js"), "rb") as f:
                return self.send(200, f.read(), "text/javascript")
        # the browser-tab icon: the StoneDock stones (SVG, with PNGs for browsers and bookmarks that want one)
        icons = {"/favicon.svg": ("favicon.svg", "image/svg+xml"), "/favicon-32.png": ("favicon-32.png", "image/png"),
                 "/favicon.ico": ("favicon-32.png", "image/png"), "/favicon-180.png": ("favicon-180.png", "image/png")}
        if path in icons:
            name, ctype = icons[path]
            with open(os.path.join(HERE, name), "rb") as f:
                return self.send(200, f.read(), ctype)
        if path == "/api/ai/status":
            return self.send(200, ai_status())
        if path == "/api/chats":
            chats = sorted(load_json(CHATS_FILE, {}).values(), key=lambda c: -(c.get("updated") or 0))
            return self.send(200, {"chats": chats})
        if path == "/api/job":
            with lock:
                return self.send(200, {"job": dict(job), "fjob": dict(fjob), "pjob": dict(pjob), "auto": auto_status()})
        if path == "/api/export.csv":
            name = f"stonedock-prospects-{datetime.now():%Y-%m-%d}.csv"
            return self.send(200, "﻿" + leads_csv(), "text/csv",
                             {"Content-Disposition": f'attachment; filename="{name}"'})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            b = self.body()
        except ValueError:
            return self.send(400, {"error": "bad json"})
        try:
            return self.route_post(path, b)
        except Exception as e:
            return self.send(502, {"error": str(e)})

    def route_post(self, path, b):
        if path == "/api/search":
            terms = [t.strip() for t in b.get("terms", []) if t.strip()][:60]
            if not terms:
                return self.send(400, {"error": "add at least one search term"})
            with lock:
                if job["running"]:
                    return self.send(409, {"error": "a search is already running"})
                job.update(running=True, done=0, total=len(terms), term="", found=0, errors=[], finished_at=None)
            args = (terms, float(b.get("max_rating", 3)), int(b.get("min_reviews", 5)),
                    (b.get("country") or "us").lower()[:2], bool(b.get("merge", True)))
            threading.Thread(target=run_search, args=args, daemon=True).start()
            return self.send(200, {"ok": True})
        if path == "/api/funded":
            terms = [t.strip() for t in b.get("terms", []) if t.strip()][:30] or FUNDED_TERMS
            if not EMAIL_RE.search(get_settings().get("sec_contact", "")):
                return self.send(400, {"error": "add a contact email in Settings first; SEC requires one to read filings"})
            with lock:
                if fjob["running"]:
                    return self.send(409, {"error": "a funding scan is already running"})
                fjob.update(running=True, done=0, total=len(terms), stage="Starting", found=0, errors=[], finished_at=None)
            days = max(14, min(int(b.get("days", 180)), 730))
            threading.Thread(target=run_funded, args=(days, terms), daemon=True).start()
            return self.send(200, {"ok": True})
        m = re.fullmatch(r"/api/chats/([\w-]{1,40})(/delete)?", path)
        if m:
            with lock:
                chats = load_json(CHATS_FILE, {})
                if m.group(2):
                    chats.pop(m.group(1), None)
                else:
                    old = chats.get(m.group(1), {"id": m.group(1), "created": time.time()})
                    msgs = [{k: x.get(k) for k in ("role", "text", "err", "mentions")} for x in (b.get("messages") or [])][-200:]
                    old.update(title=(b.get("title") or "New chat")[:80], tab=b.get("tab", ""), messages=msgs,
                               updated=time.time())
                    chats[m.group(1)] = old
                save_json(CHATS_FILE, chats)
            sync_soon()
            return self.send(200, {"ok": True})
        if path == "/api/ai/research":
            if b.get("id"):
                return self.send(200, ai_research(b["id"]))
            return self.send(200, ai_research_any(b.get("name", ""), b.get("link", ""), b.get("why", "")))
        if path == "/api/ai/email":
            return self.send(200, ai_email(b.get("id", ""), b.get("contact") or {}))
        if path == "/api/ai/chat":
            q = (b.get("question") or "").strip()
            if not q:
                return self.send(400, {"error": "ask Pebble something first"})
            return self.send(200, ai_chat(q[:2000], b.get("tab", ""), b.get("history") or [], bool(b.get("web"))))
        if path == "/api/ai/rank":
            return self.send(200, ai_rank(b.get("tab", "low")))
        if path == "/api/providers":
            which = {w for w in b.get("which", ["npi", "medicare"]) if w in ("npi", "medicare")}
            if not which:
                return self.send(400, {"error": "pick at least one source"})
            days = max(7, min(int(b.get("days") or get_settings().get("provider_days") or 90), 365))
            with lock:
                if pjob["running"]:
                    return self.send(409, {"error": "already running"})
                pjob.update(running=True, done=0, total=1, stage="Starting", found=0, errors=[], finished_at=None)
                s = get_settings()
                s["provider_days"] = days
                save_settings(s)
            threading.Thread(target=run_providers, args=(days, which, True), daemon=True).start()
            return self.send(200, {"ok": True})
        if path == "/api/clear":
            with lock:
                save_json(RESULTS_FILE, {"prospects": {}, "filters": {}, "updated": time.time()})
            return self.send(200, {"ok": True})
        if path == "/api/settings":
            with lock:
                s = get_settings()
                # null resets a setting to its default
                s.update({k: DEFAULT_SETTINGS[k] if v is None else v for k, v in b.items() if k in DEFAULT_SETTINGS})
                save_settings(s)
            sync_soon()
            return self.send(200, s)
        m = re.fullmatch(r"/api/leads/([\w-]+)(/delete)?", path)
        if m:
            with lock:
                leads = load_json(LEADS_FILE, {})
                if m.group(2):
                    leads[m.group(1)] = {"id": m.group(1), "deleted": True, "updated": time.time()}
                else:
                    lead = leads.get(m.group(1))
                    if not lead or lead.get("deleted"):
                        lead = {"id": m.group(1), "status": "New", "contacts": [], "notes": "", "added": time.time()}
                    lead.update(b)
                    lead["updated"] = time.time()
                    leads[m.group(1)] = lead
                save_json(LEADS_FILE, leads)
            sync_soon()
            return self.send(200, {} if m.group(2) else leads[m.group(1)])
        if path == "/api/enrich":
            website = (b.get("website") or "").strip()
            if not website and b.get("app_url"):
                website = website_from_store(b["app_url"])
            if not website:
                return self.send(404, {"error": "no website found; type one in and try again"})
            if not re.match(r"^(https?://)?[\w.-]+\.[a-z]{2,}", website, re.I):
                return self.send(400, {"error": "that doesn't look like a website"})
            return self.send(200, enrich_site(website))
        if path == "/api/sec/search":
            return self.send(200, {"hits": sec_search(b.get("name", ""))})
        if path == "/api/sec/filing":
            contact = get_settings().get("sec_contact", "").strip()
            if not EMAIL_RE.search(contact):
                return self.send(400, {"error": "add a contact email in Settings first; SEC requires one for filing details"})
            return self.send(200, sec_filing(b["cik"], b["adsh"], contact))
        self.send(404, {"error": "not found"})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    os.makedirs(SEC_CACHE, exist_ok=True)
    move_secret_out_of_settings()
    if "--update-once" in sys.argv:
        return update_once()
    if not os.path.exists(SEEN_FILE):
        track_new()  # first run: everything already found counts as old
    cloud["on"] = cloud_enabled()
    threading.Thread(target=scheduler, daemon=True).start()
    if cloud["on"]:
        threading.Thread(target=sync_loop, daemon=True).start()
    srv = Server(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"StoneDock Prospector running at {url}  (Ctrl+C to stop)"
          + ("\nCloud mode: syncing with GitHub" if cloud["on"] else ""))
    if "--no-open" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
