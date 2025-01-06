import sqlite3
from typing import List, Optional


class DatabaseManager:

    def __init__(self, db_path: str = "twitter_monitor.db") -> None:

        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monitored_users (
                    username TEXT PRIMARY KEY,
                    following_count INTEGER,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def add_user(self, username: str) -> None:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO monitored_users (username) VALUES (?)",
                (username,)
            )
            conn.commit()

    def remove_user(self, username: str) -> None:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM monitored_users WHERE username = ?",
                (username,)
            )
            conn.commit()

    def get_all_users(self) -> List[str]:

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT username FROM monitored_users")
            return [row[0] for row in cursor.fetchall()]

    def update_follower_count(self, username: str, count: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE monitored_users 
                SET following_count = ?, last_updated = CURRENT_TIMESTAMP
                WHERE username = ?
                """,
                (count, username)
            )
            conn.commit() 

    def get_following_count(self, username: str) -> Optional[int]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT following_count FROM monitored_users WHERE username = ?",
                (username,)
            )
            result = cursor.fetchone()
            return result[0] if result else None 