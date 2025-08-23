# db_inspect.py — SQLite schema + hash-table detector
# Put this file next to your project (same folder as main_sqlite.py) and run:
#   python db_inspect.py
# You can also specify a DB:
#   python db_inspect.py --db myhashes.db
# Or an absolute path:
#   python db_inspect.py --db "D:\path\to\your.db"
#
# It tries to import config/database to stay cohesive with your codebase.

import os
import sys
import argparse
import sqlite3
from typing import List, Tuple, Optional

# ---- Try to use your project’s config/database for cohesion ----
HASH_TABLE_CANDIDATES = ["hashes", "card_hashes", "image_hashes"]
HASH_COLUMN_SETS = [("r_phash", "g_phash", "b_phash"),
                    ("r_hash", "g_hash", "b_hash"),
                    ("r", "g", "b")]
HASH_ID_COLUMN_CANDIDATES = ["id", "card_id", "scryfall_id"]

try:
    from config import HASH_TABLE_CANDIDATES as _HTC, HASH_COLUMN_SETS as _HCS, HASH_ID_COLUMN_CANDIDATES as _HIC
    HASH_TABLE_CANDIDATES = list(_HTC)
    HASH_COLUMN_SETS = list(_HCS)
    HASH_ID_COLUMN_CANDIDATES = list(_HIC)
except Exception:
    pass

EXTRA_TABLE_CANDS = [
    "hash", "hash_table", "phashes", "card_hash", "card_hashes_rgb",
    "card_image_hashes", "hashes_rgb", "phash_rgb", "phash_values",
    "scryfall_hashes", "hashdata", "card_images",
]
EXTRA_ID_CANDS = [
    "uuid", "scryfallId", "scryfall_id", "oracle_id", "collector_number",
    "multiverse_id", "cardId", "card_id", "id",
]
SINGLE_COL_CANDS = [
    "phash", "p_hash", "avg_phash", "hash", "hash_hex", "hash_value", "pHash",
]

def find_db_path(cli_db: Optional[str]) -> Optional[str]:
    """Resolve a DB path using CLI arg, env var, database helpers, or ./data lookup."""
    # 1) CLI arg
    if cli_db:
        if os.path.isabs(cli_db) and os.path.exists(cli_db):
            return cli_db
        # if it's a plain name, try ./data/<name>
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        cand = os.path.join(data_dir, cli_db)
        return cand if os.path.exists(cand) else cli_db

    # 2) Env var (same as hashing.py override)
    forced = os.environ.get("HASH_DB_PATH")
    if forced and os.path.exists(forced):
        return forced

    # 3) database.get_db_path / list_db_files
    try:
        from database import get_db_path, list_db_files
        try:
            p = get_db_path(None)
            if p and os.path.exists(p):
                return p
        except Exception:
            pass
        try:
            dbs = list_db_files()
            if dbs:
                data_dir = os.path.join(os.path.dirname(__file__), "data")
                for name in dbs:
                    cand = os.path.join(data_dir, name)
                    if os.path.exists(cand):
                        return cand
        except Exception:
            pass
    except Exception:
        pass

    # 4) First *.db under ./data
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    if os.path.isdir(data_dir):
        for f in os.listdir(data_dir):
            if f.lower().endswith(".db"):
                return os.path.join(data_dir, f)

    return None

def list_tables(conn: sqlite3.Connection) -> List[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [r[0] for r in cur.fetchall()]

def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]

