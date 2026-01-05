import requests
from fastapi import FastAPI
from functools import lru_cache
import time
from urllib.parse import quote_plus

app = FastAPI()

# -----------------------------------
# GLOBAL HEADERS
# -----------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# -----------------------------------
# BASIC CACHED GET
# -----------------------------------
@lru_cache(maxsize=256)
def cached_get(url: str):
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()

# -----------------------------------
# YAHOO VIA ALLORIGINS (PRIMARY)
# -----------------------------------
def fetch_yahoo_quote(symbol: str):
    """
    Fetch quote data from Yahoo Finance using AllOrigins proxy.
    This avoids Render IP rate limits.
    """
    yahoo_url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={quote_plus(symbol)}"
    proxied = f"https://api.allorigins.win/raw?url={quote_plus(yahoo_url)}"

    r = requests.get(proxied, headers=HEADERS, timeout=10)
    if r.status_code == 429:
        raise Exception("Yahoo rate limit via proxy")
    r.raise_for_status()
    data = r.json()
    result = data.get("quoteResponse", {}).get("result", [])
    if not result:
        raise Exception("No Yahoo data for symbol")
    return result[0]

# -----------------------------------
# STOOQ FALLBACK HELPERS
# -----------------------------------
def stooq_symbol_us(ticker: str) -> str:
    """
    Convert a US stock ticker into Stooq's expected format.
    Example: AAPL -> aapl.us
    """
    return ticker.lower() + ".us"

def fetch_stooq_quote(symbol: str):
    """
    Fetch quote from Stooq.
    Stooq returns JSON like: [{"symbol": "...", "close": "...", ...}]
    """
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=json"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or not data:
        raise Exception("No Stooq data")
    row = data[0]
    close = row.get("close")
    if not close or close == "N/A":
        raise Exception("Stooq close not available")
    return float(close)

# -----------------------------------
# STOCK PRICE (Yahoo → Stooq sequential)
# -----------------------------------
@app.get("/price/{ticker}")
def get_price(ticker: str):
    ticker = ticker.upper()

    # 1) Try Yahoo via AllOrigins
    try:
        q = fetch_yahoo_quote(ticker)
        price = q.get("regularMarketPrice")
        if price is not None:
            return {"source": "yahoo", "ticker": ticker, "price": price}
    except Exception as e:
        # print(e)  # you can log if needed
        pass

    # 2) Fallback to Stooq with .us suffix
    try:
        stq_symbol = stooq_symbol_us(ticker)
        price = fetch_stooq_quote(stq_symbol)
        return {"source": "stooq", "ticker": ticker, "price": price}
    except Exception as e:
        # print(e)
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
        if symbol in data and "usd" in data[symbol]:
            return {"source": "coingecko", "symbol": symbol.upper(), "price": data[symbol]["usd"]}
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Invalid crypto symbol"}

# -----------------------------------
# FUTURES (Yahoo → Stooq sequential, same pattern)
# Example symbols: CL=F, ES=F, GC=F
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

    # 2) Try Stooq (not all futures map cleanly; best-effort)
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

    # 2) Stooq often uses different codes for indexes; we try lowercase directly
    try:
        stq_symbol = symbol.lower()
        price = fetch_stooq_quote(stq_symbol)
        return {"source": "stooq", "symbol": symbol, "price": price}
    except Exception:
        pass

    return {"error": "Invalid index symbol or no data available"}
