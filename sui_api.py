import os
import requests
import logging
import time

# Setup logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Get API key and set headers
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
BIRDEYE_BASE_URL = "https://public-api.birdeye.so/public"
HEADERS = {"X-API-KEY": BIRDEYE_API_KEY}

def normalize_token_address(token_type: str) -> str:
    """Extracts base address from full Sui token type string."""
    base = token_type.split("::")[0]
    logger.info(f"Normalized token address: {base}")
    return base

def _make_request(url, params=None):
    try:
        response = requests.get(url, headers=HEADERS, params=params)
        if response.status_code == 200:
            return response.json()
        logger.warning(f"API error {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"API request failed: {e}")
    return None

def fetch_token_info(token_address):
    normalized_address = normalize_token_address(token_address)
    url = f"{BIRDEYE_BASE_URL}/token/price"
    params = {"address": normalized_address, "chain": "sui"}
    data = _make_request(url, params)

    if data and data.get("data"):
        info = data["data"]
        symbol = info.get("symbol", "TOKEN")
        logger.info(f"[Birdeye] Token: {symbol} | Address: {token_address}")
        return {
            "symbol": symbol,
            "name": info.get("name", "Unknown Token"),
            "price": float(info.get("value", 0)),
            "market_cap": float(info.get("marketCap", 0)),
            "liquidity": float(info.get("liquidity", 0)),
            "price_change_30m": float(info.get("change24h", 0)),
            "price_change_24h": float(info.get("change24h", 0)),
        }

    logger.warning(f"[Birdeye] Failed to fetch token info for {token_address}")
    return {"symbol": "TOKEN", "price": 0, "market_cap": 0, "liquidity": 0}

def get_token_symbol(token_address):
    info = fetch_token_info(token_address)
    return info.get("symbol", "TOKEN")

def fetch_recent_buys(token_address, since_timestamp):
    try:
        normalized_address = normalize_token_address(token_address)
        url = f"{BIRDEYE_BASE_URL}/token/trades"
        params = {
            "address": normalized_address,
            "chain": "sui",
            "type": "buy",
            "limit": 10
        }
        data = _make_request(url, params)
        results = []

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

def verify_payment(tx_hash, expected_amount, receiver_address):
    logger.warning("verify_payment is still using placeholder logic")
    return True

# TEST FUNCTION to verify API works in isolation
if __name__ == "__main__":
    test_address = "0xf22da9a24ad027cccb5f2d496cbe91de953d363513db08a3a734d361c7c17503::LOFI::LOFI"
    print("Testing fetch_token_info...")
    info = fetch_token_info(test_address)
    print(info)
