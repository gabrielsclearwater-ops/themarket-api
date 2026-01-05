import time
import threading
from functools import lru_cache
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, Query, Body, HTTPException

# Import core logic
from core import (
    fetch_yahoo_chart,
    fetch_stooq_quote,
    fetch_crypto_price,
    detect_asset_type,
    stooq_symbol_for_stock,
    stooq_symbol_for_future,
    stooq_symbol_for_index,
)

# -----------------------------------
# CONFIG
# -----------------------------------

SERVICE_BASE_URL = "https://themarket-api.onrender.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

ALLOWED_TIMEOUT = 10
SELF_PING_INTERVAL = 60

# Whitelisted domains for internal proxy
WHITELISTED_DOMAINS = {
    "query1.finance.yahoo.com",
    "query2.finance.yahoo.com",
    "stooq.com",
    "api.coingecko.com",
    "symbol-search.tradingview.com",
    "www.alphavantage.co",
    "finnhub.io",
}

app = FastAPI()

# -----------------------------------
# WHITELIST CHECK
# -----------------------------------

def _check_whitelist(url: str):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if ":" in host:
        host = host.split(":", 1)[0]
    if host not in WHITELISTED_DOMAINS:
        raise HTTPException(status_code=400, detail=f"Domain not allowed: {host}")

# -----------------------------------
# CACHED JSON GET
# -----------------------------------

@lru_cache(maxsize=256)
def cached_get_json(url: str) -> dict:
    _check_whitelist(url)
    r = requests.get(url, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    return r.json()
# -----------------------------------
# INTERNAL PROXY (GET + POST)
# -----------------------------------

@app.api_route("/proxy", methods=["GET", "POST"])
def internal_proxy(
    url: str = Query(..., description="Target URL (must be whitelisted)"),
    method: str = Query("GET", description="HTTP method: GET or POST"),
    body: dict | None = Body(default=None),
):
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
            pass

    return {"status_code": r.status_code, "content": r.text}


# -----------------------------------
# HEALTH ENDPOINT
# -----------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# -----------------------------------
# SELF-PING BACKGROUND TASK
# -----------------------------------

def self_ping_loop():
    """
    Keeps the Render service awake by pinging /health periodically.
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
# -----------------------------------
# UNIFIED PRICE ENDPOINT (STOCK/ETF/FUTURE/INDEX)
# -----------------------------------

@app.get("/price/{symbol}")
def get_price(symbol: str):
    symbol = symbol.upper()
    asset_type = detect_asset_type(symbol)

    # 1) Yahoo Chart API (primary)
    try:
        chart = fetch_yahoo_chart(symbol)
        if chart.get("regularMarketPrice") is not None:
            return {
                "source": "yahoo_chart",
                "asset_type": asset_type,
                "symbol": symbol,

                # Real-time price
                "price": chart["regularMarketPrice"],

                # Metadata
                "previousClose": chart.get("previousClose"),
                "exchangeName": chart.get("exchangeName"),
                "currency": chart.get("currency"),
                "marketState": chart.get("marketState"),

                # OHLC + volume arrays
                "timestamps": chart.get("timestamps", []),
                "open": chart.get("open", []),
                "high": chart.get("high", []),
                "low": chart.get("low", []),
                "close": chart.get("close", []),
                "volume": chart.get("volume", []),
            }
    except Exception:
        pass

    # 2) Stooq fallback
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

    # 3) Total failure
    return {
        "error": "Invalid symbol or no data available",
        "symbol": symbol,
        "asset_type": asset_type,
    }
# -----------------------------------
# CRYPTO ENDPOINT
# -----------------------------------

@app.get("/crypto/{symbol}")
def crypto_price(symbol: str):
    symbol = symbol.lower()
    try:
        return fetch_crypto_price(symbol)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# -----------------------------------
# FUTURES ENDPOINT
# -----------------------------------

@app.get("/futures/{symbol}")
def futures_price(symbol: str):
    symbol = symbol.upper()

    try:
        # Yahoo Chart API first
        chart = fetch_yahoo_chart(symbol)
        if chart.get("regularMarketPrice") is not None:
            return {
                "source": "yahoo_chart",
                "asset_type": "future",
                "symbol": symbol,
                "price": chart["regularMarketPrice"],
                "previousClose": chart.get("previousClose"),
                "exchangeName": chart.get("exchangeName"),
                "currency": chart.get("currency"),
                "marketState": chart.get("marketState"),
                "timestamps": chart.get("timestamps", []),
                "open": chart.get("open", []),
                "high": chart.get("high", []),
                "low": chart.get("low", []),
                "close": chart.get("close", []),
                "volume": chart.get("volume", []),
            }
    except Exception:
        pass

    # Stooq fallback
    try:
        stq_symbol = stooq_symbol_for_future(symbol)
        price = fetch_stooq_quote(stq_symbol)
        return {
            "source": "stooq",
            "asset_type": "future",
            "symbol": symbol,
            "stooq_symbol": stq_symbol,
            "price": price,
        }
    except Exception:
        pass

    return {"error": "Invalid future symbol", "symbol": symbol}


# -----------------------------------
# INDEX ENDPOINT
# -----------------------------------

@app.get("/index/{symbol}")
def index_price(symbol: str):
    symbol = symbol.upper()

    try:
        # Yahoo Chart API first
        chart = fetch_yahoo_chart(symbol)
        if chart.get("regularMarketPrice") is not None:
            return {
                "source": "yahoo_chart",
                "asset_type": "index",
                "symbol": symbol,
                "price": chart["regularMarketPrice"],
                "previousClose": chart.get("previousClose"),
                "exchangeName": chart.get("exchangeName"),
                "currency": chart.get("currency"),
                "marketState": chart.get("marketState"),
                "timestamps": chart.get("timestamps", []),
                "open": chart.get("open", []),
                "high": chart.get("high", []),
                "low": chart.get("low", []),
                "close": chart.get("close", []),
                "volume": chart.get("volume", []),
            }
    except Exception:
        pass

    # Stooq fallback
    try:
        stq_symbol = stooq_symbol_for_index(symbol)
        price = fetch_stooq_quote(stq_symbol)
        return {
            "source": "stooq",
            "asset_type": "index",
            "symbol": symbol,
            "stooq_symbol": stq_symbol,
            "price": price,
        }
    except Exception:
        pass

    return {"error": "Invalid index symbol", "symbol": symbol}
