import os
import requests
import logging
import json
import time
import threading
import websocket # Will be installed via requirements.txt

logger = logging.getLogger(__name__)
# Basic logging setup for file, consider more advanced setup in bot.py
logging.basicConfig(level=logging.INFO)

RAIDENX_BASE_URL = "https://api-public.raidenx.io/sui/defi/v3/token/market-data"

# --- Configuration for Sui RPC (for real-time buys and payment verification) ---
# You MUST replace this with a real Sui RPC WebSocket URL from a provider like Alchemy or BlockEden.xyz
# Example: "wss://sui-mainnet.alchemyapi.io/v2/YOUR_ALCHEMY_API_KEY"
# Example: "wss://fullnode.sui.io:443" (public, but rate-limited)
SUI_RPC_WEBSOCKET_URL = os.getenv("SUI_RPC_WEBSOCKET_URL", "wss://fullnode.sui.io:443")
SUI_RPC_HTTP_URL = os.getenv("SUI_RPC_HTTP_URL", "https://fullnode.sui.io:443") # For verify_payment

# --- Global queue for incoming buy events ---
# This list will store parsed buy events from the WebSocket listener
# bot.py will consume from this queue.
_buy_event_queue = []
_queue_lock = threading.Lock()

# --- Market Data (from RaidenX) ---
def fetch_token_info(token_address):
    """
    Fetches market information for a given token address from RaidenX.
    This provides current price, market cap, liquidity, volume, and last updated time.
    """
    try:
        url = f"{RAIDENX_BASE_URL}?address={token_address}"
        response = requests.get(url, timeout=10) # Added timeout
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)

        data = response.json().get("data", {})
        if not data:
            logger.warning(f"[RaidenX] No data for {token_address}")
            return default_info(token_address)

        symbol = data.get("symbol", "TOKEN")
        logger.info(f"[RaidenX] Fetched: {symbol} | Address: {token_address}")

        return {
            "symbol": symbol,
            "name": data.get("name", "Unknown"),
            "price": float(data.get("price", 0)),
            "market_cap": float(data.get("marketCap", 0)),
            "liquidity": float(data.get("liquidity", 0)),
            "volume_24h": float(data.get("volume24h", 0)),
            "last_trade": int(data.get("updatedAt", 0)), # Timestamp in milliseconds
            # Note: RaidenX typically doesn't provide 30-min price change/volume directly.
            # You might need to track historical prices or use another API for accurate leaderboard.
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"[RaidenX] Network error in fetch_token_info for {token_address}: {e}")
        return default_info(token_address)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        logger.error(f"[RaidenX] Data parsing error in fetch_token_info for {token_address}: {e}")
        return default_info(token_address)
    except Exception as e:
        logger.error(f"[RaidenX] Unexpected error in fetch_token_info for {token_address}: {e}")
        return default_info(token_address)

def default_info(token_address):
    """Returns a default token info dictionary in case of API errors."""
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
    """Convenience function to get just the symbol."""
    info = fetch_token_info(token_address)
    return info.get("symbol", "TOKEN")

