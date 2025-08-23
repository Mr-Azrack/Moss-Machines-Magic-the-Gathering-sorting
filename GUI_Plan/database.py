# database.py (read-only, data folder aware)
import sqlite3
import os
from typing import Dict, Any, List, Optional

# Look for DBs inside the "data" subfolder
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")

def list_db_files() -> List[str]:
    """List all .db files inside ./data subfolder."""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)  # create if missing
    return [f for f in os.listdir(DATA_DIR) if f.endswith(".db")]

def get_db_path(db_name: Optional[str] = None) -> str:
    """Resolve the full path to a database file inside ./data."""
    if db_name is None:
        dbs = list_db_files()
        if not dbs:
            raise FileNotFoundError("No .db files found in ./data subfolder.")
        db_name = dbs[0]
    db_path = os.path.join(DATA_DIR, db_name)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file '{db_name}' not found in ./data")
    return db_path

def get_connection(db_name: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection to an existing DB in ./data (no create)."""
    db_path = get_db_path(db_name)
    return sqlite3.connect(db_path)

def get_card_info(card_id: Any, db_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch a card row by ID across tables (returns dict or None)."""
    try:
        with get_connection(db_name) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            for table in tables:
                cur.execute(f"PRAGMA table_info({table})")
                cols = [c[1] for c in cur.fetchall()]
                if "id" not in cols:
                    continue
                cur.execute(f"SELECT * FROM {table} WHERE id = ? LIMIT 1", (card_id,))
                row = cur.fetchone()
                if row:
                    return dict(zip(cols, row))
            return None
    except sqlite3.Error as e:
        print(f"DB error in get_card_info: {e}")
        return None

def update_inventory(card_id: Any, delta: int = 1, db_name: Optional[str] = None) -> bool:
    """
    Update quantity in `inventory` table if it exists.
    Returns True if updated, False otherwise.
    """
    try:
        with get_connection(db_name) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if ("inventory",) not in cur.fetchall():
                return False
            cur.execute("PRAGMA table_info(inventory)")
            cols = [c[1] for c in cur.fetchall()]
            if not {"card_id", "quantity"}.issubset(cols):
                return False
            cur.execute("SELECT quantity FROM inventory WHERE card_id=?", (card_id,))
            row = cur.fetchone()
            if not row:
                return False
            new_qty = row[0] + delta
            cur.execute("UPDATE inventory SET quantity=? WHERE card_id=?", (new_qty, card_id))
            conn.commit()
            return True
    except sqlite3.Error as e:
        print(f"DB error in update_inventory: {e}")
        return False
