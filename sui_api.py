import os
import requests
import logging
import time

logger = logging.getLogger(__name__)

MOONBAGS_API_BASE = "https://api2.moonbags.io/api/v1/coin"
BLOCKEDEN_RPC_URL = os.getenv("BLOCKEDEN_RPC_URL")
BLOCKEDEN_RPC_KEY = os.getenv("BLOCKEDEN_RPC_KEY")

def _moonbags_request(token_address):
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
    data = _moonbags_request(token_address)
    if not data:
        return {
            "symbol": "TOKEN",
            "name": "Unknown",
            "price": 0,
            "market_cap": 0,
            "liquidity": 0,
            "volume_24h": 0,
            "last_trade": 0,
        }
    return {
        "symbol": data.get("symbol", "TOKEN"),
        "name": data.get("name", "Unknown"),
        "price": float(data.get("price", 0)),
        "market_cap": float(data.get("mcapUsd", 0)),
        "liquidity": float(data.get("realSuiReserves", 0)),
        "volume_24h": float(data.get("volumeUsd24h", 0)),
        "last_trade": int(data.get("lastTrade", 0)),
    }

def get_token_symbol(token_address):
    info = fetch_token_info(token_address)
    return info.get("symbol", "TOKEN")

def verify_payment(tx_hash, expected_amount, receiver_address):
    # This should be customized for SUI coin transfers via BlockEden RPC.
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sui_getTransactionBlock",
            "params": [tx_hash]
        }
        headers = {"Authorization": f"Bearer {BLOCKEDEN_RPC_KEY}"}
        r = requests.post(BLOCKEDEN_RPC_URL, json=payload, headers=headers, timeout=10)
        if r.status_code != 200:
            return False
        res = r.json()
        # Custom: Parse the transaction for a SUI coin transfer to receiver_address
        # Implement your logic here
        # For now, just return True for demo
        return True
    except Exception as e:
        logger.error(f"BlockEden verify payment error: {e}")
    return False
