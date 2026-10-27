#!/usr/bin/env python3
"""StoneDock Prospector.

Finds likely prospects for StoneDock Connect, three ways:
  Low-rated apps  - health, wellness, telehealth and pharmacy companies whose own
                    apps are rated 3 stars or lower on the App Store.
  New apps        - health companies whose first App Store app launched recently.
  Newly funded    - health startups incorporated in the last five years that filed
                    an SEC Form D (raised private money) recently.
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
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

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
                    "auto_update": True, "auto_every_hours": 6, "auto_add_min": 0}

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

def fetch(url, ua=BROWSER_UA, timeout=15, limit=2_000_000):
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
            text, _ = fetch(url, ua="StoneDockProspector/1.0", limit=30_000_000)
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
                "id": str(a["trackId"]), "name": a.get("trackName"),
                "rating": round(a.get("averageUserRating") or 0, 2),
                "count": a.get("userRatingCount") or 0,
                "genre": HEALTH_GENRES.get(a.get("primaryGenreId"), a.get("primaryGenreName")),
                "released": a.get("releaseDate"), "updated": a.get("currentVersionReleaseDate"),
                "url": (a.get("trackViewUrl") or "").split("?")[0], "icon": a.get("artworkUrl60"),
                "price": a.get("formattedPrice"), "description": (a.get("description") or "")[:600],
            }
        with lock:
            job["found"] = sum(1 for c in companies.values() if qualifying(c, max_rating, min_reviews))
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
        job.update(running=False, done=len(terms), term="", found=len(prospects), finished_at=time.time())
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
    text, _ = fetch(f"https://efts.sec.gov/LATEST/search-index?q={q}&forms=D", ua="StoneDockProspector/1.0")
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
    text, _ = fetch("https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(params),
                    ua="StoneDockProspector/1.0")
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
    kind = "funded" if src == "funded" else "app"
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
        sources = (("low", current_prospects()[0]), ("new", new_apps(24)), ("funded", current_funded()["funded"]))
        for src, items in sources:
            for it in items:
                key = f"{src}:{it['id']}"
                if key not in seen:
                    seen[key] = 0 if baseline else now
                    if not baseline and not it.get("solo"):
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
               cloud=cloud["on"], synced_at=cloud["synced_at"], sync_error=cloud["error"])
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
    """What GitHub Actions runs: whichever search is due (or both with --force), then push."""
    s = get_settings()
    force = "--force" in sys.argv
    if not s.get("auto_update", True) and not force:
        print("Auto-update is off in Settings; nothing to do.")
        return
    if not os.path.exists(SEEN_FILE):
        track_new()
    every = float(s.get("auto_every_hours") or 6) * 3600
    ran = []
    if force or time.time() - last_ran("apps") >= every - CLOUD_GRACE:
        res = load_json(RESULTS_FILE, {})
        f = res.get("filters") or {}
        terms = res.get("terms") or DEFAULT_TERMS
        print(f"App Store search: {len(terms)} terms")
        job.update(running=True, done=0, total=len(terms), term="", found=0, errors=[], finished_at=None)
        run_search(terms, float(f.get("max_rating", 3)), int(f.get("min_reviews", 5)), f.get("country", "us"), True, 0)
        ran.append(f"App Store: {job['found']} low-rated" + (f", errors: {job['errors']}" if job["errors"] else ""))
    if EMAIL_RE.search(s.get("sec_contact", "")) and (force or time.time() - last_ran("funded") >= every - CLOUD_GRACE):
        fres = load_json(FUNDED_FILE, {})
        fterms = fres.get("terms") or FUNDED_TERMS
        print(f"SEC scan: {len(fterms)} name words, last {fres.get('days') or 180} days")
        fjob.update(running=True, done=0, total=len(fterms), stage="Starting", found=0, errors=[], finished_at=None)
        run_funded(int(fres.get("days") or 180), fterms)
        ran.append(f"SEC: {fjob['found']} startups" + (f", {len(fjob['errors'])} errors" if fjob["errors"] else ""))
    elif not EMAIL_RE.search(s.get("sec_contact", "")):
        print("No SEC_CONTACT secret set, so the SEC scan was skipped.")
    if not ran:
        print("Nothing due yet.")
        return
    new = auto.get("events", [])
    summary = "; ".join(ran) + f"; {sum(e['new'] for e in new)} new, {sum(e['added'] for e in new)} auto-added"
    print(summary)
    if cloud_enabled():
        sync(prefer_remote=False, message=f"Cloud update: {summary}")
        print("Pushed.")

# ---------- CSV ----------

def leads_csv():
    leads = live_leads()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Company", "Found by", "Status", "Score", "App", "Rating", "Ratings", "Founded", "Raised", "Website",
                "Contact name", "Title", "Email", "LinkedIn", "Contact source", "Notes", "App Store"])
    source = {"low": "Low-rated app", "new": "New app", "funded": "Newly funded"}
    for lead in sorted(leads.values(), key=lambda l: -(l.get("score") or 0)):
        base = [lead.get("company"), source.get(lead.get("source"), "Low-rated app"), lead.get("status"),
                lead.get("score"), lead.get("app"), lead.get("rating"), lead.get("ratings"),
                lead.get("founded"), lead.get("raised"), lead.get("website")]
        tail = [lead.get("notes", ""), lead.get("app_url")]
        contacts = lead.get("contacts") or [{}]
        for c in contacts:
            w.writerow(base + [c.get("name", ""), c.get("title", ""), c.get("email", ""),
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
            seen = load_json(SEEN_FILE, {})
            for src, items in (("low", prospects), ("new", newer), ("funded", funded["funded"])):
                for it in items:
                    it["first_seen"] = seen.get(f"{src}:{it['id']}", 0)
            with lock:
                j, fj = dict(job), dict(fjob)
            s = get_settings()
            return self.send(200, {"prospects": prospects, "filters": filters, "updated": updated, "job": j,
                                   "new_apps": newer, "funded": funded["funded"], "auto": auto_status(),
                                   "funded_days": funded.get("days"), "funded_updated": funded.get("updated"),
                                   "fjob": fj, "leads": live_leads(), "settings": s,
                                   "default_terms": DEFAULT_TERMS, "funded_terms": FUNDED_TERMS})
        if path == "/api/job":
            with lock:
                return self.send(200, {"job": dict(job), "fjob": dict(fjob), "auto": auto_status()})
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
