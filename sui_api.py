import requests
import logging
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger(__name__)

def fetch_token_info(token_address):
    try:
        response = requests.get(f"{Config.DEX_SCREENER_API}{token_address}")
        data = response.json()['pairs'][0]
        return {
            'symbol': data['baseToken']['symbol'],
            'price': float(data['priceUsd']),
            'liquidity': float(data['liquidity']['usd']),
            'market_cap': float(data['fdv']),
            'volume_30m': float(data['volume']['h1'])/2,
            'price_change_30m': (float(data['priceChange']['h1'])/2)
        }
    except Exception as e:
        logger.error(f"DexScreener error: {e}")
        return default_info()

def fetch_recent_buys(token_address, since):
    try:
        response = requests.post(Config.SUI_RPC, json={
            "jsonrpc": "2.0",
            "method": "suix_getEvents",
            "params": [{
                "MoveEventType": "0x2::dex::SwapEvent",
                "Sender": token_address
            }, None, 100, True]
        })
        return [process_event(e) for e in response.json()['result']['data'] 
                if e['timestampMs'] > since*1000]
    except Exception as e:
        logger.error(f"RPC error: {e}")
        return []

def process_event(event):
    parsed = event['parsedJson']
    return {
        'tx_hash': event['id']['txDigest'],
        'buyer': parsed['sender'],
        'amount': float(parsed['amount_in']),
        'usd_value': float(parsed['amount_in']) * float(parsed['price']),
        'timestamp': int(event['timestampMs']/1000)
    }
