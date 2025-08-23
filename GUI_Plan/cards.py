# cards.py — extract card info from per-set tables (e.g., AU1E, EE1E, ...)
# Accepts a composite key (table_name, id) and normalizes fields for sorting.

import os
import sqlite3
from typing import Any, Dict, Optional, Tuple, List

from config import EXCLUDED_SETS, DB_DATA_DIR

# If you have database helpers, use them
try:
    from database import get_db_path, get_connection
except Exception:
    get_db_path = None
    get_connection = None


def _db_path(db_name: Optional[str]) -> Optional[str]:
    if not db_name:
        # Try helper
        if get_db_path:
            try:
                p = get_db_path(None)
                if p and os.path.exists(p):
                    return p
            except Exception:
                pass
        # Fallback: first .db in ./data
        try:
            files = [f for f in os.listdir(DB_DATA_DIR) if f.lower().endswith(".db")]
            if files:
                return os.path.join(DB_DATA_DIR, files[0])
        except Exception:
            return None

    # accept absolute or join into /data
    if os.path.isabs(db_name or "") and os.path.exists(db_name):
        return db_name
    cand = os.path.join(DB_DATA_DIR, db_name or "")
    return cand if os.path.exists(cand) else None


def _open(db_path: str) -> sqlite3.Connection:
    if get_connection:
        try:
            return get_connection(os.path.basename(db_path))
        except Exception:
            pass
    conn = sqlite3.connect(db_path)
    # We'll convert rows to dict via cursor.description, so no row_factory needed.
    return conn


def _normalize_row(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize Akora-style columns to the keys used by sorting.py:
      - Name -> str
      - Colors -> list[str] (use extAttribute if present)
      - Types -> list[str] (extCardType, subTypeName)
      - Set -> table name (lowercased)
      - Price -> numeric (use marketPrice, fallback midPrice/highPrice/lowPrice)
      - CMC/mana_cost not available; leave absent.
    """
    def _fnum(val):
        try:
            return float(val)
        except Exception:
            return None

    name = row.get("name")
    ext_attr = row.get("extAttribute")
    ext_type = row.get("extCardType")
    sub_type = row.get("subTypeName")

    price = (
        _fnum(row.get("marketPrice")) or
        _fnum(row.get("midPrice")) or
        _fnum(row.get("highPrice")) or
        _fnum(row.get("lowPrice"))
    )

    colors: List[str] = []
    if ext_attr:
        colors = [str(ext_attr)]

    types: List[str] = []
    for t in (ext_type, sub_type):
        if t:
            types.append(str(t))

    info: Dict[str, Any] = {
        "Name": name or "Unknown",
        "Colors": colors,
        "Types": types,
        "Set": table.lower(),
        "Price": price,
        # Keep raw fields too
        "extAttribute": row.get("extAttribute"),
        "extCardType": row.get("extCardType"),
        "extNumber": row.get("extNumber"),
        "extRarity": row.get("extRarity"),
        "extSubType": row.get("extSubType"),
        "marketPrice": row.get("marketPrice"),
        "midPrice": row.get("midPrice"),
        "highPrice": row.get("highPrice"),
        "lowPrice": row.get("lowPrice"),
        "subTypeName": row.get("subTypeName"),
    }
    return info


def extract_card_info(card_key: Any, db_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    card_key can be:
      - (table_name, id) tuple  ← what hashing.py returns
      - "TABLE:ID" string (case-sensitive for table)
      - plain id (int/str)  ← fallback: first match found across all tables
    """
    db_path = _db_path(db_name)
    if not db_path:
        return None

    # Decode key
    table: Optional[str] = None
    rid: Optional[Any] = None
    if isinstance(card_key, tuple) and len(card_key) == 2:
        table, rid = card_key
    elif isinstance(card_key, str) and ":" in card_key:
        table, sid = card_key.split(":", 1)
        try:
            rid = int(sid)
        except Exception:
            rid = sid
    else:
        rid = card_key

    try:
        with _open(db_path) as conn:
            cur = conn.cursor()

            def read_one(tbl: str, row_id: Any) -> Optional[Dict[str, Any]]:
                try:
                    cur.execute(f'SELECT * FROM "{tbl}" WHERE id = ? LIMIT 1', (row_id,))
                    r = cur.fetchone()
                    if r is None:
                        return None
                    col_names = [d[0] for d in cur.description]
                    row_dict = {col_names[i]: r[i] for i in range(len(col_names))}
                    return _normalize_row(tbl, row_dict)
                except Exception:
                    return None

            if table and rid is not None:
                return read_one(table, rid)

            # Fallback scan across all tables if table unknown
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            all_tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]
            for tbl in all_tables:
                info = read_one(tbl, rid)
                if info:
                    return info

            return None
    except Exception:
        return None


def card_is_allowed(card_key: Any, db_name: Optional[str] = None) -> bool:
    """
    Filter by excluded set codes. Here, the 'set' is effectively the table name.
    """
    info = extract_card_info(card_key, db_name=db_name)
    if not info:
        return False
    set_code = str(info.get("Set", "")).lower()
    return set_code not in {s.lower() for s in EXCLUDED_SETS}
