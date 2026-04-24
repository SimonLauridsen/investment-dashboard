from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import json
import urllib.request
import urllib.parse

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STOCKS = {
    "NNE": {
        "name": "NANO Nuclear Energy Inc.",
        "theme": "Portable Micro-Reactors",
        "catalyst": "White House Space Nuclear Mandate (Apr 14 2026) targets reactor deployment beyond Earth by 2028. DOE GAIN voucher awarded for KRONOS MMR microreactor. $577M cash after $400M private placement. Reuters SMR 2026 conference platinum sponsor.",
        "thesis": "The most non-obvious nuclear play: portable micro-reactors for military forward bases, remote industry, and space. White House executive mandate just issued — procurement timelines could accelerate before the market prices it in. $577M cash = years of runway.",
        "risk": "Pre-revenue. KRONOS construction permit not until mid-2027. Regulatory path long.",
        "macro": "AI power demand + White House space nuclear mandate + off-grid energy needs for defence. Structural multi-decade tailwind."
    },
    "LUNR": {
        "name": "Intuitive Machines",
        "theme": "Lunar Economy / NASA",
        "catalyst": "$4.6B NASA Lunar Terrain Vehicle contract decision pending. $180M IM-5 lander contract just won. 5-satellite lunar GPS network launching mid-2026. Backlog $943M. Revenue guidance $900M–$1B for 2026.",
        "thesis": "The only commercial company with a proven lunar lander that has already landed on the Moon. NASA is building a permanent lunar economy and Intuitive Machines is the prime contractor. $4.6B LTV award would be a 5× revenue event.",
        "risk": "Government revenue is lumpy. CFO sold 24K shares in Apr 2026. Ongoing operating losses.",
        "macro": "Artemis programme accelerating. US-China lunar competition driving NASA budget prioritisation. Commercial space economy inflecting."
    },
    "CRML": {
        "name": "Critical Metals Corp",
        "theme": "Greenland Rare Earths",
        "catalyst": "Trump's $12B Project Vault critical minerals reserve. Jan 2027 Pentagon ban on Chinese-sourced rare earth magnets. US-China trade war accelerating domestic sourcing.",
        "thesis": "Controls Tanbreez — one of the largest non-Chinese rare earth deposits globally, in Greenland. Jumped 35% in single session on US-Greenland talks. This is a geopolitical asset, not just a mining stock.",
        "risk": "Early stage. Greenland political dynamics. Long development timeline.",
        "macro": "China controls 90% of rare earth magnet manufacturing. US defense mandate by 2027."
    },
    "RGTI": {
        "name": "Rigetti Computing",
        "theme": "Quantum Computing",
        "catalyst": "108-qubit Cepheus-1-108Q system just launched (+34% on debut). $8.4M C-DAC order for H2 2026 delivery. $5.8M AFRL contract for quantum networking. 150+ qubit system targeting 99.7% fidelity by end 2026. 336-qubit Lyra processor in roadmap for quantum advantage demonstration.",
        "thesis": "Only pure-play quantum hardware company shipping real systems to real customers. White House quantum tech mandate accelerating defence procurement. $589M cash = 3-4 years of runway regardless of revenue trajectory. 336-qubit Lyra is the quantum advantage trigger the sector has been waiting for.",
        "risk": "FY2025 revenue down 56% YoY. Still pre-scale. IBM and Google are formidable competitors.",
        "macro": "White House National Quantum Initiative. US-China quantum supremacy race. DoD quantum networking contracts expanding."
    },
    "ASML.AS": {
        "name": "ASML Holding N.V.",
        "theme": "AI Semiconductor Infrastructure",
        "catalyst": "Q1 2026 orders €7.7B — 2× analyst consensus. US CHIPS Act + European Chips Act combined €100B+ capex requires ASML EUV equipment. Trade war urgency driving TSMC/Samsung/Intel to front-load orders ahead of further US-China export controls. Only supplier of EUV lithography machines globally.",
        "thesis": "The most non-obvious AI play in Europe: ASML doesn't make chips, it makes the only machines in the world capable of making advanced AI chips. 30 years of R&D created a monopoly that cannot be replicated. Without ASML, there are no AI chips. Pulled back ~20% from ATH on tariff uncertainty — but chips are largely exempt from tariff frameworks.",
        "risk": "China export ban reduces addressable market ~15%. High valuation. TSMC capex slowdown would reduce order intake.",
        "macro": "AI infrastructure buildout requires 2–3× advanced chip fabrication capacity. US CHIPS Act + EU Chips Act funding. US-China semiconductor decoupling entrenches ASML's Western monopoly."
    },
    "NEL.OL": {
        "name": "Nel ASA",
        "theme": "European Hydrogen Infrastructure",
        "catalyst": "EU 10Mt renewable hydrogen target by 2030. German H2Global programme €2B allocation. Norwegian government hydrogen offtake guarantee. US DOE loan programs for Nel's US electrolyzer manufacturing expansion. Q1 2026 results due May 2026.",
        "thesis": "Norway's national hydrogen champion and Europe's largest electrolyzer manufacturer. The EU made hydrogen its strategic replacement for Russian gas — Nel makes the machines that produce it. Trading at 2.25 NOK vs ATH of 27 NOK, pricing in a worst-case scenario while EU mandates structurally improve fundamentals. Lowest-cost electrolyzer technology at scale.",
        "risk": "Capital-intensive. Hydrogen demand ramp slower than modelled. Chinese electrolyzer competitors lowering prices.",
        "macro": "EU energy independence from Russia. European Green Deal hydrogen mandate. Industrial decarbonization (steel, shipping, aviation). Norwegian offshore wind-to-hydrogen export strategy."
    }
}

