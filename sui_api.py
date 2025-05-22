import os
import requests
import logging

# Logger setup
logger = logging.getLogger(__name__)

# Moonbags API base URL for token data (price, market cap, etc.)
MOONBAGS_API_BASE = "https://api2.moonbags.io/api/v1/coin"

def _make_request(token_address):
    """Internal helper to call the Moonbags API and return JSON data."""
    try:
        url = f"{MOONBAGS_API_BASE}/{token_address}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            logger.warning(f"[Moonbags API] Error {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"[Moonbags API] Request failed: {e}")
    return None

def fetch_token_info(token_address):
    """Fetch token information (symbol, price, market cap, liquidity, etc.) using Moonbags API."""
    data = _make_request(token_address)
    if not data:
        logger.warning(f"[Moonbags] Failed to fetch token info for {token_address}")
        return {
            "symbol": "TOKEN",
            "name": "Unknown",
            "price": 0.0,
            "market_cap": 0.0,
            "liquidity": 0.0,
            "volume_24h": 0.0,
            "last_trade": 0
        }
    symbol = data.get("symbol", "TOKEN")
    logger.info(f"[Moonbags] Token: {symbol} | Address: {token_address}")
    return {
        "symbol": symbol,
        "name": data.get("name", "Unknown"),
        # price is calculated as market_cap_usd / supply (mcap)
        "price": float(data.get("mcapUsd", 0)) / float(data.get("mcap", 1)) if data.get("mcap") else 0.0,
        "market_cap": float(data.get("mcapUsd", 0)),
        # Convert liquidity from sui units to SUI (assuming realSuiReserves is in micro SUI or similar)
        "liquidity": float(data.get("realSuiReserves", 0)) / 1_000_000,
        "volume_24h": float(data.get("volumeUsd24h", 0)),
        "last_trade": int(data.get("lastTrade", 0))
        # Note: If the API provided short-term price change (e.g., 30m), it could be added here as "price_change_30m"
    }

def get_token_symbol(token_address):
    """Convenience function to get the token's symbol."""
    info = fetch_token_info(token_address)
    return info.get("symbol", "TOKEN")

def fetch_recent_buys(token_address, since_timestamp):
    """
    Fallback: Return a recent buy if the last trade timestamp from API is newer than the given timestamp.
    This uses Moonbags API last_trade as an approximation for recent activity.
    """
    info = fetch_token_info(token_address)
    # Moonbags 'last_trade' is in milliseconds; compare with since_timestamp (in seconds)
    if info.get("last_trade", 0) > since_timestamp * 1000:
        return [{
            "tx_hash": "0x_simulated_tx",
            "buyer_address": "0x_simulated_wallet",
            "amount": 0,
            "usd_value": info.get("price", 0.0),
            "timestamp": int(info.get("last_trade", 0) / 1000)
        }]
    return []

async def verify_payment(tx_hash, expected_amount, receiver_address):
    """
    Verify that a transaction with hash `tx_hash` sent `expected_amount` SUI to `receiver_address`.
    (This is a placeholder implementation – an actual implementation would query the blockchain.)
    """
    logger.warning("[Moonbags] Payment verification not implemented; assuming success for testing.")
    # TODO: Implement actual on-chain verification via BlockEden HTTP API or Sui RPC.
    return True
