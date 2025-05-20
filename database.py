import sqlite3
import os
import logging

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    """Create the database tables."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id INTEGER PRIMARY KEY,
                    token_address TEXT,
                    min_buy_usd REAL,
                    emoji TEXT,
                    website TEXT,
                    telegram_link TEXT,
                    twitter_link TEXT,
                    media_file_id TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS buys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT,
                    timestamp INTEGER,
                    usd_value REAL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS boosts (
                    token_address TEXT PRIMARY KEY,
                    expiration_timestamp INTEGER
                )
            """)
            conn.commit()
            logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

def get_db():
    """Connect to the database."""
    try:
        db_path = "/tmp/bot.db"
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        raise