# --- SUI Price Data (using CoinGecko as RaidenX doesn't provide native SUI price) ---
def fetch_sui_price():
    """Fetches the current price of SUI in USD from CoinGecko."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=sui&vs_currencies=usd"
        response = requests.get(url, timeout=5) # Added timeout
        response.raise_for_status()
        data = response.json()
        sui_price = data.get("sui", {}).get("usd", 0)
        logger.info(f"[CoinGecko] Fetched SUI Price: ${sui_price:.2f}")
        return sui_price
    except requests.exceptions.RequestException as e:
        logger.error(f"[CoinGecko] Network error fetching SUI price: {e}")
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        logger.error(f"[CoinGecko] Data parsing error fetching SUI price: {e}")
    except Exception as e:
        logger.error(f"[CoinGecko] Unexpected error fetching SUI price: {e}")
    return 0

# --- Real-time Buy Event Listener (Sui RPC WebSocket) ---
# This is a conceptual implementation. You MUST replace SUI_RPC_WEBSOCKET_URL
# with your chosen provider's WebSocket endpoint.
# The `on_message` logic might need adjustment based on the exact event structure
# from your RPC provider and the specific DEXs you want to track.

def on_message(ws, message):
    """Callback function for WebSocket messages."""
    try:
        event_data = json.loads(message)
        # logger.debug(f"Received WS message: {event_data}") # Uncomment for debugging

        if "params" in event_data and "result" in event_data["params"]:
            # This structure is typical for subscription results
            event = event_data["params"]["result"]["event"]
            
            # --- IMPORTANT: Parsing Logic for Token Buy Events ---
            # This is a generic attempt to parse MoveObjectChange/MoveEvent.
            # Real-world buy events often involve specific `MoveEvent`s from DEX contracts
            # or `TransferObject` events. You will need to inspect actual event data
            # from your chosen RPC provider to refine this parsing.
            
            # Example: Looking for MoveEvent (which is common for token transfers)
            if "moveEvent" in event:
                move_event = event["moveEvent"]
                package_id = move_event.get("packageId")
                transaction_module = move_event.get("transactionModule")
                function = move_event.get("function")
                
                # Check for common token transfer functions or events from known DEXs
                # This is a very simplified check.
                # You might need to look for specific `type` fields or `parsedJson` content
                # to identify token transfers accurately.
                
                # Example: Filtering for 'Transfer' or 'Swap' related events
                # You'd typically need to know the structure of the token and DEX events.
                if "parsedJson" in move_event:
                    parsed_json = move_event["parsedJson"]
                    # Example: Looking for an event that indicates a coin transfer
                    # The exact keys depend on the event definition.
                    if "amount" in parsed_json and "coin_type" in parsed_json and "recipient" in parsed_json:
                        token_address = parsed_json.get("coin_type").split("::")[0] # Crude way to get package ID
                        # Further refine token_address extraction if needed (e.g., full object ID)
                        
                        # Fetch token info to get symbol and current price for USD value
                        # This should be cached or done efficiently to avoid rate limits
                        token_info = fetch_token_info(token_address)
                        sui_price = fetch_sui_price() # Needed if the buy involves SUI as the counter-asset

                        # Assuming `parsedJson` gives us details like `amount`, `sender`, `recipient`
                        # This is highly dependent on the event structure for a BUY transaction.
                        # A buy usually means someone received a token and sent SUI/another token.
                        # You might need to look at `balanceChanges` or `objectChanges` in the transaction
                        # if the event itself doesn't contain all needed info.
                        
                        # Placeholder for extracting actual buy data
                        # In a real scenario, you'd carefully map event fields to buy_data
                        buy_data = {
                            "transaction_id": event["id"]["txDigest"],
                            "token_address": token_address, # This needs to be the actual token object ID or type
                            "buyer_address": event.get("sender", "unknown"), # Often the sender of the transaction
                            "amount": float(parsed_json.get("amount", 0)), # Amount of the token bought
                            "usd_value": float(parsed_json.get("amount", 0)) * token_info.get("price", 0),
                            "timestamp": int(event["timestampMs"]) // 1000 # Convert ms to seconds
                        }
                        
                        if buy_data["usd_value"] > 0: # Only add valid buys
                            with _queue_lock:
                                _buy_event_queue.append(buy_data)
                            logger.info(f"🟢 Detected potential buy: {buy_data}")
                            return

            # You might also look for `TransferObject` events if they fit your definition of a buy
            # Or other event types depending on how DEXes on Sui emit events for swaps.
            # Example: if "transferObject" in event: ...
            
    except Exception as e:
        logger.error(f"Error processing WebSocket message: {e}\nMessage: {message[:200]}...")

def on_error(ws, error):
    """Callback function for WebSocket errors."""
    logger.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    """Callback function for WebSocket close."""
    logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}. Attempting to reconnect...")
    # Implement a reconnection strategy here
    time.sleep(5) # Wait before attempting to reconnect
    start_sui_event_listener() # Reconnect

def on_open(ws):
    """Callback function for WebSocket open."""
    logger.info("WebSocket connection opened. Subscribing to Sui events...")
    # Subscribe to all `MoveEvent`s or specific `TransferObject` events.
    # You might want to filter by `MoveEvent` type or `objectChanges` if supported by your provider.
    # Check your Sui RPC provider's documentation for exact subscription filters.
    # Example: "EventFilter": {"MoveEventType": "0x2::coin::CoinStoreCreated"} or {"TransactionFilter": {"InputObjects": [...]}}
    
    # Generic subscription to all events for demonstration.
    # For production, refine this to only relevant event types (e.g., token transfers/swaps).
    subscribe_message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sui_subscribeEvent",
        "params": [
            {"All": []} # Subscribe to all events. Filter on_message for specific buys.
                        # For more specific: {"MoveEvent": "0x2::coin::CoinBalanceChanged"}
                        # Or {"MoveEvent": "package_id::module_name::EventName"}
            # {"MoveEvent": "0x2::transfer::Transfer"} # This is just an example, specific to Sui.
            # {"MoveEvent": "0x<DEX_ADDRESS>::<DEX_MODULE>::<SWAP_EVENT_NAME>"}
        ]
    }
    ws.send(json.dumps(subscribe_message))
    logger.info("Sent event subscription request.")

def start_sui_event_listener():
    """
    Starts a WebSocket client to listen for real-time Sui events.
    This runs in a separate thread.
    """
    logger.info(f"Attempting to connect to Sui RPC WebSocket: {SUI_RPC_WEBSOCKET_URL}")
    ws = websocket.WebSocketApp(
        SUI_RPC_WEBSOCKET_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    # The `run_forever()` method will block, so it needs to be in a separate thread.
    ws.run_forever(ping_interval=60, ping_timeout=10) # Keep connection alive

# Start the WebSocket listener in a daemon thread
# This ensures it runs in the background
_websocket_thread = threading.Thread(target=start_sui_event_listener, daemon=True)
_websocket_thread.start()
logger.info("Sui WebSocket listener thread started.")

# --- Functions to be used by bot.py to get buys ---
def fetch_recent_buys_from_queue():
    """
    Retrieves all current buy events from the queue.
    This replaces the previous RaidenX-based simulated fetch.
    """
    with _queue_lock:
        buys = list(_buy_event_queue)
        _buy_event_queue.clear() # Clear the queue after fetching
    return buys

# --- Payment Verification (Sui RPC HTTP) ---
# This requires querying a Sui RPC node via HTTP to check for transactions.
# It's a conceptual placeholder and will require a full implementation
# including proper RPC method calls to get transaction details for the BOOST_RECEIVER.
def verify_payment(transaction_hash: str, expected_receiver: str, expected_amount_sui: float) -> bool:
    """
    Verifies if a payment has been made to the expected receiver with the expected amount.
    This is a conceptual placeholder and requires a real Sui RPC implementation.
    """
    logger.warning(f"--- VERIFY_PAYMENT is a conceptual placeholder ---")
    logger.warning(f"  To implement: Query {SUI_RPC_HTTP_URL} for transaction {transaction_hash}")
    logger.warning(f"  Check if funds were sent to {expected_receiver} for at least {expected_amount_sui} SUI.")
    
    # In a real implementation, you would use a Sui RPC client library (e.g., `pysui`)
    # to query the transaction by hash and verify its details.
    
    # Example (pseudocode):
    # try:
    #     response = requests.post(SUI_RPC_HTTP_URL, json={
    #         "jsonrpc": "2.0",
    #         "id": 1,
    #         "method": "sui_getTransactionBlock",
    #         "params": [transaction_hash, {"showInput": True, "showEffects": True, "showBalanceChanges": True}]
    #     }, timeout=10)
    #     response.raise_for_status()
    #     tx_data = response.json().get("result")
    #
    #     if not tx_data:
    #         logger.error(f"Transaction {transaction_hash} not found.")
    #         return False
    #
    #     # Check for SUI balance changes to the receiver
    #     balance_changes = tx_data.get("effects", {}).get("suiEffects", {}).get("balanceChanges", [])
    #     for change in balance_changes:
    #         if change.get("owner", {}).get("Address") == expected_receiver:
    #             # Amount is in MIST (1 SUI = 1,000,000,000 MIST)
    #             actual_sui_mist = int(change.get("amount", 0))
    #             if actual_sui_mist >= expected_amount_sui * 1_000_000_000:
    #                 logger.info(f"Payment verified for {expected_amount_sui} SUI to {expected_receiver}.")
    #                 return True
    #     logger.warning(f"Payment not found or amount incorrect for {transaction_hash}.")
    #     return False
    # except Exception as e:
    #     logger.error(f"Error verifying payment for {transaction_hash}: {e}")
    #     return False

    # For now, always return True for testing/demonstration purposes
    return True # Replace with real verification logic
