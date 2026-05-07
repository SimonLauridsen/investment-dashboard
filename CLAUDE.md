# Speculative Alpha — Developer Guide

## What this is
A personal investment dashboard. Single-page app (FastAPI backend + vanilla JS frontend in one `index.html`). Deployed on Render. Trades via Nordnet.

## Architecture
| File | Purpose |
|------|---------|
| `backend.py` | FastAPI server — auth, stock data, watchlist management, Gist persistence |
| `index.html` | Entire frontend (HTML + Tailwind + Chart.js + vanilla JS, no build step) |
| `requirements.txt` | Python deps: fastapi, uvicorn, yfinance, pandas, numpy, python-multipart |
| `render.yaml` | Render deployment config |
| `start.sh` | Local start script |

## Running locally
```bash
uvicorn backend:app --reload --port 8000
# open http://localhost:8000 — first run creates users.json locally
```
No Gist credentials needed for local dev; data is stored in local JSON files instead.

## Key concepts

### Stock universe
- `STOCKS` — 6 curated "active" picks with full thesis/catalyst metadata
- `CANDIDATE_POOL` — 8 bench picks with metadata
- `ALL_STOCKS = STOCKS | CANDIDATE_POOL`
- `DYNAMIC_UNIVERSE` — ~130 index constituents (OMX C25, OBX, OMXS30, S&P 100) scanned at swap time

### Watchlist (6 active slots)
`ACTIVE_WATCHLIST` — list of `{ticker, added_date, price_at_add}` dicts.
Persisted to Gist (`watchlist.json`) in production, local file in dev.

Auto-refresh runs every hour: if any active stock has SELL/STRONG SELL signal, or its `catalyst_date` passed without a BUY, it is swapped for the best-scoring candidate from the full universe. Replacement is logged to `replacements.json`.

### Persistence: GitHub Gist
Production uses a private Gist (env vars `GIST_ID` + `GITHUB_TOKEN`) for all mutable state:
- `watchlist.json`, `replacements.json`, `users.json`
- `custom_{user}.json`, `positions_{user}.json`

`_gist_cache` is the in-memory mirror. All writes update the cache immediately; the Gist push happens in a background thread.

**Important**: Python's `json.dumps` can write bare `NaN` / `Infinity` tokens (not valid JSON). `_gist_fetch_all` sanitises these to `null` on load. Always use `_safe_price()` when persisting float prices — it converts NaN to None.

### Auth
Cookie-based session tokens (HMAC-SHA256). Passwords stored as PBKDF2-SHA256 hashes. `SECRET_KEY`, `INVITE_CODE`, `USERS`, `DASHBOARD_PASSWORD` are env vars.

## Environment variables (Render)
| Var | Purpose |
|-----|---------|
| `SECRET_KEY` | HMAC signing key for session tokens |
| `INVITE_CODE` | Required to register new accounts |
| `USERS` | Seed accounts: `user1:pass1,user2:pass2` |
| `DASHBOARD_PASSWORD` | Seeds `admin` account |
| `GIST_ID` | GitHub Gist ID for persistence |
| `GITHUB_TOKEN` | GitHub PAT with gist scope |

## Getting context for a task
Run `git log --oneline -30` to see recent changes — this is the authoritative changelog. Do not create a separate changelog file.

## Nordnet links
All stock picks must be available on Nordnet (Denmark). `nordnet_url()` builds the link from ticker + exchange code. URL pattern: `https://www.nordnet.dk/aktier/kurser/{name-slug}-{ticker}-{exchange}`.

## Common tasks

### Add a new stock to STOCKS or CANDIDATE_POOL
Add a dict entry in `backend.py` with keys: `name`, `theme`, `catalyst`, `thesis`, `risk`, `macro`, `catalyst_date`, `catalyst_event`, `catalyst_note`.

### Change the swap logic
`_do_check_and_refresh()` — swap triggers (SELL signal or expired catalyst). `_best_replacement()` — scores the full universe and picks the highest-scoring candidate not currently in the watchlist.

### Change what the History tab shows
`renderHistoryLog()` in `index.html` (~line 1103). Data comes from `/api/replacements`.

### Debug a swap that produced wrong history
`_backfill_known_swaps()` runs on every startup and can upsert/remove known-wrong log entries. Update the `wrong_pairs` set or `known` list there if the log needs manual correction.

## Testing
No automated test suite. Test manually by running the dev server and exercising the UI. Check browser console for JS errors. Check uvicorn stdout for Python errors.
