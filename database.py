import os
import sqlite3
import logging

# Logger setup
logger = logging.getLogger(__name__)

# Database path from environment or default
DB_PATH = os.getenv("DATABASE_PATH", "data/moonbags.db")

def ensure_db_directory():
    """Ensure the database directory exists."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        logger.info(f"Created directory {db_dir} for database")

def init_db():
    """Initialize the database tables if they don't exist."""
    ensure_db_directory()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # Groups table (one row per Telegram group using the bot)
        cursor.execute("""
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
        # If an older version of the DB exists, ensure token_symbol column exists
        cursor.execute("PRAGMA table_info(groups)")
        columns = [col[1] for col in cursor.fetchall()]
        if "token_symbol" not in columns:
            cursor.execute("ALTER TABLE groups ADD COLUMN token_symbol TEXT DEFAULT 'TOKEN'")
            logger.info("Added missing column: token_symbol to groups table")
        # Buys table (records of buy transactions for volume and alert deduplication)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS buys (
                transaction_id TEXT PRIMARY KEY,
                token_address TEXT NOT NULL,
                buyer_address TEXT NOT NULL,
                amount REAL NOT NULL,
                usd_value REAL NOT NULL,
                timestamp INTEGER NOT NULL
            )
        """)
        # Boosts table (tracks active boosts for tokens)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS boosts (
                token_address TEXT PRIMARY KEY,
                expiration_timestamp INTEGER NOT NULL
            )
        """)
        conn.commit()
        logger.info("Database initialized successfully")

def get_db():
    """Get a new database connection (SQLite)."""
    ensure_db_directory()
    return sqlite3.connect(DB_PATH)

def clear_fake_symbols():
    """Replace any generic/fake token symbols in groups with 'TOKEN' placeholder."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        fake_list = ['MEME', 'MOON', 'APE', 'SUI', 'DOGE', 'PEPE']
        placeholders = ', '.join('?' for _ in fake_list)
        query = f"UPDATE groups SET token_symbol = 'TOKEN' WHERE token_symbol IN ({placeholders})"
        cursor.execute(query, fake_list)
        affected = cursor.rowcount
        conn.commit()
        if affected:
            logger.info(f"Cleared {affected} fake token symbol entries in groups table")
