import time
import threading
from functools import lru_cache
from urllib.parse import quote_plus

import requests
from fastapi import FastAPI

app = FastAPI()

# -----------------------------------
# CONFIG
# -----------------------------------

# Change this if your service URL changes
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
    encoded = quote_plus(raw_url)
    return f"https://api.allorigins.win/raw?url={encoded}"


@lru_cache(maxsize=256)
def cached_get(url: str):
    """
    Cached GET request through AllOrigins.
    """
    r = requests.get(url, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    return r.json()


# -----------------------------------
# EXTERNAL DATA FETCHERS (PROXIED)
# -----------------------------------

def fetch_yahoo_quote(symbol: str) -> dict:
    """
    Fetch quote data from Yahoo Finance via AllOrigins proxy.
    Returns the first result dict for the symbol.
    """
    yahoo_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={quote_plus(symbol)}"
    proxied = proxy_url(yahoo_url)

    r = requests.get(proxied, headers=HEADERS, timeout=ALLOWED_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    result = data.get("quoteResponse", {}).get("result", [])
    if not result:
        raise Exception("No Yahoo data for symbol")
    return result[0]


def stooq_symbol_us(ticker: str) -> str:
    """
    Convert a US stock ticker into Stooq's expected format.
    Example: AAPL -> aapl.us
    """
    return ticker.lower() + ".us"


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
# STOCK PRICE (Yahoo → Stooq sequential)
# -----------------------------------

@app.get("/price/{ticker}")
def get_price(ticker: str):
    ticker = ticker.upper()

    # 1) Try Yahoo via proxy
    try:
        q = fetch_yahoo_quote(ticker)
        price = q.get("regularMarketPrice")
        if price is not None:
            return {"source": "yahoo", "ticker": ticker, "price": price}
    except Exception:
        pass

    # 2) Fallback to Stooq (.us)
    try:
        stq_symbol = stooq_symbol_us(ticker)
        price = fetch_stooq_quote(stq_symbol)
        return {"source": "stooq", "ticker": ticker, "price": price}
    except Exception:
        pass

    return {"error": "Invalid ticker or no data available"}


# -----------------------------------
# CRYPTO (CoinGecko via proxy)
# -----------------------------------

@app.get("/crypto/{symbol}")
def get_crypto(symbol: str):
    coin_id = symbol.lower()
    try:
        # using cached_get around the proxied URL for CoinGecko
        raw_url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        proxied = proxy_url(raw_url)
        data = cached_get(proxied)
        if coin_id in data and "usd" in data[coin_id]:
            return {
                "source": "coingecko",
                "symbol": coin_id.upper(),
                "price": float(data[coin_id]["usd"])
            }
    except Exception as e:
        return {"error": str(e)}

    return {"error": "Invalid crypto symbol or no data available"}


# -----------------------------------
# FUTURES (Yahoo → Stooq sequential)
# Example: CL=F, ES=F, GC=F
# -----------------------------------

@app.get("/futures/{symbol}")
def get_futures(symbol: str):
    symbol = symbol.upper()

    # 1) Try Yahoo
    try:
        q = fetch_yahoo_quote(symbol)
        price = q.get("regularMarketPrice")
        if price is not None:
            return {"source": "yahoo", "symbol": symbol, "price": price}
    except Exception:
        pass

    # 2) Try Stooq directly with lowercase symbol
    try:
        stq_symbol = symbol.lower()
        price = fetch_stooq_quote(stq_symbol)
        return {"source": "stooq", "symbol": symbol, "price": price}
    except Exception:
        pass

    return {"error": "Invalid futures symbol or no data available"}


# -----------------------------------
# INDEXES (Yahoo → Stooq sequential)
# Example: ^GSPC, ^NDX, ^DJI
# -----------------------------------

@app.get("/index/{symbol}")
def get_index(symbol: str):
    symbol = symbol.upper()

    # 1) Try Yahoo
    try:
        q = fetch_yahoo_quote(symbol)
        price = q.get("regularMarketPrice")
        if price is not None:
            return {"source": "yahoo", "symbol": symbol, "price": price}
    except Exception:
        pass

    # 2) Try Stooq with lowercase symbol
    try:
        stq_symbol = symbol.lower()
        price = fetch_stooq_quote(stq_symbol)
        return {"source": "stooq", "symbol": symbol, "price": price}
    except Exception:
        pass

    return {"error": "Invalid index symbol or no data available"}


# -----------------------------------
# SELF-PING BACKGROUND TASK
# -----------------------------------

def self_ping_loop():
    """
    Background loop to keep the Render service awake.
    Pings the /health endpoint every SELF_PING_INTERVAL seconds.
    """
    # Small initial delay to let the server fully start
    time.sleep(10)
    url = f"{SERVICE_BASE_URL}/health"

    while True:
        try:
            requests.get(url, timeout=ALLOWED_TIMEOUT)
        except Exception:
            # We silently ignore errors here; next iteration will try again.
            pass
        time.sleep(SELF_PING_INTERVAL)


@app.on_event("startup")
def start_self_ping():
    """
    Start the self-ping loop in a background thread when the app starts.
    """
    t = threading.Thread(target=self_ping_loop, daemon=True)
    t.start()
