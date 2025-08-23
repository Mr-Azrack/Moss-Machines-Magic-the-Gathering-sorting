# InventoryTracker.py — DB-first duplicate check with legacy fallback

import time
import os
import sqlite3
from typing import Any, Optional

from database import get_connection, list_db_files

LEGACY_PATH = "Collection/Collection.txt"


def _ensure_legacy_file():
    os.makedirs(os.path.dirname(LEGACY_PATH), exist_ok=True)
    if not os.path.exists(LEGACY_PATH):
        with open(LEGACY_PATH, 'w'):
            time.sleep(0.1)


def _db_check_and_insert(card_id: Any, db_name: Optional[str]) -> Optional[str]:
    """
    Return:
      - "RejectCard" if already present (quantity >= 1)
      - string(card_id) if newly inserted (quantity=1)
      - None if DB/table/columns aren't available
    """
    # Resolve DB choice
    dbfile = db_name
    if not dbfile:
        dbs = list_db_files()
        dbfile = dbs[0] if dbs else None
    if not dbfile:
        return None

    try:
        with get_connection(dbfile) as conn:
            cur = conn.cursor()

            # Ensure inventory table exists (read-only policy -> do NOT create)
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='inventory'")
            if not cur.fetchone():
                return None

            # Ensure expected columns
            cur.execute("PRAGMA table_info(inventory)")
            cols = {row[1] for row in cur.fetchall()}
            if not {"card_id", "quantity"}.issubset(cols):
                return None

            # Check for existing row
            cur.execute("SELECT quantity FROM inventory WHERE card_id=?", (str(card_id),))
            row = cur.fetchone()
            if row:
                return "RejectCard"

            # Insert a new row with quantity = 1
            cur.execute("INSERT INTO inventory(card_id, quantity) VALUES(?, ?)", (str(card_id), 1))
            conn.commit()
            return str(card_id)

    except sqlite3.Error:
        return None
    except Exception:
        return None


def CheckInventory(card, db_name: Optional[str] = None):
    """
    DB-first duplicate tracking:
      • If `inventory` table is present and the card already exists -> "RejectCard"
      • If not present, insert new row with quantity=1 and return the card (string)
      • If DB not usable, fallback to legacy file "Collection/Collection.txt"
    """
    result = _db_check_and_insert(card, db_name=db_name)
    if result is not None:
        return result

    # Legacy fallback
    _ensure_legacy_file()
    with open(LEGACY_PATH) as fh:
        if str(card) in fh.read():
            print('Found card')
            return "RejectCard"

    with open(LEGACY_PATH, "a") as fh:
        fh.write(f"{card}\n")
    print('Not found, added to your collection list')
    return card
