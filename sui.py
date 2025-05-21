from pysui.sui.sui_clients.async_client import SuiClient
from pysui.sui.sui_config import SuiConfig
import os

async def verify_payment(txn_hash: str, expected_sui: float, receiver: str) -> bool:
    config = SuiConfig.from_config_file(os.getenv('SUI_CONFIG', '~/.sui/sui_config.yaml'))
    async with SuiClient(config) as client:
        try:
            txn = await client.get_transaction(txn_hash)
            if txn.effects.status.status != "success":
                return False
                
            for event in txn.events:
                if event.get('transfer'):
                    recipient = event['transfer']['recipient']
                    amount = float(event['transfer']['amount']) / 1e9
                    if recipient == receiver and amount >= expected_sui:
                        return True
            return False
        except Exception as e:
            print(f"Verification error: {e}")
            return False
