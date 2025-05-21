import os

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    BOOST_WALLET = os.getenv("BOOST_WALLET", "0x7338ef163ee710923803cb0dd60b5b02cddc5fbafef417342e1bbf1fba20e702")
    TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@moonbagstrending")
    DATABASE_PATH = os.getenv("DATABASE_PATH", "data/moonbags.db")
    PORT = int(os.getenv("PORT", 8080))
    DEX_SCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/"
    SUI_RPC = "https://fullnode.mainnet.sui.io/"
