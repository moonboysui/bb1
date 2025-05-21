import os

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    BOOST_WALLET = os.getenv("BOOST_WALLET", "0x7338ef163ee710923803cb0dd60b5b02cddc5fbafef417342e1bbf1fba20e702")
    TRENDING_CHANNEL = os.getenv("TRENDING_CHANNEL", "@moonbagstrending")
    PORT = int(os.getenv("PORT", 10000))  # Render requires port 10000
    WEB_SERVER_URL = os.getenv("WEB_SERVER_URL", "https://your-render-app-name.onrender.com")
  
