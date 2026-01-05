import time
import threading
from functools import lru_cache
from urllib.parse import urlparse, quote

from typing import Optional, Dict, Any

import requests
from fastapi import FastAPI, Query, Body, HTTPException

app = FastAPI()

# -----------------------------------
# CONFIG
# -----------------------------------

# Make sure this matches your Render URL
SERVICE_BASE_URL = "https://themarket-api.onrender.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

ALLOWED_TIMEOUT = 10  # seconds
SELF_PING_INTERVAL = 60  # seconds

# Whitelisted domains for internal proxy
WHITELISTED_DOMAINS = {
    "query1.finance.yahoo.com",
    "stooq.com",
    "api.coingecko.com",
    "symbol-search.tradingview.com",
    "www.alphavantage.co",
    "finnhub.io",
    "query2.finance.yahoo.com",
}


# -----------------------------------
# INTERNAL PROXY (GET + POST, WHITELISTED)
# -----------------------------------

def _check_whitelist(url: str):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    # strip port if present
    if ":" in host:
        host = host.split(":", 1)[0]
    if host not in WHITELISTED_DOMAINS:
        raise HTTPException(status_code=400, detail=f"Domain not allowed: {host}")


@app.api_route("/proxy", methods=["GET", "POST"])
def internal_proxy(
    url: str = Query(..., description="Target URL (must be whitelisted)"),
    method: str = Query("GET", description="HTTP method: GET or POST"),
    body: Optional[Dict[str, Any]] = Body(default=None),
):
    """
    Internal proxy that forwards GET/POST requests to whitelisted domains only.
    - Use for future frontend calls if you need raw API access.
    - Market endpoints below call providers directly and don't use this.
    """
    _check_whitelist(url)
    method_upper = method.upper()

    try:
        if method_upper == "GET":
            r = requests.get(url, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
        elif method_upper == "POST":
            r = requests.post(url, json=body, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
        else:
            raise HTTPException(status_code=400, detail="Only GET and POST are supported")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=str(e))

    content_type = r.headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            return r.json()
        except ValueError:
            # fall through to text
            pass

    return {"status_code": r.status_code, "content": r.text}


@lru_cache(maxsize=256)
def cached_get_json(url: str) -> dict:
    """
    Cached GET JSON for provider calls that are safe to cache.
    """
    _check_whitelist(url)
    r = requests.get(url, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    return r.json()


# -----------------------------------
# ASSET TYPE DETECTION
# -----------------------------------

def detect_asset_type(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("^"):
        return "index"
    if "=" in s:
        return "future"
    return "stock"  # includes ETFs


# -----------------------------------
# EXTERNAL FETCHERS (DIRECT, NO EXTERNAL PROXY)
# -----------------------------------

def fetch_yahoo_quote(symbol: str) -> dict:
    """
    Fetch quote data from Yahoo Finance.
    """
    encoded_symbol = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={encoded_symbol}"
    data = cached_get_json(url)
    result = data.get("quoteResponse", {}).get("result", [])
    if not result:
        raise Exception("No Yahoo data for symbol")
    return result[0]


def stooq_symbol_for_stock(ticker: str) -> str:
    return ticker.lower() + ".us"


def stooq_symbol_for_future(symbol: str) -> str:
    s = symbol.upper()
    base = s.replace("=F", "").lower()
    return base + ".f"


def stooq_symbol_for_index(symbol: str) -> str:
    return symbol.lower()


def fetch_stooq_quote(symbol: str) -> float:
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=json"
    data = cached_get_json(url)

    if not isinstance(data, list) or not data:
        raise Exception("No Stooq data")
    row = data[0]
    close = row.get("close")
    if not close or close == "N/A":
        raise Exception("Stooq close not available")
    return float(close)


def fetch_coingecko_price(coin_id: str) -> float:
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
    data = cached_get_json(url)
    if coin_id not in data or "usd" not in data[coin_id]:
        raise Exception("No CoinGecko price")
    return float(data[coin_id]["usd"])


# -----------------------------------
# HEALTH ENDPOINT
# -----------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# -----------------------------------
# UNIFIED PRICE ENDPOINT (STOCK/ETF/FUTURE/INDEX)
# -----------------------------------

@app.get("/price/{symbol}")
def get_price(symbol: str):
    """
    Unified endpoint for:
      - stocks/ETFs: AAPL, MSFT, TSLA, SPY, QQQ
      - futures: CL=F, ES=F, GC=F
      - indexes: ^GSPC, ^NDX, ^DJI, etc.
    """
    raw_symbol = symbol
    symbol = symbol.upper()
    asset_type = detect_asset_type(symbol)

    # 1) Yahoo
    try:
        q = fetch_yahoo_quote(symbol)
        price = q.get("regularMarketPrice")
        if price is not None:
            return {
                "source": "yahoo",
                "asset_type": asset_type,
                "symbol": symbol,
                "price": price,
            }
    except Exception:
        pass

    # 2) Stooq
    try:
        if asset_type == "stock":
            stq_symbol = stooq_symbol_for_stock(symbol)
        elif asset_type == "future":
            stq_symbol = stooq_symbol_for_future(symbol)
        elif asset_type == "index":
            stq_symbol = stooq_symbol_for_index(symbol)
        else:
            stq_symbol = stooq_symbol_for_stock(symbol)

        price = fetch_stooq_quote(stq_symbol)
        return {
            "source": "stooq",
            "asset_type": asset_type,
            "symbol": symbol,
            "stooq_symbol": stq_symbol,
            "price": price,
        }
    except Exception:
        pass

    return {
        "error": "Invalid symbol or no data available",
        "symbol": raw_symbol,
        "asset_type": asset_type,
    }


# -----------------------------------
# FUTURES / INDEX WRAPPERS
# -----------------------------------

@app.get("/futures/{symbol}")
def get_futures(symbol: str):
    data = get_price(symbol)
    data["endpoint"] = "futures"
    return data


@app.get("/index/{symbol}")
def get_index(symbol: str):
    data = get_price(symbol)
    data["endpoint"] = "index"
    return data


# -----------------------------------
# CRYPTO ENDPOINT
# -----------------------------------

@app.get("/crypto/{symbol}")
def get_crypto(symbol: str):
    coin_id = symbol.lower()
    try:
        price = fetch_coingecko_price(coin_id)
        return {
            "source": "coingecko",
            "symbol": coin_id.upper(),
            "price": price,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# -----------------------------------
# SELF-PING BACKGROUND TASK
# -----------------------------------

def self_ping_loop():
    """
    Keep the Render service awake by pinging /health periodically.
    """
    time.sleep(10)
    url = f"{SERVICE_BASE_URL}/health"

    while True:
        try:
            requests.get(url, timeout=ALLOWED_TIMEOUT)
        except Exception:
            pass
        time.sleep(SELF_PING_INTERVAL)


@app.on_event("startup")
def start_self_ping():
    t = threading.Thread(target=self_ping_loop, daemon=True)
    t.start()
