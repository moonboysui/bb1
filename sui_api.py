import os
import logging
import requests
import aiohttp

logger = logging.getLogger(__name__)

# Moonbags API base URL for token info
MOONBAGS_API_BASE = "https://api2.moonbags.io/api/v1/coin"
# Sui RPC endpoint for on-chain queries (public mainnet fullnode)
RPC_URL = os.getenv("RPC_URL", "https://fullnode.mainnet.sui.io:443")

def _make_request(token_address: str):
    """Helper to fetch JSON data from Moonbags API for a given token."""
    try:
        url = f"{MOONBAGS_API_BASE}/{token_address}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            return response.json()
        logger.warning(f"[Moonbags API] HTTP {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"[Moonbags API] Request failed for {token_address}: {e}")
    return None

def fetch_token_info(token_address: str) -> dict:
    """Fetch token stats (price, market cap, liquidity, etc.) from Moonbags API."""
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
            "price_change_30m": 0.0
        }
    symbol = data.get("symbol", "TOKEN")
    # Calculate price from market cap if available (price = mcapUsd / total_supply)
    price = 0.0
    try:
        mcap_usd = float(data.get("mcapUsd", 0))
        mcap_coins = float(data.get("mcap", 0))  # possibly circulating supply
        price = (mcap_usd / mcap_coins) if mcap_coins else 0.0
    except Exception as e:
        logger.error(f"[Moonbags] Error calculating price for {token_address}: {e}")
    info = {
        "symbol": symbol,
        "name": data.get("name", "Unknown"),
        "price": price,
        "market_cap": float(data.get("mcapUsd", 0)),
        "liquidity": float(data.get("realSuiReserves", 0)) / 1_000_000,  # convert reserves to SUI units
        "volume_24h": float(data.get("volumeUsd24h", 0)),
        "price_change_30m": float(data.get("priceChange30m", 0))
    }
    return info

def get_token_symbol(token_address: str) -> str:
    """Convenience to get a token's symbol."""
    info = fetch_token_info(token_address)
    return info.get("symbol", "TOKEN")

def fetch_recent_buys(token_address: str, since_timestamp: int) -> list:
    """
    Polling helper: returns a simulated 'buy' if the token's last trade is newer than since_timestamp.
    This is a fallback for environments where WebSocket is unavailable.
    """
    info = fetch_token_info(token_address)
    # Some API data may include last trade timestamp (in milliseconds)
    last_trade_ms = int(info.get("last_trade", info.get("lastTrade", 0)))
    if last_trade_ms and last_trade_ms > since_timestamp * 1000:
        # Create a dummy buy event using the latest price as usd_value
        return [{
            "tx_hash": "0x_simulated_tx",
            "buyer_address": "0x_simulated_buyer",
            "amount": 0.0,
            "usd_value": info["price"],
            "timestamp": int(last_trade_ms / 1000)
        }]
    return []

async def verify_payment(tx_hash: str, expected_amount: float, receiver_address: str) -> bool:
    """
    Verify on-chain that transaction `tx_hash` sent exactly `expected_amount` SUI to `receiver_address`.
    Returns True if the payment is confirmed, False otherwise.
    """
    try:
        # Build JSON-RPC request for transaction details including balance changes
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sui_getTransactionBlock",
            "params": [
                tx_hash,
                {"showBalanceChanges": True}
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(RPC_URL, json=payload) as resp:
                if resp.status != 200:
                    logger.warning(f"RPC call failed with status {resp.status}")
                    return False
                data = await resp.json()
        if data.get("error"):
            logger.warning(f"RPC error for {tx_hash}: {data['error']}")
            return False
        result = data.get("result")
        if not result:
            logger.warning(f"No transaction data for {tx_hash}")
            return False
        balance_changes = result.get("balanceChanges", [])
        expected_base = int(expected_amount * (10**9))  # convert SUI amount to base units (10^9)
        for change in balance_changes:
            try:
                owner = change.get("owner")
                coin_type = change.get("coinType", "")
                amount = int(change.get("amount", 0))
            except Exception as e:
                logger.error(f"Error parsing balance change: {e}")
                continue
            # If owner is an address (might be nested in dict) and coin is SUI
            if isinstance(owner, dict):
                owner_addr = owner.get("AddressOwner") or owner.get("address") or ""
            else:
                owner_addr = str(owner)
            if coin_type.endswith("::sui::SUI") and owner_addr.lower() == receiver_address.lower():
                if amount == expected_base:
                    return True
        return False
    except Exception as e:
        logger.error(f"Exception during payment verification: {e}")
        return False
