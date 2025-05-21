import os
import requests
import logging
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

RAIDENX_BASE_URL = "https://api-public.raidenx.io/sui/defi/v3/token/market-data"

def fetch_token_info(token_address):
    try:
        url = f"{RAIDENX_BASE_URL}?address={token_address}"
        response = requests.get(url)
        if response.status_code != 200:
            logger.warning(f"[RaidenX] Error {response.status_code}: {response.text}")
            return default_info(token_address)

        data = response.json().get("data", {})
        if not data:
            logger.warning(f"[RaidenX] No data for {token_address}")
            return default_info(token_address)

        symbol = data.get("symbol", "TOKEN")
        logger.info(f"[RaidenX] Token: {symbol} | Address: {token_address}")

        return {
            "symbol": symbol,
            "name": data.get("name", "Unknown"),
            "price": float(data.get("price", 0)),
            "market_cap": float(data.get("marketCap", 0)),
            "liquidity": float(data.get("liquidity", 0)),
            "volume_24h": float(data.get("volume24h", 0)),
            "last_trade": int(data.get("updatedAt", 0))
        }
    except Exception as e:
        logger.error(f"[RaidenX] Exception in fetch_token_info: {e}")
        return default_info(token_address)

def default_info(token_address):
    return {
        "symbol": "TOKEN",
        "name": "Unknown",
        "price": 0,
        "market_cap": 0,
        "liquidity": 0,
        "volume_24h": 0,
        "last_trade": 0
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
            "tx_hash": "0x_simulated_raidenx",
            "buyer_address": "0x_unknown_wallet",
            "amount": 0,
            "usd_value": info["price"],
            "timestamp": int(info["last_trade"] / 1000)
        }]
    logger.info(f"[Buy Check] ❌ No new trade.")
    return []

def verify_payment(tx_hash, expected_amount, receiver_address):
    logger.warning("[RaidenX] Payment verification is not supported yet.")
    return True
