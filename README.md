# Speculative Alpha Dashboard

A live investment dashboard for tracking speculative, high-conviction stock picks with real-time technical analysis, buy/sell signals, and portfolio tracking. Built for use with [Nordnet](https://www.nordnet.dk).

## Features

- **Live data** — prices, RSI, MACD, Bollinger Bands, and SMA20/SMA50 fetched via Yahoo Finance
- **Signal engine** — composite scoring system (RSI, MACD histogram, Bollinger Band position, SMA50 trend) produces STRONG BUY / BUY / HOLD / SELL / STRONG SELL ratings
- **Two-tab layout**
  - *Watchlist* — curated picks with full thesis, catalyst, risk, and macro context
  - *My Dashboard* — add any ticker by search; supports US and all major European exchanges
- **Portfolio tracker** — log a buy price, track P&L live, get exit alerts when stop loss is breached or a price target is hit
- **Nordnet integration** — direct buy links for every tracked stock, covering NASDAQ, NYSE, Euronext Amsterdam, Oslo Børs, Nasdaq Copenhagen/Stockholm/Helsinki, London, and Xetra
- **Search schedule** — per-ticker catalyst calendar with countdown to key events
- **Ticker tape** — live scrolling price bar across all tracked stocks
- **Auto-refresh** — data reloads every 60 seconds

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3, FastAPI, yfinance, pandas, NumPy |
| Frontend | Vanilla JS, Chart.js, Tailwind CSS (CDN) |
| Serving | Uvicorn (single process, serves both API and static HTML) |

## Getting Started

### Prerequisites

```bash
python3 -m pip install fastapi uvicorn yfinance pandas numpy --break-system-packages
```

### Run

```bash
./start.sh
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

## Technical Indicators

| Indicator | Parameters | Signal logic |
|-----------|------------|--------------|
| RSI | 14-period | <35 = BUY (+2), >70 = SELL (−2), <50 = mild BUY (+1) |
| MACD | 12/26/9 EMA | Histogram > 0 = BUY (+1), < 0 = SELL (−1) |
| Bollinger Bands | 20-period, 2σ | Price < lower = BUY (+2), > upper = SELL (−2) |
| SMA50 | 50-day | Price above = BUY (+1), below = SELL (−1) |

**Score thresholds:** ≥3 = STRONG BUY, ≥1 = BUY, 0 = HOLD, ≥−2 = SELL, <−2 = STRONG SELL

## Portfolio Tracking

Click **Bought** on any card to log an entry price and stop style. The dashboard then tracks:

- **P&L** — unrealised % and absolute gain/loss vs. entry
- **Stop loss** — fixed −20% or −25% from entry
- **Trailing stop** — 25% from the highest price seen
- **Targets** — T1 (+35%), T2 (+75%), T3 (+125%)
- **EXIT ALERT** — triggered when stop is breached, a target is hit with a SELL signal, or RSI > 70 with >15% gain

## Watchlist

The curated watchlist is filtered to BUY-signal picks only, spanning multiple markets:

| Ticker | Exchange | Theme |
|--------|----------|-------|
| NNE | NASDAQ | Portable Micro-Reactors |
| LUNR | NASDAQ | Lunar Economy / NASA |
| CRML | NASDAQ | Greenland Rare Earths |
| RGTI | NASDAQ | Quantum Computing |
| ASML.AS | Euronext Amsterdam | AI Semiconductor Infrastructure |
| NEL.OL | Oslo Børs | European Hydrogen Infrastructure |

## Supported Exchanges (Nordnet URL generation)

| Yahoo Finance code | Exchange | Nordnet MIC |
|--------------------|----------|-------------|
| NMS / NGM / NCM | NASDAQ | xnas |
| NYQ | NYSE | xnys |
| AMS | Euronext Amsterdam | xams |
| OSL | Oslo Børs | xosl |
| CPH | Nasdaq Copenhagen | xcse |
| STO | Nasdaq Stockholm | xsto |
| HEL | Nasdaq Helsinki | xhel |
| LSE | London Stock Exchange | xlon |
| GER / XETR | Xetra (Frankfurt) | xetr |

## Project Structure

```
InvestmentDashboard/
├── backend.py      # FastAPI server — data fetching, indicators, signal engine
├── index.html      # Single-file frontend — all UI, charts, portfolio logic
└── start.sh        # Launch script
```
