from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
COINGECKO = "https://api.coingecko.com/api/v3/coins/"

def fetch_yahoo(ticker):
    url = f"{YAHOO}{ticker}?interval=1d&range=3mo"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()["chart"]["result"][0]

    timestamps = data.get("timestamp", [])
    closes = data["indicators"]["quote"][0]["close"]

    points = []
    for ts, c in zip(timestamps, closes):
        if c is None:
            continue
        points.append({
            "date": ts * 1000,
            "close": c
        })

    current = points[-1]["close"]
    prev = points[-2]["close"] if len(points) > 1 else current
    change = current - prev
    change_pct = (change / prev) * 100 if prev else 0

    prices = [p["close"] for p in points]
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    vol = (variance ** 0.5)
    vol_pct = (vol / mean) * 100 if mean else 0

    risk = "medium"
    if vol_pct < 1.5:
        risk = "low"
    elif vol_pct > 4:
        risk = "high"

    return {
        "ticker": ticker,
        "current": current,
        "change": change,
        "change_pct": change_pct,
        "vol_pct": vol_pct,
        "risk": risk,
        "history": points
    }

@app.get("/price/{ticker}")
def price(ticker: str):
    return fetch_yahoo(ticker.upper())

@app.get("/history/{ticker}")
def history(ticker: str):
    return fetch_yahoo(ticker.upper())["history"]

@app.get("/crypto/{cid}")
def crypto(cid: str):
    url = f"{COINGECKO}{cid}/market_chart?vs_currency=usd&days=90"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()

    prices = data["prices"]
    closes = [p[1] for p in prices]
    current = closes[-1]
    prev = closes[-2]
    change = current - prev
    change_pct = (change / prev) * 100

    return {
        "id": cid,
        "current": current,
        "change": change,
        "change_pct": change_pct,
        "history": [{"date": p[0], "close": p[1]} for p in prices]
    }
