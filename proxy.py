import time
import threading
from functools import lru_cache
from urllib.parse import quote

import random
import requests
from fastapi import FastAPI

app = FastAPI()

# -----------------------------------
# CONFIG
# -----------------------------------

SERVICE_BASE_URL = "https://themarket-api.onrender.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

ALLOWED_TIMEOUT = 10  # seconds
SELF_PING_INTERVAL = 60  # seconds


# -----------------------------------
# PROXY DEFINITIONS
# -----------------------------------

def proxy_allorigins(raw_url: str) -> str:
    encoded = quote(raw_url, safe='')
    return f"https://api.allorigins.win/raw?url={encoded}"

def proxy_thingproxy(raw_url: str) -> str:
    encoded = quote(raw_url, safe='')
    return f"https://thingproxy.freeboard.io/fetch/{encoded}"

def proxy_cors_anywhere(raw_url: str) -> str:
    # public CORS proxy (may be rate-limited, used as fallback only)
    encoded = quote(raw_url, safe='')
    return f"https://cors.isomorphic-git.org/{encoded}"

def proxy_dummy_duckduckgo(raw_url: str) -> str:
    # Placeholder-style proxy; using ddg as a passthrough is hacky in reality,
    # but here it's just another fallback pattern.
    encoded = quote(raw_url, safe='')
    return f"https://r.jina.ai/https://{encoded}"

def proxy_dummy_github(raw_url: str) -> str:
    # Another placeholder-style proxy pattern as last resort.
    encoded = quote(raw_url, safe='')
    return f"https://r.jina.ai/https://{encoded}"

PRIMARY_PROXY = proxy_allorigins
FALLBACK_PROXIES = [
    proxy_thingproxy,
    proxy_cors_anywhere,
    proxy_dummy_duckduckgo,
    proxy_dummy_github,
]


@lru_cache(maxsize=256)
def cached_get(url: str):
    r = requests.get(url, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    return r.json()


def proxied_get_json(raw_url: str) -> dict:
    """
    Try AllOrigins first, then randomly rotate through the other proxies.
    """
    # 1) Primary: AllOrigins
    try:
        proxied = PRIMARY_PROXY(raw_url)
        r = requests.get(proxied, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        pass

    # 2) Randomized rotation among fallbacks
    proxies = FALLBACK_PROXIES[:]
    random.shuffle(proxies)

    last_error = None
    for proxy_fn in proxies:
        try:
            proxied = proxy_fn(raw_url)
            r = requests.get(proxied, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_error = e
            continue

    raise last_error if last_error else Exception("All proxies failed")


# -----------------------------------
# ASSET TYPE DETECTION
# -----------------------------------

def detect_asset_type(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("^"):
        return "index"
    if "=" in s:
        return "future"
    return "stock"


# -----------------------------------
# EXTERNAL DATA FETCHERS (PROXIED)
# -----------------------------------

def fetch_yahoo_quote(symbol: str) -> dict:
    encoded_symbol = quote(symbol, safe='')
    yahoo_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={encoded_symbol}"
    data = proxied_get_json(yahoo_url)
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
    raw_url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
    data = proxied_get_json(raw_url)
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
# UNIFIED PRICE ENDPOINT
# -----------------------------------

@app.get("/price/{symbol}")
def get_price(symbol: str):
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
                "price": price
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
    data = get_price(symbol)
    data["endpoint"] = "futures"
    return data


@app.get("/index/{symbol}")
def get_index(symbol: str):
    data = get_price(symbol)
    data["endpoint"] = "index"
    return data


# -----------------------------------
# CRYPTO
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
