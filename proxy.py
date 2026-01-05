import requests
from fastapi import FastAPI
from functools import lru_cache
import time

app = FastAPI()

# -----------------------------------
# GLOBAL HEADERS (Fix #1: User-Agent)
# -----------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# -----------------------------------
# CACHED GET (prevents rate limits)
# -----------------------------------
@lru_cache(maxsize=256)
def cached_get(url):
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

# -----------------------------------
# RETRY WRAPPER (handles temporary 429)
# -----------------------------------
def fetch_with_retry(url, retries=3):
    for i in range(retries):
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()
        time.sleep(0.5 * (i + 1))  # exponential backoff
    raise Exception("Yahoo rate limit")

# -----------------------------------
# STOCK PRICE (Yahoo + Stooq fallback)
# -----------------------------------
@app.get("/price/{ticker}")
def get_price(ticker: str):
    ticker = ticker.upper()

    yahoo_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    stooq_url = f"https://stooq.com/q/l/?s={ticker.lower()}&f=sd2t2ohlcv&h&e=json"

    try:
        # Try Yahoo first
        data = fetch_with_retry(yahoo_url)
        result = data["quoteResponse"]["result"]
        if result:
            price = result[0].get("regularMarketPrice")
            if price is not None:
                return {"ticker": ticker, "price": price}
    except:
        pass  # Yahoo failed → fallback to Stooq

    # Fallback to Stooq
    try:
        data = requests.get(stooq_url, timeout=10).json()
        if isinstance(data, list) and len(data) > 0:
            close_price = data[0].get("close")
            if close_price not in (None, "N/A"):
                return {"ticker": ticker, "price": float(close_price)}
    except:
        pass

    return {"error": "Invalid ticker or no data available"}

# -----------------------------------
# CRYPTO (CoinGecko)
# -----------------------------------
@app.get("/crypto/{symbol}")
def get_crypto(symbol: str):
    symbol = symbol.lower()
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"

    try:
        data = cached_get(url)
        if symbol in data:
            return {"symbol": symbol.upper(), "price": data[symbol]["usd"]}
    except Exception as e:
        return {"error": str(e)}

    return {"error": "Invalid crypto symbol"}

# -----------------------------------
# FUTURES (Yahoo + Stooq fallback)
# Example: CL=F, ES=F, GC=F
# -----------------------------------
@app.get("/futures/{symbol}")
def get_futures(symbol: str):
    symbol = symbol.upper()

    yahoo_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
    stooq_url = f"https://stooq.com/q/l/?s={symbol.lower()}&f=sd2t2ohlcv&h&e=json"

    try:
        data = fetch_with_retry(yahoo_url)
        result = data["quoteResponse"]["result"]
        if result:
            price = result[0].get("regularMarketPrice")
            if price is not None:
                return {"symbol": symbol, "price": price}
    except:
        pass

    try:
        data = requests.get(stooq_url, timeout=10).json()
        if isinstance(data, list) and len(data) > 0:
            close_price = data[0].get("close")
            if close_price not in (None, "N/A"):
                return {"symbol": symbol, "price": float(close_price)}
    except:
        pass

    return {"error": "Invalid futures symbol"}

# -----------------------------------
# INDEXES (Yahoo + Stooq fallback)
# Example: ^GSPC, ^NDX, ^DJI
# -----------------------------------
@app.get("/index/{symbol}")
def get_index(symbol: str):
    symbol = symbol.upper()

    yahoo_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
    stooq_url = f"https://stooq.com/q/l/?s={symbol.lower()}&f=sd2t2ohlcv&h&e=json"

    try:
        data = fetch_with_retry(yahoo_url)
        result = data["quoteResponse"]["result"]
        if result:
            price = result[0].get("regularMarketPrice")
            if price is not None:
                return {"symbol": symbol, "price": price}
    except:
        pass

    try:
        data = requests.get(stooq_url, timeout=10).json()
        if isinstance(data, list) and len(data) > 0:
            close_price = data[0].get("close")
            if close_price not in (None, "N/A"):
                return {"symbol": symbol, "price": float(close_price)}
    except:
        pass

    return {"error": "Invalid index symbol"}