EXCHANGE_TO_NORDNET = {
    "NMS": "xnas", "NGM": "xnas", "NCM": "xnas",  # NASDAQ tiers
    "NYQ": "xnys",                                   # NYSE
    "ASE": "xnas",                                   # NYSE American
    "AMS": "xams",                                   # Euronext Amsterdam
    "OSL": "xosl",                                   # Oslo Børs
    "CPH": "xcse",                                   # Nasdaq Copenhagen
    "STO": "xsto",                                   # Nasdaq Stockholm
    "HEL": "xhel",                                   # Nasdaq Helsinki
    "LSE": "xlon",                                   # London Stock Exchange
    "GER": "xetr", "XETR": "xetr",                  # Xetra (Frankfurt)
    "PNK": None,                                     # OTC Pink — not on Nordnet
    "OTC": None,
}

def nordnet_url(ticker: str, company_name: str, exchange_code: str) -> str | None:
    exc = EXCHANGE_TO_NORDNET.get(exchange_code)
    if exc is None:
        return None
    import re
    # Strip Yahoo Finance exchange suffix (.AS, .OL, .CO, .ST, .L, .DE, .HE, .PA, etc.)
    clean_ticker = re.sub(r'\.[A-Z]{1,2}$', '', ticker).lower()
    drop = r"\b(inc\.?|corp\.?|ltd\.?|llc\.?|plc\.?|ag|sa|nv|n\.v\.?|bv|b\.v\.?|asa|a\.s\.?|a/s|oyj|ab|holdings?|group|co\.?)\b"
    slug = re.sub(drop, "", company_name.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return f"https://www.nordnet.dk/aktier/kurser/{slug}-{clean_ticker}-{exc}"

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
        overall = "STRONG BUY"
        color = "#00ff88"
    elif score >= 1:
        overall = "BUY"
        color = "#44cc77"
    elif score == 0:
        overall = "HOLD"
        color = "#ffaa00"
    elif score >= -2:
        overall = "SELL"
        color = "#ff6644"
    else:
        overall = "STRONG SELL"
        color = "#ff2244"

    return {"overall": overall, "color": color, "score": score, "signals": signals}

@app.get("/api/stock/{ticker}")
def get_stock(ticker: str):
    try:
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

        info = STOCKS.get(ticker, {})
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
            "nordnet_verified": ticker in STOCKS,
            "updated_at": datetime.now().isoformat()
        }
    except Exception as e:
        return {"error": str(e), "ticker": ticker}

@app.get("/api/search")
def search_tickers(q: str = Query(default="", min_length=1)):
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=8&newsCount=0&enableFuzzyQuery=false"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read())
        quotes = [
            {"symbol": item["symbol"], "name": item.get("shortname") or item.get("longname", ""), "exchange": item.get("exchDisp", "")}
            for item in data.get("quotes", [])
            if item.get("quoteType") == "EQUITY"
        ]
        return quotes
    except Exception:
        return []

@app.get("/api/stocks")
def get_all_stocks():
    tickers = list(STOCKS.keys())
    results = [get_stock(t) for t in tickers]
    return results

@app.get("/")
def serve_index():
    return FileResponse("index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
