import os
import sqlite3
import logging

# Get logger
logger = logging.getLogger(__name__)

# Use environment variable or default to data directory
DB_PATH = os.getenv("DATABASE_PATH", "data/moonbags.db")

def ensure_db_directory():
    """Ensure the directory for the database exists."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        logger.info(f"Created directory {db_dir} for database")

def init_db():
    """Initialize the database with required tables."""
    ensure_db_directory()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Groups table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            group_id INTEGER PRIMARY KEY,
            token_address TEXT NOT NULL,
            min_buy_usd REAL DEFAULT 0,
            emoji TEXT DEFAULT '🔥',
            website TEXT,
            telegram_link TEXT,
            twitter_link TEXT,
            media_file_id TEXT
        )
        ''')
        
        # Buys table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS buys (
            transaction_id TEXT PRIMARY KEY,
            token_address TEXT NOT NULL,
            buyer_address TEXT NOT NULL,
            amount REAL NOT NULL,
            usd_value REAL NOT NULL,
            timestamp INTEGER NOT NULL
        )
        ''')
        
        # Boosts table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS boosts (
            token_address TEXT PRIMARY KEY,
            expiration_timestamp INTEGER NOT NULL
        )
        ''')
        
        conn.commit()
        logger.info("Database initialized successfully")

def get_db():
    """Get a database connection."""
    ensure_db_directory()
    return sqlite3.connect(DB_PATH)
