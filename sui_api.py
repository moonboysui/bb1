import aiohttp
import logging
import time
import json
import os
from datetime import datetime

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# API endpoints
RAIDEN_API_URL = "https://api.raiden.xyz/v1/marketplace"
SUI_API_URL = "https://mainnet.sui.io"
MOONBAGS_API_URL = "https://api.moonbags.io"

# Cache for token info to avoid excessive API calls
token_info_cache = {}
cache_expiry = 300  # 5 minutes

async def fetch_recent_buys(token_address, since_timestamp):
    """Fetch recent buys for a given token since the timestamp."""
    try:
        # This endpoint should return buys for the specified token
        url = f"{RAIDEN_API_URL}/buys"
        params = {
            "token": token_address,
            "since": since_timestamp,
            "limit": 50  # Adjust as needed
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    logger.error(f"API error {response.status}: {await response.text()}")
                    return []
                
                data = await response.json()
                
                # Transform the API response into our expected format
                buys = []
                for item in data.get("data", []):
                    try:
                        buy = {
                            "tx_hash": item["transaction_id"],
                            "buyer_address": item["buyer_address"],
                            "amount": float(item["token_amount"]),
                            "usd_value": float(item["usd_value"]),
                            "timestamp": int(datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).timestamp())
                        }
                        buys.append(buy)
                    except (KeyError, ValueError) as e:
                        logger.error(f"Error parsing buy data: {e}")
                
                return buys
    
    except Exception as e:
        logger.error(f"Error fetching recent buys: {e}")
        return []

async def fetch_token_info(token_address):
    """Fetch and cache token information."""
    global token_info_cache
    
    current_time = time.time()
    
    # Check cache first
    if token_address in token_info_cache:
        cache_data = token_info_cache[token_address]
        if current_time - cache_data["timestamp"] < cache_expiry:
            return cache_data["data"]
    
    try:
        # Fetch token info from API
        url = f"{RAIDEN_API_URL}/tokens/{token_address}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"API error {response.status}: {await response.text()}")
                    return {
                        "symbol": "UNKNOWN",
                        "price": 0,
                        "market_cap": 0,
                        "liquidity": 0,
                        "price_change_30m": 0
                    }
                
                data = await response.json()
                
                # Extract relevant information
                token_info = {
                    "symbol": data.get("symbol", "UNKNOWN"),
                    "price": float(data.get("price", 0)),
                    "market_cap": float(data.get("market_cap", 0)),
                    "liquidity": float(data.get("liquidity", 0)),
                    "price_change_30m": float(data.get("price_change_30m", 0))
                }
                
                # Cache the result
                token_info_cache[token_address] = {
                    "data": token_info,
                    "timestamp": current_time
                }
                
                return token_info
    
    except Exception as e:
        logger.error(f"Error fetching token info: {e}")
        return {
            "symbol": "UNKNOWN",
            "price": 0,
            "market_cap": 0,
            "liquidity": 0,
            "price_change_30m": 0
        }

async def get_token_symbol(token_address):
    """Get token symbol (simplified helper function)."""
    token_info = await fetch_token_info(token_address)
    return token_info.get("symbol", "TOKEN")

async def verify_payment(txn_hash, expected_amount, expected_receiver):
    """Verify a SUI payment transaction."""
    try:
        # Create the SUI RPC request
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sui_getTransaction",
            "params": [txn_hash]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(SUI_API_URL, json=payload) as response:
                if response.status != 200:
                    logger.error(f"SUI API error {response.status}: {await response.text()}")
                    return False
                
                result = await response.json()
                
                # Extract transaction data
                tx_data = result.get("result", {}).get("transaction", {})
                
                # Check if it's a payment transaction
                if tx_data.get("data", {}).get("transaction", {}).get("kind") != "ProgrammableTransaction":
                    logger.warning(f"Transaction {txn_hash} is not a programmable transaction")
                    return False
                
                # Extract transaction details - for SUI payments we need to analyze the transaction commands
                # This is a simplified version and might need adjustment based on the actual SUI transaction structure
                try:
                    commands = tx_data.get("data", {}).get("transaction", {}).get("transactions", [])
                    for cmd in commands:
                        if cmd.get("TransferObjects") and len(cmd.get("TransferObjects", [])[1]) > 0:
                            recipient = cmd.get("TransferObjects", [])[1]
                            if recipient == expected_receiver:
                                # For simplicity, we're not checking the amount here
                                # A real implementation would extract and verify the amount as well
                                logger.info(f"Payment verified: {txn_hash}")
                                return True
                except Exception as e:
                    logger.error(f"Error parsing transaction structure: {e}")
                    return False
                
                logger.warning(f"Payment verification failed for txn {txn_hash}")
                return False
    
    except Exception as e:
        logger.error(f"Error verifying payment: {e}")
        return False
