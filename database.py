import os
import sqlite3
import logging

logger = logging.getLogger(__name__)
DB_PATH = os.getenv("DATABASE_PATH", "data/moonbags.db")

def ensure_db_directory():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logger.info(f"Created directory {db_dir} for database")

def init_db():
    """Initialize the SQLite database with required tables."""
    ensure_db_directory()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        # Groups table (one token config per Telegram group)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                token_address TEXT NOT NULL,
                token_symbol TEXT DEFAULT 'TOKEN',
                min_buy_usd REAL DEFAULT 0,
                emoji TEXT DEFAULT '🔥',
                website TEXT,
                telegram_link TEXT,
                twitter_link TEXT,
                media_file_id TEXT
            )
        """)
        # Ensure token_symbol column exists (for backward compatibility)
        cur.execute("PRAGMA table_info(groups)")
        columns = [col[1] for col in cur.fetchall()]
        if "token_symbol" not in columns:
            cur.execute("ALTER TABLE groups ADD COLUMN token_symbol TEXT DEFAULT 'TOKEN'")
            logger.info("Added missing column token_symbol to groups table")
        # Buys table (records of recent buy events for volume calculations)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS buys (
                transaction_id TEXT PRIMARY KEY,
                token_address TEXT NOT NULL,
                buyer_address TEXT NOT NULL,
                amount REAL NOT NULL,
                usd_value REAL NOT NULL,
                timestamp INTEGER NOT NULL
            )
        """)
        # Boosts table (active token boosts with expiry)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS boosts (
                token_address TEXT PRIMARY KEY,
                expiration_timestamp INTEGER NOT NULL
            )
        """)
        conn.commit()
        logger.info("Database initialized successfully")

def get_db():
    """Obtain a new database connection (for use with context manager)."""
    ensure_db_directory()
    return sqlite3.connect(DB_PATH)

def clear_fake_symbols():
    """Reset any placeholder/fake token symbols in groups table to 'TOKEN'. """
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        fake_list = ['MEME', 'MOON', 'APE', 'SUI', 'DOGE', 'PEPE']
        placeholders = ','.join('?' for _ in fake_list)
        cur.execute(f"UPDATE groups SET token_symbol = 'TOKEN' WHERE token_symbol IN ({placeholders})", fake_list)
        affected = cur.rowcount
        conn.commit()
        if affected:
            logger.info(f"Cleared {affected} fake token symbol entries in groups table")
