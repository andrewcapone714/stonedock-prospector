# StoneDock Prospector

Instructions for coding agents (Codex and others; Claude Code reads the matching `CLAUDE.md`).

Prospecting tool for StoneDock Connect (white-label health data + AI API, $8/member/month): finds health companies that may need it (low-rated health apps, new apps, newly funded startups, new pharmacies/clinics, low Medicare ratings) and helps research and contact them. **Full context (StoneDock background, decisions, sources, scoring, API, files, the user's preferences, and a dated change log) lives in the Obsidian note:**

`C:\Users\andre\OneDrive\Documents\Obsidian Vault\StoneDock\StoneDock - Project Context.md`

Read it before making changes.

**Standing rule from the user (2026-10-26):** every time you add to or change StoneDock work, update that note in the same turn: a dated Change log entry (what changed and why, newest at the top) plus every section the change affects (files, sources, API, UI, limits, open ideas). Don't wait to be asked.

## Quick facts

- `python server.py` → http://localhost:8770 (`--no-open` skips opening the browser). Python standard library only, no API keys. Restart the server after editing `server.py`; just reload the page after editing `index.html` or `pebble.js`.
- Files: `server.py` (data sources, scoring, jobs, cloud sync, Pebble AI endpoints), `index.html` (the whole UI), `pebble.js` (Pebble, the animated AI mascot), `data/*.json` (results, pipeline, settings, AI research, chats), `.github/workflows/update.yml` (cloud updates every 2 h on GitHub Actions).
- Check page JavaScript before finishing: extract the `<script>` blocks of `index.html` and run `node --check` on them; `node --check pebble.js`; `python -m py_compile server.py`.
- Every outside request identifies honestly as `StoneDockProspector/1.0` (`TOOL_UA`). Never switch to a browser user agent or work around bot checks. Keep the existing throttles (iTunes 3 s gap + per-term cache, Google Play 1.5 s, SEC requires the contact email in its UA).
- The dashboard only builds LinkedIn search links; it never scrapes LinkedIn. Pebble's web research may open LinkedIn, Google Maps and Yelp pages at the user's explicit request.
- Fit scores live in `score_*` functions in `server.py`; the **Criteria** tab (`CRIT` in `index.html`) is a hand-written copy of those points. Change both together.
- **Pebble's animations are final** (user approved them, 2026-10-27). Don't change `pebble.js` choreography unless asked.
- Pebble's AI runs through Claude Code (`claude.exe`, Claude plan) or Codex (`codex.exe exec`, ChatGPT plan) on this PC only; the model is chosen in Settings or Pebble's status line (`ai_model`).
- Git: repo https://github.com/andrewcapone714/stonedock-prospector (private, branch `main`). This folder has its own git identity ("StoneDock Prospector (local)"). The running server commits and syncs `data/` on its own; commit code changes with a clear message and push. `data/local.json` (the SEC contact email) is git-ignored: never commit it.
- Windows PowerShell 5.1: `Set-Content -Encoding utf8` adds a BOM; write files with Python or an editor instead. Keep LF line endings (`.gitattributes`).
