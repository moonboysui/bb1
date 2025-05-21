import os
import requests
import logging

logger = logging.getLogger(__name__)

MOONBAGS_API_BASE = "https://api2.moonbags.io/api/v1/coin"
BLOCKEDEN_RPC_URL = os.getenv("BLOCKEDEN_RPC_URL")
BLOCKEDEN_RPC_KEY = os.getenv("BLOCKEDEN_RPC_KEY")

def fetch_token_info(token_address):
    # Try moonbags first
    try:
        url = f"{MOONBAGS_API_BASE}/{token_address}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                "symbol": data.get("symbol", "TOKEN"),
                "name": data.get("name", "Unknown"),
                "price": float(data.get("price", 0)),
                "market_cap": float(data.get("mcapUsd", 0)),
                "liquidity": float(data.get("realSuiReserves", 0)),
                "volume_24h": float(data.get("volumeUsd24h", 0)),
                "last_trade": int(data.get("lastTrade", 0)),
                "sui_price": float(data.get("suiPrice", 0))
            }
    except Exception as e:
        logger.error(f"[Moonbags API] Request failed: {e}")
    # Fallback
    return {
        "symbol": "TOKEN", "name": "Unknown", "price": 0,
        "market_cap": 0, "liquidity": 0, "volume_24h": 0, "last_trade": 0, "sui_price": 0
    }

def get_token_symbol(token_address):
    info = fetch_token_info(token_address)
    return info.get("symbol", "TOKEN")
