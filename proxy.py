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
# BASIC UTILITIES
# -----------------------------------

def proxy_url(raw_url: str) -> str:
    """
    Wrap a raw external URL with AllOrigins so the request
    comes from AllOrigins, not Render's IP.
    """
    encoded = quote(raw_url, safe='')
    return f"https://api.allorigins.win/raw?url={encoded}"


@lru_cache(maxsize=256)
def cached_get(url: str):
    """
    Cached GET request. The URL passed in should already be
    an AllOrigins-proxied URL if hitting external services.
    """
    r = requests.get(url, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    return r.json()


# -----------------------------------
# ASSET TYPE DETECTION
# -----------------------------------

def detect_asset_type(symbol: str) -> str:
    """
    Detect basic asset type from the raw symbol.
    Returns one of: 'stock', 'etf', 'future', 'index'
    (For now, stock and etf are handled the same on the backend.)
    """
    s = symbol.upper()

    if s.startswith("^"):
        return "index"
    if "=" in s:
        return "future"
    # crude: treat everything else as stock/etf
    return "stock"


# -----------------------------------
# EXTERNAL DATA FETCHERS (PROXIED)
# -----------------------------------

def fetch_yahoo_quote(symbol: str) -> dict:
    """
    Fetch quote data from Yahoo Finance via AllOrigins proxy.
    Returns the first result dict for the symbol.
    """
    # Properly encode symbol, including ^ and =
    encoded_symbol = quote(symbol, safe='')
    yahoo_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={encoded_symbol}"
    proxied = proxy_url(yahoo_url)

    r = requests.get(proxied, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    result = data.get("quoteResponse", {}).get("result", [])
    if not result:
        raise Exception("No Yahoo data for symbol")
    return result[0]


def stooq_symbol_for_stock(ticker: str) -> str:
    """
    Stooq format for US stocks/ETFs:
    AAPL -> aapl.us, SPY -> spy.us
    """
    return ticker.lower() + ".us"


def stooq_symbol_for_future(symbol: str) -> str:
    """
    Stooq future symbols are often like:
    CL=F (Yahoo) -> cl.f (Stooq)
    ES=F -> es.f
    GC=F -> gc.f
    So we:
      - lowercase
      - drop '=f'
      - add '.f'
    """
    s = symbol.upper()
    base = s.replace("=F", "").lower()
    return base + ".f"


def stooq_symbol_for_index(symbol: str) -> str:
    """
    For indexes, we do aggressive mapping:
      ^GSPC -> ^gspc
      ^NDX  -> ^ndx
      ^DJI  -> ^dji
      ^ANY  -> ^any
    """
    return symbol.lower()


def fetch_stooq_quote(symbol: str) -> float:
    """
    Fetch quote from Stooq via AllOrigins proxy.

    Stooq returns JSON like: [{"symbol": "...", "close": "...", ...}]
    """
    base_url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=json"
    proxied = proxy_url(base_url)

    r = requests.get(proxied, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, list) or not data:
        raise Exception("No Stooq data")
    row = data[0]
    close = row.get("close")
    if not close or close == "N/A":
        raise Exception("Stooq close not available")
    return float(close)


def fetch_coingecko_price(coin_id: str) -> float:
    """
    Fetch crypto price (USD) from CoinGecko via AllOrigins.
    coin_id: e.g., 'bitcoin', 'ethereum'.
    """
    raw_url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
    proxied = proxy_url(raw_url)

    r = requests.get(proxied, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if coin_id not in data or "usd" not in data[coin_id]:
        raise Exception("No CoinGecko price")
    return float(data[coin_id]["usd"])


# -----------------------------------
# HEALTH ENDPOINT (for self-ping)
# -----------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# -----------------------------------
# UNIFIED STOCK/ETF/FUTURE/INDEX PRICE
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

    # 1) Try Yahoo via proxy for everything
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

    # 2) Stooq fallback depending on asset type
    try:
        if asset_type == "stock":
            stq_symbol = stooq_symbol_for_stock(symbol)
        elif asset_type == "future":
            stq_symbol = stooq_symbol_for_future(symbol)
        elif asset_type == "index":
            stq_symbol = stooq_symbol_for_index(symbol)
        else:
            # default to stock logic if somehow unknown
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
# LEGACY FUTURES ENDPOINT (wrapper over /price)
# -----------------------------------

@app.get("/futures/{symbol}")
def get_futures(symbol: str):
    """
    Thin wrapper around /price for futures.
    """
    data = get_price(symbol)
    data["endpoint"] = "futures"
    return data


# -----------------------------------
# LEGACY INDEX ENDPOINT (wrapper over /price)
# -----------------------------------

@app.get("/index/{symbol}")
def get_index(symbol: str):
    """
    Thin wrapper around /price for indexes.
    """
    data = get_price(symbol)
    data["endpoint"] = "index"
    return data


# -----------------------------------
# CRYPTO (CoinGecko via proxy)
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
    time.sleep(10)  # initial startup delay
    url = f"{SERVICE_BASE_URL}/health"

    while True:
        try:
            requests.get(url, timeout=ALLOWED_TIMEOUT)
        except Exception:
            # Ignore errors; next iteration will try again.
            pass
        time.sleep(SELF_PING_INTERVAL)


@app.on_event("startup")
def start_self_ping():
    """
    Start the self-ping loop in a background thread when the app starts.
    """
    t = threading.Thread(target=self_ping_loop, daemon=True)
    t.start()
