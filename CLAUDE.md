# StoneDock Prospector

Prospecting tool for StoneDock Connect: finds health companies whose apps are rated 3 stars or lower. **Full context (StoneDock background, decisions, sources, scoring, API, preferences) lives in the Obsidian note:**

`C:\Users\andre\OneDrive\Documents\Obsidian Vault\StoneDock\StoneDock - Project Context.md`

Read it before making changes.

**Standing rule from the user (2026-10-26):** every time you add to or change StoneDock work, update that note in the same turn: a dated Change log entry (what changed and why) plus every section the change affects. Don't wait to be asked.

## Quick facts

- `python server.py` → http://localhost:8770. Standard library only, no API keys. Restart after editing `server.py`; reload the page after editing `index.html`.
- LinkedIn is never scraped; the UI only builds search links.
- iTunes allows ~20 calls/min; keep the 3 s gap and the per-term cache.
