import time
import threading
from functools import lru_cache
from urllib.parse import quote

import requests
from fastapi import FastAPI

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


# -----------------------------------
# PROXY LAYER (2-PROXY ROTATION)
# -----------------------------------

def proxy_allorigins(raw_url: str) -> str:
    """
    Proxy via AllOrigins (primary).
    """
    encoded = quote(raw_url, safe='')
    return f"https://api.allorigins.win/raw?url={encoded}"


def proxy_thingproxy(raw_url: str) -> str:
    """
    Proxy via ThingProxy (fallback).
    """
    encoded = quote(raw_url, safe='')
    return f"https://thingproxy.freeboard.io/fetch/{encoded}"


PROXY_CHAIN = [proxy_allorigins, proxy_thingproxy]


def proxied_get_json(raw_url: str) -> dict:
    """
    Try AllOrigins first, then ThingProxy.
    Return JSON or raise the last error.
    """
    last_error = None

    for proxy_fn in PROXY_CHAIN:
        try:
            proxied_url = proxy_fn(raw_url)
            r = requests.get(proxied_url, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_error = e
            continue

    raise last_error if last_error else Exception("All proxies failed")


@lru_cache(maxsize=256)
def cached_proxied_get_json(raw_url: str) -> dict:
    """
    Cached version of proxied_get_json (cache keyed by the raw URL).
    """
    return proxied_get_json(raw_url)


# -----------------------------------
# ASSET TYPE DETECTION
# -----------------------------------

def detect_asset_type(symbol: str) -> str:
    """
    Classify the symbol into a broad asset type.
    """
    s = symbol.upper()

    if s.startswith("^"):
        return "index"
    if "=" in s:
        return "future"
    return "stock"  # stocks + ETFs treated the same here


# -----------------------------------
# EXTERNAL FETCHERS (YAHOO, STOOQ, COINGECKO)
# -----------------------------------

def fetch_yahoo_quote(symbol: str) -> dict:
    """
    Fetch quote data from Yahoo Finance via proxy.
    Returns the first result dict for the symbol.
    """
    encoded_symbol = quote(symbol, safe='')
    yahoo_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={encoded_symbol}"
    data = proxied_get_json(yahoo_url)
    result = data.get("quoteResponse", {}).get("result", [])
    if not result:
        raise Exception("No Yahoo data for symbol")
    return result[0]


def stooq_symbol_for_stock(ticker: str) -> str:
    """
    Stooq format for US stocks/ETFs: AAPL -> aapl.us, SPY -> spy.us
    """
    return ticker.lower() + ".us"


def stooq_symbol_for_future(symbol: str) -> str:
    """
    Stooq futures mapping:
      CL=F (Yahoo) -> cl.f
      ES=F -> es.f
      GC=F -> gc.f
    """
    s = symbol.upper()
    base = s.replace("=F", "").lower()
    return base + ".f"


def stooq_symbol_for_index(symbol: str) -> str:
    """
    Aggressive index mapping:
      ^GSPC -> ^gspc
      ^NDX  -> ^ndx
      ^DJI  -> ^dji
      ^RUT  -> ^rut
    """
    return symbol.lower()


def fetch_stooq_quote(symbol: str) -> float:
    """
    Fetch quote from Stooq via proxy.
    Stooq returns JSON like: [{"symbol": "...", "close": "...", ...}]
    """
    base_url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=json"
    data = proxied_get_json(base_url)

    if not isinstance(data, list) or not data:
        raise Exception("No Stooq data")
    row = data[0]
    close = row.get("close")
    if not close or close == "N/A":
        raise Exception("Stooq close not available")
    return float(close)


def fetch_coingecko_price(coin_id: str) -> float:
    """
    Fetch crypto price (USD) from CoinGecko via proxy.
    """
    raw_url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
    data = cached_proxied_get_json(raw_url)
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

    # 1) Yahoo via proxy
    try:
        q = fetch_yahoo_quote(symbol)
        price = q.get("regularMarketPrice")
        if price is not None:
            return {
                "source": "yahoo",
                "asset_type": asset_type,
                "symbol": symbol,
                "price": price
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
            "price": price
        }
    except Exception:
        pass

    return {
        "error": "Invalid symbol or no data available",
        "symbol": raw_symbol,
        "asset_type": asset_type
    }


# -----------------------------------
# FUTURES / INDEX WRAPPERS
# -----------------------------------

@app.get("/futures/{symbol}")
def get_futures(symbol: str):
    """
    Thin wrapper around /price for futures.
    """
    data = get_price(symbol)
    data["endpoint"] = "futures"
    return data


@app.get("/index/{symbol}")
def get_index(symbol: str):
    """
    Thin wrapper around /price for indexes.
    """
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
            "price": price
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# -----------------------------------
# SELF-PING BACKGROUND TASK
# -----------------------------------

def self_ping_loop():
    """
    Background loop to keep the Render service awake.
    Pings the /health endpoint every SELF_PING_INTERVAL seconds.
    """
    time.sleep(10)  # initial delay to let the app fully start
    url = f"{SERVICE_BASE_URL}/health"

    while True:
        try:
            requests.get(url, timeout=ALLOWED_TIMEOUT)
        except Exception:
            # Ignore errors; next iteration will try again
            pass
        time.sleep(SELF_PING_INTERVAL)


@app.on_event("startup")
def start_self_ping():
    """
    Start the self-ping loop in a background thread when the app starts.
    """
    t = threading.Thread(target=self_ping_loop, daemon=True)
    t.start()
