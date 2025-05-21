import sqlite3
import os
import logging
from contextlib import contextmanager

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "moonbags.db")

@contextmanager
def get_db():
    """Context manager for database connection."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        yield conn
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def init_db():
    """Initialize the database with the required tables."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Create groups table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    group_id INTEGER PRIMARY KEY,
                    token_address TEXT NOT NULL,
                    min_buy_usd REAL DEFAULT 5.0,
                    emoji TEXT DEFAULT '🔥',
                    website TEXT,
                    telegram_link TEXT,
                    twitter_link TEXT,
                    media_file_id TEXT
                )
            ''')
            
            # Create buys table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS buys (
                    transaction_id TEXT PRIMARY KEY,
                    token_address TEXT NOT NULL,
                    buyer_address TEXT NOT NULL,
                    amount REAL NOT NULL,
                    usd_value REAL NOT NULL,
                    timestamp INTEGER NOT NULL,
                    FOREIGN KEY (token_address) REFERENCES groups(token_address)
                )
            ''')
            
            # Create boosts table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS boosts (
                    token_address TEXT PRIMARY KEY,
                    expiration_timestamp INTEGER NOT NULL
                )
            ''')
            
            conn.commit()
            logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise
