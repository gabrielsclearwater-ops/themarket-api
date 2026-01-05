import requests
from urllib.parse import quote

# -----------------------------------
# CONFIG
# -----------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

TIMEOUT = 10  # seconds


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
# FUTURES / INDEX SYMBOL MAPPING
# -----------------------------------

def stooq_symbol_for_stock(ticker: str) -> str:
    return ticker.lower() + ".us"

def stooq_symbol_for_future(symbol: str) -> str:
    base = symbol.upper().replace("=F", "").lower()
    return base + ".f"

def stooq_symbol_for_index(symbol: str) -> str:
    return symbol.lower()


# -----------------------------------
# YAHOO CHART API (PRIMARY SOURCE)
# -----------------------------------

def fetch_yahoo_chart(symbol: str) -> dict:
    """
    Fetch full chart data from Yahoo Finance:
    - regularMarketPrice
    - OHLC arrays
    - volume
    - timestamps
    - metadata (exchange, currency, marketState)
    """
    encoded = quote(symbol, safe="")
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/"
        f"{encoded}?interval=1d&range=3mo"
    )

    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    result = data.get("chart", {}).get("result")
    if not result:
        raise Exception("No Yahoo Chart data")

    chart = result[0]
    meta = chart.get("meta", {})
    indicators = chart.get("indicators", {})
    quote_block = indicators.get("quote", [{}])[0]

    return {
        "source": "yahoo_chart",
        "symbol": symbol.upper(),
        "regularMarketPrice": meta.get("regularMarketPrice"),
        "previousClose": meta.get("previousClose"),
        "exchangeName": meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "marketState": meta.get("marketState"),

        # 90-day history
        "timestamps": chart.get("timestamp", []),

        # OHLC + volume arrays
        "open": quote_block.get("open", []),
        "high": quote_block.get("high", []),
        "low": quote_block.get("low", []),
        "close": quote_block.get("close", []),
        "volume": quote_block.get("volume", []),
    }


# -----------------------------------
# STOOQ FALLBACK (SECONDARY SOURCE)
# -----------------------------------

def fetch_stooq_quote(symbol: str) -> float:
    """
    Fetch last close price from Stooq.
    """
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=json"

    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, list) or not data:
        raise Exception("Invalid Stooq response")

    row = data[0]
    close = row.get("close")
    if not close or close == "N/A":
        raise Exception("Stooq close unavailable")

    return float(close)


# -----------------------------------
# CRYPTO (COINGECKO)
# -----------------------------------

def fetch_crypto_price(symbol: str) -> dict:
    """
    Fetch crypto price from CoinGecko.
    """
    coin_id = symbol.lower()
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={coin_id}&vs_currencies=usd"
    )

    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if coin_id not in data or "usd" not in data[coin_id]:
        raise Exception("No CoinGecko price")

    return {
        "source": "coingecko",
        "symbol": symbol.upper(),
        "price": float(data[coin_id]["usd"]),
    }
