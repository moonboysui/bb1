import os
import requests
import logging
import time

# Logger setup
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MOONBAGS_API_BASE = "https://api2.moonbags.io/api/v1/coin"

def _make_request(token_address):
    try:
        url = f"{MOONBAGS_API_BASE}/{token_address}"
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        logger.warning(f"[Moonbags API] Error {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"[Moonbags API] Request failed: {e}")
    return None

def fetch_token_info(token_address):
    data = _make_request(token_address)
    if not data:
        logger.warning(f"[Moonbags] Failed to fetch token info for {token_address}")
        return {
            "symbol": "TOKEN",
            "name": "Unknown",
            "price": 0,
            "market_cap": 0,
            "liquidity": 0,
            "volume_24h": 0,
            "last_trade": 0,
        }

    symbol = data.get("symbol", "TOKEN")
    logger.info(f"[Moonbags] Token: {symbol} | Address: {token_address}")
    return {
        "symbol": symbol,
        "name": data.get("name", "Unknown"),
        "price": float(data.get("mcapUsd", 0)) / float(data.get("mcap", 1)) if data.get("mcap") else 0,
        "market_cap": float(data.get("mcapUsd", 0)),
        "liquidity": float(data.get("realSuiReserves", 0)) / 1_000_000,
        "volume_24h": float(data.get("volumeUsd24h", 0)),
        "last_trade": int(data.get("lastTrade", 0)),
    }

def get_token_symbol(token_address):
    info = fetch_token_info(token_address)
    return info.get("symbol", "TOKEN")

def fetch_recent_buys(token_address, since_timestamp):
    info = fetch_token_info(token_address)
    logger.info(f"[Buy Check] Token: {token_address}")
    logger.info(f"[Buy Check] LastTrade: {info['last_trade']} | Since: {since_timestamp * 1000}")
    if info["last_trade"] > since_timestamp * 1000:
        logger.info(f"[Buy Check] ✅ New trade detected!")
        return [{
            "tx_hash": "0x_simulated_tx",
            "buyer_address": "0x_simulated_wallet",
            "amount": 0,
            "usd_value": info["price"],
            "timestamp": int(info["last_trade"] / 1000)
        }]
    logger.info(f"[Buy Check] ❌ No new trade.")
    return []

def verify_payment(tx_hash, expected_amount, receiver_address):
    logger.warning("[Moonbags] Payment verification not implemented.")
    return True
