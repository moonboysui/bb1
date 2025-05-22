import os
import json
import asyncio
import aiohttp
import logging
from queue import Queue, Empty

logger = logging.getLogger(__name__)

# BlockEden Sui WebSocket URL (requires an API key token in the URL)
WS_URL = os.getenv("BLOCKEDEN_WSS", "")

# Thread-safe queues for cross-thread communication
event_queue = Queue()      # events from WS thread to main thread
subscribe_queue = Queue()  # subscription requests from main thread to WS thread

async def _ws_listen(ws):
    """Listen for incoming WebSocket messages and handle subscriptions."""
    try:
        while True:
            # Wait for next WS message (with timeout to check subscribe_queue)
            msg = None
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
            except asyncio.TimeoutError:
                msg = None
            # Process any pending subscription requests from main thread
            while True:
                try:
                    new_token = subscribe_queue.get_nowait()
                except Empty:
                    break
                try:
                    # Subscribe to CoinBalanceChange events for the new token:contentReference[oaicite:0]{index=0}
                    filter_params = {"All": [{"EventType": "CoinBalanceChange"}, {"CoinType": new_token}]}
                    subscribe_req = {"jsonrpc": "2.0", "id": new_token, "method": "sui_subscribeEvent", "params": [filter_params]}
                    await ws.send_str(json.dumps(subscribe_req))
                    ack = await ws.receive()  # subscription acknowledgement
                    if ack.type == aiohttp.WSMsgType.TEXT:
                        resp = json.loads(ack.data)
                        if resp.get("error"):
                            logger.error(f"Subscription error for {new_token}: {resp['error']}")
                        else:
                            logger.info(f"Subscribed to events for token {new_token}")
                except Exception as e:
                    logger.error(f"Failed to subscribe to {new_token}: {e}")
            # Handle incoming message (if any)
            if msg:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    # If this is an event notification, put event result in queue
                    if data.get("method") == "sui_subscribeEvent":
                        event = data.get("params", {}).get("result")
                        if event:
                            event_queue.put(event)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
    finally:
        await ws.close()
        logger.info("WebSocket connection closed.")

async def run_ws_client(initial_tokens):
    """Connect to BlockEden WebSocket and subscribe to initial tokens' events."""
    if not WS_URL:
        logger.warning("No BlockEden WSS URL provided; skipping WebSocket connection.")
        return
    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect(WS_URL)
        logger.info("Connected to BlockEden WebSocket.")
        # Subscribe to each initial token's CoinBalanceChange events
        for token in initial_tokens:
            try:
                filter_params = {"All": [{"EventType": "CoinBalanceChange"}, {"CoinType": token}]}
                subscribe_req = {"jsonrpc": "2.0", "id": token, "method": "sui_subscribeEvent", "params": [filter_params]}
                await ws.send_str(json.dumps(subscribe_req))
                ack = await ws.receive()
                if ack.type == aiohttp.WSMsgType.TEXT:
                    resp = json.loads(ack.data)
                    if resp.get("error"):
                        logger.error(f"Subscription failed for {token}: {resp['error']}")
                    else:
                        logger.info(f"Subscribed to token events: {token}")
            except Exception as e:
                logger.error(f"Error subscribing to {token}: {e}")
        # Enter listening loop (indefinitely until connection closes)
        await _ws_listen(ws)
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
    finally:
        await session.close()
        logger.info("WebSocket session closed.")

def start_ws_thread(initial_tokens):
    """Launch the WebSocket client in a separate thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(run_ws_client(initial_tokens))
    try:
        loop.run_until_complete(task)
    except Exception as e:
        logger.error(f"Exception in WebSocket thread: {e}")
    # Keep the event loop running (if run_ws_client returns, connection ended)
    try:
        loop.run_forever()
    finally:
        loop.close()
