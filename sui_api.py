import requests
import logging
import time
import json

# Get logger
logger = logging.getLogger(__name__)

# API endpoints
SUI_API_BASE = "https://wallet-rpc.sui.io/"
TOKEN_INFO_API = "https://moonbags.io/api/tokens/"

async def verify_payment(tx_hash, expected_amount, receiver_address):
    """Verify that a payment was made in the expected amount to the expected address."""
    try:
        # Add retry logic since the transaction might not be immediately available
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    SUI_API_BASE,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "sui_getTransactionBlock",
                        "params": [
                            tx_hash,
                            {
                                "showEffects": True,
                                "showInput": True,
                                "showEvents": True
                            }
                        ]
                    },
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Check if transaction exists
                    if "result" not in data or data.get("error"):
                        logger.warning(f"Transaction not found or error: {data.get('error', 'Unknown error')}")
                        if attempt < max_retries - 1:
                            time.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        return False
                    
                    # Extract transaction details
                    tx_data = data["result"]
                    
                    # Check if this is a payment transaction
                    if "effects" not in tx_data:
                        logger.warning("Transaction has no effects")
                        return False
                    
                    # Check for SUI coin transfers in the transaction effects
                    for change in tx_data["effects"]["mutated"]:
                        # Check if this is a coin and matches our receiver
                        if "coinType" in change["reference"]["objectType"] and "0x2::sui::SUI" in change["reference"]["objectType"]:
                            if change["owner"]["AddressOwner"] == receiver_address:
                                # Found matching receiver, now check amount
                                # Note: This is simplified and would need adjustment based on actual Sui response format
                                balance_change = int(change["preview"]["balance_change"]) / 1_000_000_000  # Convert from MIST to SUI
                                
                                # Allow for small discrepancies in floating point comparison
                                if abs(balance_change - expected_amount) < 0.01:
                                    logger.info(f"Payment verified: {balance_change} SUI to {receiver_address}")
                                    return True
                    
                    logger.warning(f"Could not verify payment of {expected_amount} SUI to {receiver_address}")
                    return False
                else:
                    logger.warning(f"API error: {response.status_code} - {response.text}")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    return False
                
            except Exception as e:
                logger.error(f"Error verifying payment (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                return False
        
        return False
        
    except Exception as e:
        logger.error(f"Error verifying payment: {e}")
        return False

async def fetch_recent_buys(token_address, since_timestamp):
    """Fetch recent buys for a specific token since the given timestamp."""
    try:
        # This would normally call the Moonbags API or other source
        # For now, returning mock data for demonstration
        # In a real implementation, you would make an API call here
        
        # Mock data for demonstration - replace with actual API call
        current_time = int(time.time())
        
        # Simulate some buys only if enough time has passed
        if current_time - since_timestamp > 60:  # Only generate mock data every minute or so
            # Return 1-3 random buys
            mock_buys = []
            import random
            for _ in range(random.randint(1, 3)):
                mock_buys.append({
                    "tx_hash": f"0x{random.randint(10000000, 99999999)}abcdef",
                    "buyer_address": f"0x{random.randint(1000, 9999)}abcd{random.randint(1000, 9999)}",
                    "amount": random.uniform(10, 1000),
                    "usd_value": random.uniform(10, 500),
                    "timestamp": current_time - random.randint(1, 59)
                })
            return mock_buys
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching recent buys: {e}")
        return []

async def fetch_token_info(token_address):
    """Fetch information about a token."""
    try:
        # In a real implementation, you would make an API call to get token info
        # For now, returning mock data
        
        # Mock data - would be replaced with actual API call
        import random
        
        # Generate a random token symbol
        symbols = ["MOON", "SUI", "PEPE", "DOGE", "SHIB", "APE", "MEME"]
        symbol = random.choice(symbols)
        
        return {
            "symbol": symbol,
            "name": f"{symbol} Token",
            "price": random.uniform(0.00000001, 0.1),
            "market_cap": random.uniform(100000, 5000000),
            "liquidity": random.uniform(50000, 1000000),
            "price_change_30m": random.uniform(-10, 20),
            "price_change_24h": random.uniform(-30, 50),
        }
        
    except Exception as e:
        logger.error(f"Error fetching token info: {e}")
        return {"symbol": "TOKEN", "price": 0, "market_cap": 0, "liquidity": 0}

async def get_token_symbol(token_address):
    """Get the symbol for a token."""
    try:
        token_info = await fetch_token_info(token_address)
        return token_info.get("symbol", "TOKEN")
    except Exception as e:
        logger.error(f"Error getting token symbol: {e}")
        return "TOKEN"
