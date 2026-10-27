# StoneDock Prospector

Finds companies that may need StoneDock Connect, ranks them by how well they fit, and keeps a pipeline of contacts and outreach. No API keys, no installs: just Python.

It finds prospects three ways, each with its own tab:

- **Low-rated apps:** health, wellness, telehealth and pharmacy companies whose own app is rated **3 stars or lower** on the App Store.
- **New apps:** health companies whose first App Store app launched recently (6–36 months, your choice), at any rating.
- **Newly funded:** health startups **under 5 years old** that recently filed an SEC Form D, meaning they raised private money.

## Run it

```
python server.py
```

Your browser opens at http://localhost:8770. Leave the window running while you use the tool; press Ctrl+C to stop it.

## It keeps itself up to date

While `python server.py` is running, the tool re-runs both searches on its own (every 6 hours by default) using your last search terms and filters. The chip in the header shows when the next check is.

- Companies that show up for the first time get a blue **NEW** badge for a week, and each tab shows how many are new. Tick **New this week** to see only those.
- If the page is open when new companies arrive, a pop-up tells you how many.
- In **Settings** you can change how often it checks (every hour to once a day), turn auto-update off, or have strong new companies (fit 70, 80 or 90+) **added to the pipeline automatically**, with the leaders from their SEC filing as contacts. Solo developers are never added.

### Even when your computer is off (cloud mode)

This folder is connected to the private GitHub repo **andrewcapone714/stonedock-prospector**. GitHub Actions (`.github/workflows/update.yml`) wakes up every 2 hours, runs whichever search is due (by **Check every**, so at most every 2 hours in practice) and saves the results back to the repo, NEW marks and auto-added leads included.

- When you start the Prospector, and every 15 minutes while it runs, it downloads what the cloud found; your pipeline and settings changes are uploaded about 20 seconds after you make them. If both sides changed the pipeline, the two are combined (the most recent edit to a lead wins; deleted leads stay deleted). For search results, the cloud's copy wins.
- In cloud mode the Prospector doesn't run scheduled searches itself; the header chip says **Cloud auto-update**. Manual searches still work.
- Your SEC email is never uploaded: it lives in `data/local.json` on this computer and in the repo secret `SEC_CONTACT` for the cloud.
- To run the cloud update now: repo → **Actions** → **Update prospects** → **Run workflow** (runs both searches).
- Free for private repos: about 2,000 Actions minutes a month are included; this uses roughly 500–900.

Without cloud mode, nothing updates while the server is stopped; it catches up a minute after you start it again.

## How to use it

1. **Search the App Store** (left panel). The search terms are already filled in. Choose **Run search**. Each term takes about 3 seconds the first time (Apple's limit) and is instant for the next 24 hours. This fills both **Low-rated apps** and **New apps**.
2. **Scan SEC filings** (second panel). Pick how far back to look and choose **Scan SEC filings**. The first scan takes a few minutes (it reads up to 400 filings); later scans reuse saved filings. Needs your email in **Settings**, because SEC requires one.
3. **Pick companies.** The **Fit** score (0–100) is explained when you hover it or open a company. Big hospital systems, insurers and dialysis chains are marked down, and so are App Store sellers that look like one person rather than a company; **Hide solo developers** hides those.
4. **Research one.** Click a row:
   - **LinkedIn buttons** open LinkedIn's own search for the founder/CEO, CTO, partnerships and so on, in your browser. Nothing is scraped.
   - **Find emails** checks the company website's home, about, team and contact pages for public emails and LinkedIn links.
   - For newly funded startups, the panel shows the amount raised and the **leaders named in the filing**, each with a LinkedIn search button.
   - For app companies, **SEC Form D** shows whether the company has raised money; **Details** lists officers and the amount raised.
5. **Add to pipeline.** Track status (New → Contacted → Demo booked …), contacts and notes. Adding a newly funded startup adds the leaders from its filing as contacts.
6. **Draft outreach.** Fills in the template (edit it in Settings) for a contact; copy it or open it in your email app. Nothing is sent automatically.
7. **Export CSV** for the team spreadsheet. It says which tab each company came from.

## Files

- `server.py` — the data server
- `index.html` — the page
- `data/` — your results, pipeline, funding scan, settings, and the record of when each company first appeared (created on first run)
