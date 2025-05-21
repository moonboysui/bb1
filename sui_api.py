# ✅ This file replaces `sui_api.py` with real API logic using Birdeye for Sui token tracking.
# 📌 Assumes you sign up and get a free Birdeye API key.

import requests
import logging
import time

# Get logger
logger = logging.getLogger(__name__)

# Replace this with your actual API key from Birdeye
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
BIRDEYE_BASE_URL = "https://public-api.birdeye.so/public"

HEADERS = {"X-API-KEY": BIRDEYE_API_KEY}


def _make_request(url, params=None):
    try:
        response = requests.get(url, headers=HEADERS, params=params)
        if response.status_code == 200:
            return response.json()
        logger.warning(f"API error {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"API request failed: {e}")
    return None


async def fetch_token_info(token_address):
    """Fetch real token info from Birdeye."""
    url = f"{BIRDEYE_BASE_URL}/token/price"
    params = {"address": token_address, "chain": "sui"}
    data = _make_request(url, params)

    if data and data.get("data"):
        info = data["data"]
        return {
            "symbol": info.get("symbol", "TOKEN"),
            "name": info.get("name", "Unknown Token"),
            "price": float(info.get("value", 0)),
            "market_cap": float(info.get("marketCap", 0)),
            "liquidity": float(info.get("liquidity", 0)),
            "price_change_30m": float(info.get("change24h", 0)),  # Placeholder if no 30m field
            "price_change_24h": float(info.get("change24h", 0)),
        }

    return {"symbol": "TOKEN", "price": 0, "market_cap": 0, "liquidity": 0}


async def get_token_symbol(token_address):
    info = await fetch_token_info(token_address)
    return info.get("symbol", "TOKEN")


async def fetch_recent_buys(token_address, since_timestamp):
    """Fetch recent buys from Birdeye"""
    try:
        url = f"{BIRDEYE_BASE_URL}/token/trades"
        params = {
            "address": token_address,
            "chain": "sui",
            "type": "buy",
            "limit": 10
        }
        data = _make_request(url, params)
        results = []
        now = int(time.time())

        if data and data.get("data"):
            for tx in data["data"]:
                ts = int(tx.get("timestamp", 0))
                if ts > since_timestamp:
                    results.append({
                        "tx_hash": tx.get("txHash", "0x_unknown"),
                        "buyer_address": tx.get("source", "0x_unknown"),
                        "amount": float(tx.get("amount", 0)),
                        "usd_value": float(tx.get("valueUsd", 0)),
                        "timestamp": ts
                    })

        return results
    except Exception as e:
        logger.error(f"Failed to fetch buys: {e}")
        return []


async def verify_payment(tx_hash, expected_amount, receiver_address):
    """Still using old logic. Adjust if Birdeye supports direct transaction lookup."""
    logger.warning("verify_payment is still using placeholder logic")
    return True  # Always approve for now (or integrate a future transaction checker)
