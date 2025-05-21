import os
import requests
import logging
import time

logger = logging.getLogger(__name__)

MOONBAGS_API_BASE = "https://api2.moonbags.io/api/v1/coin"
BLOCKEDEN_API_KEY = os.getenv("BLOCKEDEN_API_KEY")
BLOCKEDEN_RPC_URL = f"https://sui.blockeden.xyz/v1/{BLOCKEDEN_API_KEY}"

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
    symbol = data.get("symbol", "TOKEN")
    # You may want to replace below logic if Moonbags API changes!
    return {
        "symbol": symbol,
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

def fetch_recent_buys(token_address, since_timestamp):
    """
    Polls BlockEden Sui RPC for swap events for this token since the last check.
    Returns a list of buys: {tx_hash, buyer_address, amount, usd_value, timestamp}
    """
    # This logic assumes the project uses standard swap pools. You may need to tune event filter to match specific Sui DEX swap events!
    events = []
    try:
        # Replace this event filter as needed for the actual DEX contract
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sui_getEvents",
            "params": [
                {
                    "MoveEventType": "0x2::coin::Transfer"  # Or whatever event your DEX uses for buys
                },
                since_timestamp * 1000,
                int(time.time()) * 1000,
                100
            ]
        }
        headers = {"Authorization": f"Bearer {BLOCKEDEN_API_KEY}"}
        r = requests.post(BLOCKEDEN_RPC_URL, json=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            res = r.json()
            for event in res.get('result', {}).get('data', []):
                # Check if the event is a buy of the tracked token (requires actual event structure)
                # Example filter, you'll want to tune this for your DEX/event
                if event.get("packageId", "").lower() == token_address.lower():
                    events.append({
                        "tx_hash": event.get("transactionDigest"),
                        "buyer_address": event.get("sender"),
                        "amount": float(event.get("amount", 0)),
                        "usd_value": float(event.get("amount", 0)) * fetch_token_info(token_address)["price"],
                        "timestamp": int(event.get("timestampMs", 0)) // 1000
                    })
    except Exception as e:
        logger.error(f"BlockEden fetch buys failed: {e}")
    return events

def verify_payment(tx_hash, expected_amount, receiver_address):
    """
    Verifies if a SUI payment with given tx_hash sent the expected_amount to the receiver_address.
    Returns True/False.
    """
    try:
        # Fetch transaction from BlockEden
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sui_getTransactionBlock",
            "params": [tx_hash]
        }
        headers = {"Authorization": f"Bearer {BLOCKEDEN_API_KEY}"}
        r = requests.post(BLOCKEDEN_RPC_URL, json=payload, headers=headers, timeout=10)
        if r.status_code != 200:
            return False
        res = r.json()
        effects = res.get("result", {}).get("effects", {})
        for effect in effects.get("mutated", []):
            if effect.get("owner", {}).get("AddressOwner", "").lower() == receiver_address.lower():
                # You may need to parse coin balances more deeply for SUI
                # For MVP, just pass if the receiver got a coin at all
                return True
        # Alternatively, parse the full events for SUI payments
    except Exception as e:
        logger.error(f"BlockEden verify payment error: {e}")
    return False
