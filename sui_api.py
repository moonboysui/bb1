import requests
import logging
from config import Config

logger = logging.getLogger(__name__)

def fetch_token_info(full_address):
    try:
        base_address = full_address.split("::")[0]
        response = requests.get(f"{Config.DEX_SCREENER_API}{base_address}")
        data = response.json()
        
        for pair in data.get('pairs', []):
            if pair['baseToken']['address'].lower() == full_address.lower():
                return {
                    'symbol': pair['baseToken']['symbol'],
                    'price': float(pair['priceUsd']),
                    'liquidity': float(pair['liquidity']['usd']),
                    'market_cap': float(pair['fdv']),
                    'volume_24h': float(pair['volume']['h24']),
                    'sui_price': float(pair['quoteToken']['priceUsd'])
                }
        return default_info(full_address)
    except Exception as e:
        logger.error(f"API Error: {e}")
        return default_info(full_address)

def fetch_recent_buys(token_address):
    try:
        response = requests.post(Config.SUI_RPC, json={
            "jsonrpc": "2.0",
            "method": "suix_getEvents",
            "params": [{
                "MoveEventType": "0x3::token::TokenPurchaseEvent"
            }, None, 100, True]
        })
        return [process_event(e) for e in response.json()['result']['data']]
    except Exception as e:
        logger.error(f"RPC Error: {e}")
        return []

def verify_payment(tx_hash, amount, receiver):
    try:
        response = requests.post(Config.SUI_RPC, json={
            "jsonrpc": "2.0",
            "method": "sui_getTransactionBlock",
            "params": [tx_hash, {"showEffects": True}],
            "id": 1
        })
        tx = response.json()['result']
        return any(
            eff['recipient']['addressOwner'] == receiver and 
            float(eff['amount']) >= amount
            for eff in tx['effects']['events']
            if eff.get('coinBalanceChange')
        )
    except Exception as e:
        logger.error(f"Payment verification failed: {e}")
        return False

def default_info(address):
    return {
        'symbol': 'TOKEN',
        'price': 0,
        'liquidity': 0,
        'market_cap': 0,
        'volume_24h': 0,
        'sui_price': 0
    }
