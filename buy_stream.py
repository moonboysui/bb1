import os
import asyncio
import websockets
import json
import logging
from database import get_db

logger = logging.getLogger(__name__)

BLOCKEDEN_WWS_URL = os.getenv("BLOCKEDEN_WWS_URL")
# Example event type for Cetus DEX. Adjust if you want more DEXs.
DEX_EVENTS = [
    {"MoveEventType": "0x23a79c4eb5e60d19a1674058a77c4ba0486265c705f5c7f1f1233cfb2e25e1c6::pool::SwapEvent"},
    # Add other DEXes here as needed
]

def parse_swap_event(event):
    # This parser is for Cetus. If using other DEXs, add parsing logic for them.
    data = event.get('parsedJson', {})
    return {
        "token_in": data.get("coin_in_address"),
        "token_out": data.get("coin_out_address"),
        "buyer_address": data.get("owner"),
        "amount": float(data.get("amount_out", 0)),
        "tx_hash": event.get("id", ""),  # Might need to adjust if not present
        "timestamp": int(event.get("timestampMs", 0)) // 1000,
    }

async def start_buy_stream(alert_callback):
    while True:
        try:
            async with websockets.connect(BLOCKEDEN_WWS_URL) as ws:
                # Subscribe to all relevant DEX events
                for event_filter in DEX_EVENTS:
                    sub_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "sui_subscribeEvent",
                        "params": [event_filter]
                    }
                    await ws.send(json.dumps(sub_msg))
                logger.info("Subscribed to DEX events on BlockEden WWS")
                while True:
                    msg = await ws.recv()
                    msg_json = json.loads(msg)
                    event = msg_json.get("params", {}).get("result", {}).get("event", {})
                    if not event:
                        continue
                    buy = parse_swap_event(event)
                    if not buy or not buy.get("token_out"):
                        continue
                    # Find all groups tracking this token_out
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT group_id, min_buy_usd, buystep, emoji, website, telegram_link, twitter_link, media_file_id FROM groups WHERE token_address = ?",
                            (buy["token_out"],)
                        )
                        groups = cursor.fetchall()
                    if not groups:
                        continue
                    for group_data in groups:
                        # Save to buys DB
                        with get_db() as conn:
                            cursor = conn.cursor()
                            cursor.execute(
                                "INSERT OR IGNORE INTO buys (transaction_id, token_address, buyer_address, amount, usd_value, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                                (
                                    buy['tx_hash'],
                                    buy['token_out'],
                                    buy['buyer_address'],
                                    buy['amount'],
                                    0,  # USD value is calculated in alert logic
                                    buy['timestamp'],
                                )
                            )
                            conn.commit()
                        await alert_callback(buy, group_data)
        except Exception as e:
            logger.error(f"BlockEden WWS error: {e}")
            await asyncio.sleep(5)  # Wait and reconnect on error
