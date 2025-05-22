import os
import json
import time
import logging
import queue
from websocket import create_connection, WebSocket

# Logger setup
logger = logging.getLogger(__name__)

# WebSocket and event queues
event_queue = queue.Queue()
subscribe_queue = queue.Queue()

# WebSocket URL from environment (BlockEden Sui WS endpoint)
WS_URL = os.getenv("SUI_WS_URL", "")

def start_ws_thread(initial_tokens):
    """Connect to the Sui WebSocket and subscribe to coin balance events for tokens."""
    if not WS_URL:
        logger.info("No SUI WebSocket URL provided. Skipping WebSocket listener.")
        return
    while True:
        try:
            ws = create_connection(WS_URL)
            logger.info("Connected to Sui WebSocket API")
            # Subscribe to CoinBalanceChange events for each initial token
            sub_id_counter = 1
            for token in initial_tokens:
                try:
                    subscribe_request = {
                        "jsonrpc": "2.0",
                        "id": sub_id_counter,
                        "method": "sui_subscribeEvent",
                        "params": [
                            {
                                "All": [
                                    {"EventType": "CoinBalanceChange"},
                                    {"CoinType": token}
                                ]
                            }
                        ]
                    }
                    ws.send(json.dumps(subscribe_request))
                    sub_id_counter += 1
                    logger.info(f"Subscribed to events for token: {token}")
                except Exception as e:
                    logger.error(f"Failed to subscribe initial token {token}: {e}")
            # Continuously listen for messages and handle subscriptions
            while True:
                # Send new subscription requests if any tokens are added to the queue
                while not subscribe_queue.empty():
                    new_token = subscribe_queue.get()
                    try:
                        subscribe_request = {
                            "jsonrpc": "2.0",
                            "id": sub_id_counter,
                            "method": "sui_subscribeEvent",
                            "params": [
                                {
                                    "All": [
                                        {"EventType": "CoinBalanceChange"},
                                        {"CoinType": new_token}
                                    ]
                                }
                            ]
                        }
                        ws.send(json.dumps(subscribe_request))
                        sub_id_counter += 1
                        logger.info(f"Subscribed to events for new token: {new_token}")
                    except Exception as e:
                        logger.error(f"Failed to subscribe to new token {new_token}: {e}")
                # Receive messages from WebSocket
                message = ws.recv()
                if not message:
                    continue
                data = json.loads(message)
                # When an event is received, BlockEden will send a JSON with method "sui_subscribeEvent"
                if data.get("method") == "sui_subscribeEvent":
                    result = data.get("params", {}).get("result")
                    if result:
                        event_queue.put(result)
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}. Reconnecting in 5 seconds...")
            try:
                ws.close()
            except Exception:
                pass
            time.sleep(5)
            # Loop will retry connection