def detect_hash_table(conn: sqlite3.Connection) -> Optional[Tuple[str, Tuple[str, str, str], str, bool]]:
    """
    Try to detect which table holds hashes.
    Returns: (table, (r,g,b), id_col, single_col_mode)
      - single_col_mode=True means (r,g,b) are actually the same column name reused.
    """
    tables = list_tables(conn)
    # Build ordered candidate list
    table_cands = [t for t in tables if t in HASH_TABLE_CANDIDATES]
    table_cands += [t for t in tables if t in EXTRA_TABLE_CANDS]
    # Try every table
    for t in table_cands:
        cols = table_columns(conn, t)
        # pick id column
        id_col = None
        for c in list(dict.fromkeys(HASH_ID_COLUMN_CANDIDATES + EXTRA_ID_CANDS)):
            if c in cols:
                id_col = c
                break
        if not id_col:
            continue
        # RGB set?
        for r, g, b in HASH_COLUMN_SETS:
            if r in cols and g in cols and b in cols:
                return t, (r, g, b), id_col, False
        # Single column fallback
        for single in SINGLE_COL_CANDS:
            if single in cols:
                return t, (single, single, single), id_col, True
    return None

def sample_rows(conn: sqlite3.Connection, table: str, id_col: str, cols: Tuple[str, str, str], n: int = 3):
    cur = conn.cursor()
    r, g, b = cols
    try:
        cur.execute(f"SELECT {id_col}, {r}, {g}, {b} FROM {table} LIMIT {n}")
        return cur.fetchall()
    except Exception:
        # if it was a single-column case but DB can't SELECT same column 3x with aliases,
        # try selecting just id_col and the first column once
        try:
            if r == g == b:
                cur.execute(f"SELECT {id_col}, {r} FROM {table} LIMIT {n}")
                return cur.fetchall()
        except Exception:
            pass
    return []

def main():
    parser = argparse.ArgumentParser(description="Inspect SQLite schema and detect hash table/columns.")
    parser.add_argument("--db", help="DB filename or absolute path (optional).")
    args = parser.parse_args()

    db_path = find_db_path(args.db)
    if not db_path or not os.path.exists(db_path):
        print("No database found. Try: python db_inspect.py --db your.db")
        sys.exit(2)

    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        tables = list_tables(conn)
        if not tables:
            print("No tables in this database.")
            return

        print("\n=== Tables ===")
        for t in tables:
            print(f"- {t}")

        print("\n=== Columns per table ===")
        for t in tables:
            cols = table_columns(conn, t)
            print(f"{t}: {cols}")

        print("\n=== Hash table detection ===")
        det = detect_hash_table(conn)
        if not det:
            print("!! No suitable hash table/columns matched the known candidates.")
            print("   Use the column lists above and tell me which table & columns hold:")
            print("   - the unique card id (e.g., 'id', 'card_id', 'uuid', ...)")
            print("   - either RGB pHash columns (e.g., r_phash/g_phash/b_phash) OR a single 'phash' column")
            return

        table, cols, id_col, single_mode = det
        print(f"LIKELY HASH TABLE: {table}")
        if single_mode:
            print(f"  id_col = {id_col}")
            print(f"  single phash column = {cols[0]}  (will be reused for R/G/B)")
        else:
            print(f"  id_col = {id_col}")
            print(f"  r,g,b columns = {cols}")

        # Sample a few rows
        rows = sample_rows(conn, table, id_col, cols, n=5)
        if rows:
            print("\nSample rows:")
            for r in rows:
                try:
                    # Row is sqlite3.Row; show keys dynamically
                    keys = r.keys()
                    preview = {k: (str(r[k])[:40] + "..." if r[k] and len(str(r[k])) > 43 else r[k]) for k in keys}
                    print(" ", preview)
                except Exception:
                    print(" ", tuple(r))
        else:
            print("\n(No sample rows fetched; table may be empty or columns need aliasing.)")

        print("\nIf this detection looks correct, you can run with environment overrides (Windows cmd):")
        if single_mode:
            print(f'  set HASH_TABLE={table}')
            print(f'  set HASH_IDCOL={id_col}')
            print(f'  set HASH_RCOL={cols[0]}')
        else:
            print(f'  set HASH_TABLE={table}')
            print(f'  set HASH_IDCOL={id_col}')
            print(f'  set HASH_RCOL={cols[0]}')
            print(f'  set HASH_GCOL={cols[1]}')
            print(f'  set HASH_BCOL={cols[2]}')
        print("  python main_sqlite.py")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
