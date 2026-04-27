from fastapi import FastAPI, Query, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, date
from pathlib import Path
import json, os, hmac, hashlib, base64, threading
import urllib.request
import urllib.parse

# ── Auth ───────────────────────────────────────────────────────────────────────
SECRET_KEY  = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
INVITE_CODE = os.environ.get("INVITE_CODE", "")
USERS_FILE  = Path("users.json")

# -- GitHub Gist persistence (free durable storage) ----------------------------
GIST_ID      = os.environ.get("GIST_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
_gist_cache: dict = {}
_gist_lock  = threading.Lock()

def _gist_enabled() -> bool:
    return bool(GIST_ID and GITHUB_TOKEN)

def _gist_fetch_all():
    """Load all Gist files into _gist_cache at startup."""
    if not _gist_enabled():
        return
    try:
        req = urllib.request.Request(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "InvestmentDashboard/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            gist_data = json.loads(resp.read())
        with _gist_lock:
            for filename, file_info in gist_data.get("files", {}).items():
                try:
                    _gist_cache[filename] = json.loads(file_info.get("content", "null"))
                except Exception:
                    pass
        print(f"[gist] Loaded {len(_gist_cache)} files from Gist")
    except Exception as e:
        print(f"[gist] Startup fetch failed: {e}")

def _gist_push(filename: str, data):
    if not _gist_enabled():
        return
    try:
        payload = json.dumps({"files": {filename: {"content": json.dumps(data)}}}).encode()
        req = urllib.request.Request(
            f"https://api.github.com/gists/{GIST_ID}",
            data=payload, method="PATCH",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json",
                "User-Agent": "InvestmentDashboard/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception as e:
        print(f"[gist] Push failed for {filename}: {e}")

def _gist_write(filename: str, data):
    """Update in-memory cache immediately; push to Gist in background."""
    with _gist_lock:
        _gist_cache[filename] = data
    threading.Thread(target=_gist_push, args=(filename, data), daemon=True).start()

# -- Password hashing (stdlib only, no bcrypt dependency) ----------------------
def _hash_pw(pw: str) -> str:
    salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 260_000)
    return f"pbkdf2:{salt}:{dk.hex()}"

def _check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, dk_hex = stored.split(":", 2)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 260_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False

# -- File-backed user store ----------------------------------------------------
def _load_users() -> dict:
    if _gist_enabled():
        with _gist_lock:
            return dict(_gist_cache.get("users.json") or {})
    try:
        return json.loads(USERS_FILE.read_text()) if USERS_FILE.exists() else {}
    except Exception:
        return {}

def _save_users(users: dict):
    if _gist_enabled():
        _gist_write("users.json", users)
    else:
        USERS_FILE.write_text(json.dumps(users))

def _init_users():
    """Seed accounts from USERS / DASHBOARD_PASSWORD env vars on every startup."""
    users = _load_users()
    changed = False
    for entry in os.environ.get("USERS", "").split(","):
        entry = entry.strip()
        if ":" in entry:
            u, p = entry.split(":", 1)
            u = u.strip().lower()
            if u not in users:
                users[u] = _hash_pw(p.strip())
                changed = True
    dp = os.environ.get("DASHBOARD_PASSWORD", "")
    if dp and "admin" not in users:
        users["admin"] = _hash_pw(dp)
        changed = True
    if changed:
        _save_users(users)

_gist_fetch_all()
_init_users()

# -- Session tokens ------------------------------------------------------------
def _make_token(username: str) -> str:
    msg = f"auth:{username}".encode()
    sig = hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(msg + b"|" + sig).decode()

def _verify_token(token: str) -> str | None:
    try:
        decoded = base64.urlsafe_b64decode(token.encode())
        msg, sig = decoded.split(b"|", 1)
        expected = hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        prefix, username = msg.decode().split(":", 1)
        return username if prefix == "auth" else None
    except Exception:
        return None

# -- Shared HTML base for auth pages -------------------------------------------
_AUTH_CSS = """
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0a0f1e;color:#e5e7eb;font-family:system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center}}
    .card{{background:#111827;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:44px 40px;width:100%;max-width:380px}}
    h1{{font-size:20px;font-weight:700;margin-bottom:6px;letter-spacing:-.3px}}
    .sub{{color:#6b7280;font-size:13px;margin-bottom:32px}}
    label{{font-size:12px;color:#9ca3af;display:block;margin-bottom:6px;font-weight:500}}
    input{{width:100%;background:#0a0f1e;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:11px 14px;color:#e5e7eb;font-size:14px;outline:none;margin-bottom:16px;transition:border .15s}}
    input:focus{{border-color:#3b82f6}}
    button{{width:100%;background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:12px;font-size:14px;font-weight:600;cursor:pointer;transition:background .15s}}
    button:hover{{background:#2563eb}}
    .error{{color:#ef4444;font-size:13px;margin-bottom:14px;padding:10px 12px;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:8px}}
    .link{{display:block;text-align:center;margin-top:20px;font-size:13px;color:#6b7280}}
    .link a{{color:#3b82f6;text-decoration:none}}
    .link a:hover{{text-decoration:underline}}
"""

LOGIN_HTML = f"""<!DOCTYPE html><html><head>
  <title>Speculative Alpha — Sign in</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>{_AUTH_CSS}</style></head><body>
  <div class="card">
    <h1>⚡ Speculative Alpha</h1>
    <p class="sub">Personal investment dashboard</p>
    {{error}}
    <form method="post" action="/login">
      <label>Username</label>
      <input type="text" name="username" autofocus autocomplete="username" placeholder="Your username">
      <label>Password</label>
      <input type="password" name="password" autocomplete="current-password" placeholder="Your password">
      <button type="submit">Sign in</button>
    </form>
    <p class="link"><a href="/register">Create an account →</a></p>
  </div></body></html>"""

REGISTER_HTML = f"""<!DOCTYPE html><html><head>
  <title>Speculative Alpha — Create account</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>{_AUTH_CSS}</style></head><body>
  <div class="card">
    <h1>⚡ Speculative Alpha</h1>
    <p class="sub">Create your account</p>
    {{error}}
    <form method="post" action="/register">
      <label>Invite code</label>
      <input type="password" name="invite_code" placeholder="Ask Simon for the invite code">
      <label>Choose a username</label>
      <input type="text" name="username" autocomplete="username" placeholder="e.g. lars">
      <label>Choose a password</label>
      <input type="password" name="password" autocomplete="new-password" placeholder="Min. 6 characters">
      <label>Confirm password</label>
      <input type="password" name="confirm" autocomplete="new-password" placeholder="Repeat your password">
      <button type="submit">Create account</button>
    </form>
    <p class="link"><a href="/login">← Back to sign in</a></p>
  </div></body></html>"""

# -- Middleware ----------------------------------------------------------------
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/login", "/logout", "/register"):
            return await call_next(request)
        username = _verify_token(request.cookies.get("sa_session", ""))
        if not username:
            return RedirectResponse("/login", status_code=302)
        request.state.username = username
        return await call_next(request)

app = FastAPI()
app.add_middleware(AuthMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/login", response_class=HTMLResponse)
def login_page():
    return LOGIN_HTML.format(error="")

@app.post("/login")
async def do_login(username: str = Form(...), password: str = Form(...)):
    uname = username.strip().lower()
    users = _load_users()
    if uname in users and _check_pw(password, users[uname]):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("sa_session", _make_token(uname), httponly=True,
                        max_age=60 * 60 * 24 * 30, samesite="lax")
        return resp
    err = '<p class="error">Invalid username or password</p>'
    if not users:
        err = '<p class="error">No accounts exist yet — <a href="/register" style="color:#3b82f6">create one</a>.</p>'
    return HTMLResponse(LOGIN_HTML.format(error=err), status_code=401)

@app.get("/register", response_class=HTMLResponse)
def register_page():
    return REGISTER_HTML.format(error="")

@app.post("/register")
async def do_register(invite_code: str = Form(...), username: str = Form(...),
                      password: str = Form(...), confirm: str = Form(...)):
    def err(msg): return HTMLResponse(REGISTER_HTML.format(error=f'<p class="error">{msg}</p>'), 400)
    if not INVITE_CODE:
        return err("Registration is disabled — contact Simon to get an account.")
    if invite_code != INVITE_CODE:
        return err("Wrong invite code.")
    uname = username.strip().lower()
    if len(uname) < 2:
        return err("Username must be at least 2 characters.")
    if not uname.isalnum():
        return err("Username can only contain letters and numbers.")
    if len(password) < 6:
        return err("Password must be at least 6 characters.")
    if password != confirm:
        return err("Passwords do not match.")
    users = _load_users()
    if uname in users:
        return err("That username is already taken.")
    users[uname] = _hash_pw(password)
    _save_users(users)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("sa_session", _make_token(uname), httponly=True,
                    max_age=60 * 60 * 24 * 30, samesite="lax")
    return resp

@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("sa_session")
    return resp

# ── Per-user data storage ─────────────────────────────────────────────────────
USER_DATA_DIR = Path("user_data")
USER_DATA_DIR.mkdir(exist_ok=True)

def _user_file(username: str, kind: str) -> Path:
    return USER_DATA_DIR / f"{kind}_{username}.json"

def _read_user(username: str, kind: str, default):
    filename = f"{kind}_{username}.json"
    if _gist_enabled():
        with _gist_lock:
            val = _gist_cache.get(filename)
        return val if val is not None else default
    f = _user_file(username, kind)
    try:
        return json.loads(f.read_text()) if f.exists() else default
    except Exception:
        return default

def _write_user(username: str, kind: str, data):
    filename = f"{kind}_{username}.json"
    if _gist_enabled():
        _gist_write(filename, data)
    else:
        _user_file(username, kind).write_text(json.dumps(data))

# ── Custom tickers (per user) ─────────────────────────────────────────────────
@app.get("/api/custom")
def get_custom(request: Request):
    return _read_user(request.state.username, "custom", [])

@app.post("/api/custom/{ticker}")
def add_custom(ticker: str, request: Request):
    tickers = _read_user(request.state.username, "custom", [])
    if ticker not in tickers:
        tickers.append(ticker)
        _write_user(request.state.username, "custom", tickers)
    return tickers

@app.delete("/api/custom/{ticker}")
def remove_custom(ticker: str, request: Request):
    tickers = [t for t in _read_user(request.state.username, "custom", []) if t != ticker]
    _write_user(request.state.username, "custom", tickers)
    return tickers

# ── Portfolio positions (per user) ────────────────────────────────────────────
@app.get("/api/positions")
def get_positions(request: Request):
    return _read_user(request.state.username, "positions", {})

@app.put("/api/positions")
async def save_positions(request: Request):
    data = await request.json()
    _write_user(request.state.username, "positions", data)
    return data

# ── Stock universe ─────────────────────────────────────────────────────────────
# Active watchlist (6 slots) — persisted to watchlist.json
# Candidate pool — bench picks promoted when a slot opens

STOCKS = {
    "NNE": {
        "name": "NANO Nuclear Energy Inc.",
        "theme": "Portable Micro-Reactors",
        "catalyst": "White House Space Nuclear Mandate (NSTM-3, Apr 14 2026) targets reactor deployment beyond Earth by 2028. DOE GAIN voucher + ORNL safety-study collaboration for KRONOS MMR now underway. $577M cash after $400M private placement.",
        "thesis": "NSTM-3 gives NNE a direct policy tailwind for the KRONOS portable microreactor — both for terrestrial defence/remote-industry use and space. DOE/ORNL partnership reduces regulatory uncertainty. The market has priced the mandate pop but not the procurement pipeline that follows.",
        "risk": "Pre-revenue. KRONOS NRC pre-application and construction permit not until mid-2027. Single regulatory delay or competitor contract win could collapse the premium.",
        "macro": "AI power demand + White House space nuclear mandate + off-grid energy needs for defence and space.",
        "catalyst_date": "2026-07-15",
        "catalyst_event": "KRONOS ORNL Safety-Study Completion",
        "catalyst_note": "NRC pre-application milestone or NASA/DoD contract citing NSTM-3 — confirms procurement timeline",
    },
    "LUNR": {
        "name": "Intuitive Machines",
        "theme": "Lunar Economy / NASA",
        "catalyst": "$180M IM-5 South Pole lander contract secured. $4.6B NASA Lunar Terrain Vehicle 'as-a-service' award decision pending. 5-satellite lunar GPS network launching mid-2026. Backlog $943M. Revenue guidance $900M–$1B for 2026.",
        "thesis": "Transitioning from mission-to-mission launcher to durable space infrastructure platform with $943M backlog and full-year revenue guidance of $900M–$1B. The $4.6B LTV 'as-a-service' contract would be a transformational multi-year revenue event — the largest in the company's history.",
        "risk": "$641M shelf filing signals near-term dilution. IM-5 mission failure would damage commercial credibility. Ongoing operating losses.",
        "macro": "Artemis programme accelerating. US-China lunar competition driving NASA budget. Commercial space economy inflecting.",
        "catalyst_date": "2026-09-30",
        "catalyst_event": "IM-5 Launch / NASA LTV Award",
        "catalyst_note": "LTV win is the binary valuation event — replace if lost to competitor",
    },
    "CRML": {
        "name": "Critical Metals Corp",
        "theme": "Greenland Rare Earths",
        "catalyst": "Greenland government approved 92.5% Tanbreez ownership (Apr 17 2026). $30M acceleration program underway. 150-tonne bulk sample + pilot plant commissioning targeting May–June 2026. Jan 2027 Pentagon ban on Chinese-sourced rare earth magnets.",
        "thesis": "Now holds 92.5% of Tanbreez — one of the world's largest non-Chinese REE deposits — with full Greenland government approval. US Project Vault + 2027 defense magnet sourcing rules create structural demand. Pilot plant will be the first hard proof-of-concept production data.",
        "risk": "Path from pilot plant to 85,000 tonne/year commercial production (late 2028–2029) is long and capital-intensive. Further dilutive financing rounds likely. US-Greenland sovereignty tensions remain a political wildcard.",
        "macro": "China controls 90% of rare earth magnet manufacturing. US defense mandate by 2027. Critical minerals supply-chain reshoring accelerating.",
        "catalyst_date": "2026-05-31",
        "catalyst_event": "Tanbreez Pilot Plant Commissioning",
        "catalyst_note": "First ore + recovery-rate data — proof of concept for the deposit; re-score if delayed",
    },
    "RGTI": {
        "name": "Rigetti Computing",
        "theme": "Quantum Computing",
        "catalyst": "108-qubit Cepheus-1 now generally available on Amazon Braket (+34% on debut). $8.4M C-DAC on-premises system order for H2 2026. $5.8M AFRL quantum networking contract. Q1 2026 earnings May 18 2026.",
        "thesis": "The April 2026 general availability of Cepheus-1-108Q — the industry's largest modular quantum system — marks a genuine hardware milestone. C-DAC deal shows real commercial traction for on-premises systems. 1,000-qubit Lyra roadmap by 2027 is the quantum-advantage trigger.",
        "risk": "Revenue still tiny (~$5–10M quarterly) and deeply pre-profit. IBM, Google, and IonQ competing at higher qubit counts with superior fidelity. Modular chiplet architecture must prove it scales fidelity.",
        "macro": "White House National Quantum Initiative. US-China quantum supremacy race. DoD quantum networking contracts expanding.",
        "catalyst_date": "2026-05-18",
        "catalyst_event": "Q1 2026 Earnings + Roadmap Update",
        "catalyst_note": "Revenue recognition from Novera systems + 1,000-qubit timeline update — replace if roadmap slips",
    },
    "ASML.AS": {
        "name": "ASML Holding N.V.",
        "theme": "AI Semiconductor Infrastructure",
        "catalyst": "Q1 2026 beat: €8.8B sales, raised full-year 2026 guidance to €36–40B. AI-led capacity expansion from TSMC/Samsung/Intel structurally insulating demand. US MATCH Act (threatens China DUV servicing) is the key legislative risk to watch.",
        "thesis": "Beat Q1 2026 consensus and raised guidance — AI chip demand is pulling forward EUV orders and insulating ASML from tariff noise. The monopoly on EUV lithography is 30 years of R&D that cannot be replicated. China exposure has already shrunk to ~20% of sales, limiting MATCH Act downside.",
        "risk": "US MATCH Act, if passed, would ban high-margin China DUV servicing revenue (~10% of EPS). China was still 33% of 2025 revenue. Any TSMC capex slowdown would ripple directly.",
        "macro": "AI infrastructure buildout requires 2–3× advanced chip fabrication capacity. US-China semiconductor decoupling entrenches ASML's Western monopoly.",
        "catalyst_date": "2026-07-16",
        "catalyst_event": "Q2 2026 Earnings",
        "catalyst_note": "Order intake re-disclosed? MATCH Act vote status — guidance cut risk if passed",
    },
    "NEL.OL": {
        "name": "Nel ASA",
        "theme": "European Hydrogen Infrastructure",
        "catalyst": "Q1 2026: NOK 85M orders, -5% revenue — weak but expected. Next-gen pressurised alkaline electrolyzer platform launching May 2026 (40–60% CapEx cost reduction claim). CEO flagged 2 more PO signings targeted before end of H1 2026.",
        "thesis": "After 7 years in development, Nel's next-gen pressurised alkaline platform (FID Dec 2025, launch May 2026) promises 40–60% CapEx and 10–20% OpEx reductions vs competitors. If commercially validated, it re-establishes Nel as Europe's cost-leader in green hydrogen — at ~2 NOK vs 27 NOK ATH.",
        "risk": "Q1 order intake was weak. Hydrogen project cancellations and subsidy uncertainty persist across Europe and the US. NOK 1.4B cash reserve being depleted; must secure significant H1 orders before scale-up at Herøya.",
        "macro": "EU energy independence from Russia. European Green Deal hydrogen mandate. Industrial decarbonization (steel, shipping, aviation).",
        "catalyst_date": "2026-05-31",
        "catalyst_event": "Next-Gen Electrolyzer Platform Launch",
        "catalyst_note": "First purchase orders for new platform — commercial validation of 40–60% cost reduction claim",
    },
}

CANDIDATE_POOL = {
    "ALFA.ST": {
        "name": "Alfa Laval AB",
        "theme": "AI Data Center Thermal Management",
        "catalyst": "Hyperscaler data center buildout in Europe 2026–2027 driving liquid-cooling orders. Alfa Laval won major contracts for AI rack cooling with Microsoft and Amazon. Swedish defence budget up 40% — naval vessel cooling systems second catalyst.",
        "thesis": "The most non-obvious AI infrastructure play: every AI GPU rack generates massive heat and liquid cooling is the only scalable solution. Alfa Laval dominates European heat-exchanger supply with a 130-year head start. The market prices it as a boring industrial — not as the company that keeps AI running.",
        "risk": "Cyclical industrial. Data centre orders can be lumpy. Competition from German peers.",
        "macro": "AI data centre buildout requires liquid cooling at scale. European rearmament (naval thermal systems). Energy transition heat-pump demand.",
        "catalyst_date": "2026-07-18",
        "catalyst_event": "Q2 2026 Earnings",
        "catalyst_note": "Data centre order intake — key re-rating signal",
    },
    "COLO-B.CO": {
        "name": "Coloplast B A/S",
        "theme": "Healthcare Devices / Demographic Megatrend",
        "catalyst": "China market re-entry clearance expected 2026. Wound care + urology expansion into ageing markets. Strong USD tailwind on DKK reporting. RSI oversold at 37 — near-term mean reversion setup.",
        "thesis": "World's largest maker of ostomy and continence care products — a near-monopoly in a market that only grows as populations age. Oversold to RSI 37 despite fully intact fundamentals. Defensive compounder getting the same drawdown as cyclicals for no reason. STRONG BUY signal.",
        "risk": "Currency headwinds. China regulatory uncertainty could delay re-entry.",
        "macro": "Global ageing population (65+ doubles by 2050). Healthcare spending structurally rising. Emerging market middle class expansion.",
        "catalyst_date": "2026-08-20",
        "catalyst_event": "Q2 2026 Earnings + China Update",
        "catalyst_note": "China re-entry confirmation or delay — binary for thesis",
    },
    "SAND.ST": {
        "name": "Sandvik AB",
        "theme": "Critical Minerals Extraction",
        "catalyst": "Mining capex surging globally for copper, lithium and cobalt. Sandvik Q1 2026 orders up on strong demand. Nordic defence spending (Varel drilling systems for military engineering).",
        "thesis": "Sandvik makes the drill bits, mining tools and rock-processing equipment for extracting the critical minerals the energy transition requires. Every mine expanding copper or lithium production buys Sandvik. The picks-and-shovels play on critical minerals with far lower geopolitical risk than the miners themselves.",
        "risk": "Cyclical exposure to mining capex. Strong SEK hurts exports.",
        "macro": "Energy transition requires 4–6× more copper and lithium mining. EV buildout. NATO Nordic defence spending surge.",
        "catalyst_date": "2026-07-18",
        "catalyst_event": "Q2 2026 Earnings",
        "catalyst_note": "Mining order book — confirms or breaks extraction thesis",
    },
    "LDOS": {
        "name": "Leidos Holdings, Inc.",
        "theme": "US Defence IT & AI Modernisation",
        "catalyst": "DoD AI contract awards accelerating in 2026. Pentagon $1.8T budget. Leidos is the largest US defence IT integrator. RSI at 32 — deeply oversold on broader market selloff, not company-specific news.",
        "thesis": "The picks-and-shovels play on US military AI modernisation. Every weapons system being upgraded with AI needs Leidos's integration work. RSI at 32 means it's priced for a worst-case scenario while the DoD budget is at record highs. Largest defence IT firm = lowest execution risk of any defence play.",
        "risk": "Government contract concentration. Budget continuing-resolution risk. Tariff-driven DoD reprioritisation.",
        "macro": "US military AI modernisation. Cybersecurity buildout. $1.8T defence budget.",
        "catalyst_date": "2026-07-24",
        "catalyst_event": "Q2 FY2026 Earnings",
        "catalyst_note": "Contract award pipeline — confirms defence IT spending is intact",
    },
    "DSV.CO": {
        "name": "DSV A/S",
        "theme": "Global Trade Reshoring",
        "catalyst": "DB Schenker integration creating world's largest freight forwarder — full synergies mid-2026. US-China trade war supply chain reshuffling generating massive freight-forwarding demand. H1 2026 revenue guidance upgraded.",
        "thesis": "DSV just acquired DB Schenker to become the world's largest freight forwarder. Trade war chaos is a windfall for freight companies — every supply chain being rerouted generates fees. The bigger and more global the network, the more indispensable DSV becomes. Market treats it as a cyclical; it is actually a network business.",
        "risk": "Integration execution risk. Freight rate volatility. Economic slowdown reducing trade volumes.",
        "macro": "US-China trade war reshuffling global supply chains. Near-shoring and friend-shoring driving new freight patterns.",
        "catalyst_date": "2026-08-10",
        "catalyst_event": "H1 2026 Results",
        "catalyst_note": "DB Schenker integration synergies — confirms or breaks the deal thesis",
    },
    "ORSTED.CO": {
        "name": "Orsted A/S",
        "theme": "European Energy Independence",
        "catalyst": "EU energy independence mandate accelerating offshore wind procurement. UK Contracts for Difference Round 7 results Q3 2026. Baltic Sea wind farm approvals. EU 2030 offshore wind target requires 10× current capacity.",
        "thesis": "The world's largest offshore wind developer. Fell 70%+ from ATH on US project write-downs — but the European business is intact and growing. EU energy crisis from Russia has made offshore wind a national security priority, not just an environmental one. Rebuilding from a washed-out base.",
        "risk": "US project write-downs may continue. High interest-rate sensitivity (capital-intensive). Permitting delays.",
        "macro": "EU energy independence from Russia. Net Zero 2050 mandates. North Sea offshore wind buildout. NATO energy security.",
        "catalyst_date": "2026-09-15",
        "catalyst_event": "UK CfD Round 7 Results",
        "catalyst_note": "Contract award confirms European pipeline — key re-rating",
    },
    "EXPN.L": {
        "name": "Experian plc",
        "theme": "Data Analytics / Credit Infrastructure",
        "catalyst": "FY2026 results May 2026. Consumer credit demand rebounding as rate cycle turns. B2B data services expansion into emerging markets. AI-powered fraud detection product launch Q2 2026.",
        "thesis": "Experian is one of three companies globally that control consumer credit data — a structural monopoly. As rates fall and credit demand recovers, Experian's volumes re-accelerate. The AI fraud detection pivot is a new high-margin revenue stream the market has not priced in.",
        "risk": "Regulatory risk on credit data use in EU/UK. Economic slowdown reducing credit applications.",
        "macro": "Rate cycle turning — credit demand recovering. AI-powered financial services expansion. Emerging market credit infrastructure buildout.",
        "catalyst_date": "2026-05-21",
        "catalyst_event": "FY2026 Results",
        "catalyst_note": "Revenue acceleration + AI product revenue — triggers re-rating",
    },
    "SKA-B.ST": {
        "name": "Skanska AB",
        "theme": "European Defence Infrastructure",
        "catalyst": "NATO infrastructure spending (bunkers, airfields, hardened command centres). Nordic countries committed to 3% GDP on defence. Sweden and Finland NATO membership triggering massive military construction pipeline. Swedish government infrastructure package 2026.",
        "thesis": "Skanska builds the physical infrastructure for European rearmament — NATO-spec bunkers, runways, military bases and hardened facilities. The market prices it as a generic construction company; it is actually a primary beneficiary of the largest European defence build-up since WWII. Contracts are long-term and government-backed.",
        "risk": "Construction cost inflation. Labour shortages in Nordics. Project delays on large-scale contracts.",
        "macro": "European NATO rearmament. Nordic defence spending surge. EU defence infrastructure mandate.",
        "catalyst_date": "2026-07-24",
        "catalyst_event": "Q2 2026 Earnings",
        "catalyst_note": "Defence construction order book — key thesis signal",
    },
}

ALL_STOCKS = {**STOCKS, **CANDIDATE_POOL}

# ── Active watchlist ───────────────────────────────────────────────────────────
WATCHLIST_FILE    = Path("watchlist.json")
REPLACEMENTS_FILE = Path("replacements.json")

def _load_watchlist() -> list:
    raw = None
    if _gist_enabled():
        with _gist_lock:
            raw = _gist_cache.get("watchlist.json")
    else:
        try:
            if WATCHLIST_FILE.exists():
                raw = json.loads(WATCHLIST_FILE.read_text())
        except Exception:
            pass
    if not isinstance(raw, list) or not raw:
        raw = list(STOCKS.keys())
    today = date.today().isoformat()
    result = []
    for item in raw:
        if isinstance(item, str):
            # Original 6 picks were live from 2026-04-24 (initial commit date)
            launch_date = "2026-04-24" if item in STOCKS else today
            result.append({"ticker": item, "added_date": launch_date, "price_at_add": None})
        else:
            # Correct added_date for original picks that were migrated with today's date
            if item.get("ticker") in STOCKS and item.get("added_date", "") > "2026-04-24":
                item["added_date"] = "2026-04-24"
                item["price_at_add"] = None  # force re-lookup at correct date
            result.append(item)
    return result

def _save_watchlist(wl: list):
    if _gist_enabled():
        _gist_write("watchlist.json", wl)
    else:
        WATCHLIST_FILE.write_text(json.dumps(wl))

def _wl_tickers() -> list:
    return [e["ticker"] for e in ACTIVE_WATCHLIST]

def _price_on_date(ticker: str, date_str: str) -> float:
    try:
        from datetime import timedelta
        start = date.fromisoformat(date_str)
        end   = start + timedelta(days=5)
        hist  = yf.Ticker(ticker).history(start=str(start), end=str(end))
        if not hist.empty:
            return round(float(hist["Close"].iloc[0]), 4)
    except Exception:
        pass
    return 0.0

def _load_replacements() -> list:
    if _gist_enabled():
        with _gist_lock:
            return list(_gist_cache.get("replacements.json") or [])
    try:
        return json.loads(REPLACEMENTS_FILE.read_text()) if REPLACEMENTS_FILE.exists() else []
    except Exception:
        return []

def _save_replacements(log: list):
    if _gist_enabled():
        _gist_write("replacements.json", log[-20:])
    else:
        REPLACEMENTS_FILE.write_text(json.dumps(log[-20:]))

ACTIVE_WATCHLIST = _load_watchlist()

def _backfill_known_swaps():
    """Seed replacement log entries that predate the logging mechanism."""
    log = _load_replacements()
    tickers = _wl_tickers()
    dirty = False
    known = [
        # Add entries here for any swaps that happened before logging was reliable
        {"removed": "CRML", "added": "LDOS", "reason": "Signal turned SELL",
         "date": "2026-04-27", "removed_added_date": "2026-04-24"},
    ]
    existing = {(e["removed"], e["added"]) for e in log}
    for entry in known:
        key = (entry["removed"], entry["added"])
        if entry["removed"] not in tickers and entry["added"] in tickers and key not in existing:
            log.append(entry)
            dirty = True
    if dirty:
        _save_replacements(log)

def _enrich_replacements():
    """Lazily fill in historical prices for log entries that are missing them."""
    log = _load_replacements()
    dirty = False
    for e in log:
        if not e.get("removed_price_at_add") and e.get("removed_added_date"):
            p = _price_on_date(e["removed"], e["removed_added_date"])
            if p:
                e["removed_price_at_add"] = p
                dirty = True
        if not e.get("removed_price_exit") and e.get("date"):
            p = _price_on_date(e["removed"], e["date"])
            if p:
                e["removed_price_exit"] = p
                dirty = True
    if dirty:
        _save_replacements(log)

_backfill_known_swaps()
_enrich_replacements()

# ── Exchange / Nordnet URL ────────────────────────────────────────────────────
EXCHANGE_TO_NORDNET = {
    "NMS": "xnas", "NGM": "xnas", "NCM": "xnas",
    "NYQ": "xnys",
    "ASE": "xnas",
    "AMS": "xams",
    "OSL": "xosl",
    "CPH": "xcse",
    "STO": "xsto",
    "HEL": "xhel",
    "LSE": "xlon",
    "GER": "xetr", "XETR": "xetr",
    "PNK": None,
    "OTC": None,
}

def nordnet_url(ticker: str, company_name: str, exchange_code: str) -> str | None:
    exc = EXCHANGE_TO_NORDNET.get(exchange_code)
    if exc is None:
        return None
    import re
    clean_ticker = re.sub(r'\.[A-Z]{1,2}$', '', ticker).lower()
    drop = r"\b(inc\.?|corp\.?|ltd\.?|llc\.?|plc\.?|ag|sa|nv|n\.v\.?|bv|b\.v\.?|asa|a\.s\.?|a/s|oyj|ab|holdings?|group|co\.?)\b"
    slug = re.sub(drop, "", company_name.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return f"https://www.nordnet.dk/aktier/kurser/{slug}-{clean_ticker}-{exc}"

# ── Technical indicators ──────────────────────────────────────────────────────
def compute_rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2) if not rsi.empty else 50.0

def compute_macd(prices: pd.Series):
    ema12 = prices.ewm(span=12, adjust=False).mean()
    ema26 = prices.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return round(float(macd_line.iloc[-1]), 4), round(float(signal_line.iloc[-1]), 4), round(float(histogram.iloc[-1]), 4)

def compute_bollinger(prices: pd.Series, period: int = 20):
    sma = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper = sma + (std * 2)
    lower = sma - (std * 2)
    return round(float(upper.iloc[-1]), 4), round(float(sma.iloc[-1]), 4), round(float(lower.iloc[-1]), 4)

def generate_signal(rsi, macd_hist, price, bb_upper, bb_lower, sma20, sma50):
    signals = []
    score = 0
    if rsi < 35:
        signals.append({"type": "BUY", "reason": f"RSI oversold ({rsi})"})
        score += 2
    elif rsi > 70:
        signals.append({"type": "SELL", "reason": f"RSI overbought ({rsi})"})
        score -= 2
    elif rsi < 50:
        score += 1
    if macd_hist > 0:
        signals.append({"type": "BUY", "reason": "MACD histogram positive (bullish momentum)"})
        score += 1
    else:
        signals.append({"type": "SELL", "reason": "MACD histogram negative (bearish momentum)"})
        score -= 1
    if price < bb_lower:
        signals.append({"type": "BUY", "reason": "Price below lower Bollinger Band (mean reversion setup)"})
        score += 2
    elif price > bb_upper:
        signals.append({"type": "SELL", "reason": "Price above upper Bollinger Band (extended)"})
        score -= 2
    if sma50 > 0 and price > sma50:
        signals.append({"type": "BUY", "reason": "Price above 50-day MA (uptrend)"})
        score += 1
    elif sma50 > 0:
        signals.append({"type": "SELL", "reason": "Price below 50-day MA (downtrend)"})
        score -= 1
    if score >= 3:
        overall, color = "STRONG BUY", "#00ff88"
    elif score >= 1:
        overall, color = "BUY", "#44cc77"
    elif score == 0:
        overall, color = "HOLD", "#ffaa00"
    elif score >= -2:
        overall, color = "SELL", "#ff6644"
    else:
        overall, color = "STRONG SELL", "#ff2244"
    return {"overall": overall, "color": color, "score": score, "signals": signals}

# ── Core stock fetch ──────────────────────────────────────────────────────────
def _fetch_stock_data(ticker: str) -> dict:
    tk = yf.Ticker(ticker)
    hist = tk.history(period="3mo", interval="1d")
    if hist.empty:
        return {"error": "No data"}
    prices = hist["Close"]
    current_price = float(prices.iloc[-1])
    prev_price = float(prices.iloc[-2]) if len(prices) > 1 else current_price
    change_pct = round((current_price - prev_price) / prev_price * 100, 2)
    rsi = compute_rsi(prices)
    macd_val, macd_sig, macd_hist = compute_macd(prices)
    bb_upper, bb_mid, bb_lower = compute_bollinger(prices)
    sma20 = float(prices.rolling(20).mean().iloc[-1]) if len(prices) >= 20 else 0
    sma50 = float(prices.rolling(50).mean().iloc[-1]) if len(prices) >= 50 else 0
    signal = generate_signal(rsi, macd_hist, current_price, bb_upper, bb_lower, sma20, sma50)
    chart_data = [
        {"date": str(d.date()), "close": round(float(c), 4), "volume": int(v)}
        for d, c, v in zip(hist.index, hist["Close"], hist["Volume"])
    ]
    info = ALL_STOCKS.get(ticker, {})
    info_raw = tk.info or {}
    market_cap = info_raw.get("marketCap", 0)
    company_name = info.get("name", info_raw.get("longName", ticker))
    exchange_code = info_raw.get("exchange", "NMS")
    nn_url = nordnet_url(ticker, company_name, exchange_code)
    return {
        "ticker": ticker,
        "name": company_name,
        "theme": info.get("theme", info_raw.get("sector", "")),
        "catalyst": info.get("catalyst", ""),
        "thesis": info.get("thesis", ""),
        "risk": info.get("risk", ""),
        "macro": info.get("macro", ""),
        "catalyst_date": info.get("catalyst_date", ""),
        "catalyst_event": info.get("catalyst_event", ""),
        "catalyst_note": info.get("catalyst_note", ""),
        "currency": info_raw.get("currency", "USD"),
        "price": round(current_price, 2),
        "change_pct": change_pct,
        "market_cap": market_cap,
        "rsi": rsi,
        "macd": macd_val,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2),
        "signal": signal,
        "chart_data": chart_data,
        "nordnet_url": nn_url,
        "nordnet_verified": ticker in ALL_STOCKS,
        "updated_at": datetime.now().isoformat(),
    }

@app.get("/api/stock/{ticker}")
def get_stock(ticker: str):
    try:
        return _fetch_stock_data(ticker)
    except Exception as e:
        return {"error": str(e), "ticker": ticker}

@app.get("/api/stocks")
def get_all_stocks():
    global ACTIVE_WATCHLIST
    results = []
    wl_changed = False
    for entry in ACTIVE_WATCHLIST:
        ticker = entry["ticker"]
        try:
            data = _fetch_stock_data(ticker)
            if data.get("error"):
                results.append(data)
                continue
            # Lazily record price_at_add on first fetch after a stock is added
            if not entry.get("price_at_add"):
                p = _price_on_date(ticker, entry["added_date"]) or data["price"]
                entry["price_at_add"] = round(p, 4)
                wl_changed = True
            p0 = entry["price_at_add"]
            p1 = data["price"]
            hypo_pct   = round((p1 - p0) / p0 * 100, 2) if p0 else 0.0
            hypo_value = round(10000 * (1 + hypo_pct / 100), 2)
            data["added_date"]   = entry["added_date"]
            data["price_at_add"] = p0
            data["hypo_pct"]     = hypo_pct
            data["hypo_value"]   = hypo_value
            results.append(data)
        except Exception as e:
            results.append({"error": str(e), "ticker": ticker})
    if wl_changed:
        _save_watchlist(ACTIVE_WATCHLIST)
    return results

@app.get("/api/replacements")
def get_replacements():
    return _load_replacements()

@app.get("/api/search")
def search_tickers(q: str = Query(default="", min_length=1)):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=8&newsCount=0&enableFuzzyQuery=false"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read())
        return [
            {"symbol": item["symbol"], "name": item.get("shortname") or item.get("longname", ""), "exchange": item.get("exchDisp", "")}
            for item in data.get("quotes", [])
            if item.get("quoteType") == "EQUITY"
        ]
    except Exception:
        return []

@app.get("/")
def serve_index():
    return FileResponse("index.html")

# ── Auto-refresh watchlist ────────────────────────────────────────────────────
def _best_replacement(exclude_tickers: list) -> tuple[str | None, float]:
    best_ticker, best_score, best_price = None, -99, 0.0
    for ticker in CANDIDATE_POOL:
        if ticker in exclude_tickers:
            continue
        try:
            data = _fetch_stock_data(ticker)
            if data.get("error"):
                continue
            score = data["signal"]["score"]
            if score > best_score:
                best_score  = score
                best_ticker = ticker
                best_price  = data.get("price", 0.0)
        except Exception:
            continue
    if best_score >= 1:
        return best_ticker, best_price
    return None, 0.0

def _check_and_refresh():
    global ACTIVE_WATCHLIST
    today = date.today().isoformat()
    log = _load_replacements()
    changed = False

    for i, entry in enumerate(ACTIVE_WATCHLIST[:]):
        ticker = entry["ticker"]
        try:
            data = _fetch_stock_data(ticker)
        except Exception:
            continue
        if data.get("error"):
            continue

        signal = data["signal"]["overall"]
        meta   = ALL_STOCKS.get(ticker, {})
        reason = None

        if signal in ("SELL", "STRONG SELL"):
            reason = f"Signal turned {signal}"
        else:
            cat_date = meta.get("catalyst_date", "")
            if cat_date and cat_date < today and signal not in ("BUY", "STRONG BUY"):
                reason = f"Catalyst date {cat_date} passed — signal is {signal}"

        if reason:
            current_tickers = _wl_tickers()
            replacement, repl_price = _best_replacement(current_tickers)
            if replacement:
                ACTIVE_WATCHLIST[i] = {
                    "ticker":       replacement,
                    "added_date":   today,
                    "price_at_add": round(repl_price, 4) if repl_price else None,
                }
                log.append({
                    "removed":              ticker,
                    "added":                replacement,
                    "reason":               reason,
                    "date":                 today,
                    "removed_added_date":   entry.get("added_date"),
                    "removed_price_at_add": entry.get("price_at_add"),
                    "removed_price_exit":   round(data["price"], 4) if data.get("price") else None,
                })
                print(f"[auto-refresh] Replaced {ticker} → {replacement}: {reason}")
                changed = True

    if changed:
        _save_watchlist(ACTIVE_WATCHLIST)
        _save_replacements(log)

def _auto_refresh_loop():
    import time
    time.sleep(300)  # wait 5 min after startup before first check
    while True:
        try:
            _check_and_refresh()
        except Exception as e:
            print(f"[auto-refresh] Error: {e}")
        time.sleep(86400)  # recheck every 24 hours

threading.Thread(target=_auto_refresh_loop, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
