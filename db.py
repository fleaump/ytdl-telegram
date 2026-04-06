import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class WhitelistDB:
    """SQLite whitelist database manager"""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def init_db(self):
        """Initialize database and create table if not exists"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS whitelist (
                chat_id INTEGER PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("Whitelist database initialized")

    def is_allowed(self, chat_id: int) -> bool:
        """Check if chat_id is in whitelist"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM whitelist WHERE chat_id = ?', (chat_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def add(self, chat_id: int) -> bool:
        """Add chat_id to whitelist"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO whitelist (chat_id) VALUES (?)', (chat_id,))
            conn.commit()
            logger.info(f"Added chat_id {chat_id} to whitelist")
            return True
        except sqlite3.IntegrityError:
            logger.info(f"chat_id {chat_id} already in whitelist")
            return False
        finally:
            conn.close()

    def remove(self, chat_id: int) -> bool:
        """Remove chat_id from whitelist"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute('DELETE FROM whitelist WHERE chat_id = ?', (chat_id,))
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        if affected > 0:
            logger.info(f"Removed chat_id {chat_id} from whitelist")
        return affected > 0

    def list_all(self):
        """Get all whitelisted chat_ids"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM whitelist ORDER BY added_at')
        result = [row[0] for row in cursor.fetchall()]
        conn.close()
        return result
