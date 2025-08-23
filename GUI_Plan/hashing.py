# hashing.py — SQLite-backed pHash lookup across ALL set tables
# Loads from any table that contains: id, r_phash, g_phash, b_phash

import os
import sqlite3
import logging
from typing import List, Tuple, Optional, Any, Dict

import imagehash
from PIL import Image

# Prefer shared DB helpers if available
try:
    from database import list_db_files, get_db_path, get_connection, DATA_DIR as _DB_DATA_DIR
except Exception:  # pragma: no cover
    list_db_files = None
    get_db_path = None
    get_connection = None
    _DB_DATA_DIR = None

from config import (
    DB_DATA_DIR,
    HASH_TABLE_CANDIDATES,
    HASH_COLUMN_SETS,
    HASH_ID_COLUMN_CANDIDATES,
    DEFAULT_HASH_SIZE,
)

logger = logging.getLogger(__name__)

# ---------- Internal state ----------
# Store (table, id, r_hash, g_hash, b_hash)
PRECOMPUTED_HASHES: List[Tuple[str, Any, imagehash.ImageHash, imagehash.ImageHash, imagehash.ImageHash]] = []
_LOADED_DB_NAME: Optional[str] = None
_LOADED_TABLES: List[str] = []
_LOADED_COLS: Optional[Tuple[str, str, str]] = None  # r,g,b column names (all tables share these)
_LOADED_ID_COL: Optional[str] = None

# Accept any table that has these columns
RGB_COL_CHOICES = list(HASH_COLUMN_SETS) + [
    ("r_phash", "g_phash", "b_phash"),
    ("r_hash", "g_hash", "b_hash"),
    ("r", "g", "b"),
]
ID_COL_CHOICES = list(dict.fromkeys(HASH_ID_COLUMN_CANDIDATES + ["id", "card_id", "uuid"]))



def _qident(name: str) -> str:
    # Quote an identifier for SQLite (double quotes, escape embedded quotes)
    return '"' + str(name).replace('"', '""') + '"'

# ---------- DB helpers ----------

def _resolve_db_path(db_name: Optional[str]) -> Optional[str]:
    """Resolve a DB filename to an absolute path inside ./data (or absolute if given)."""
    forced = os.environ.get("HASH_DB_PATH")
    if forced and os.path.exists(forced):
        return forced

    if db_name:
        if os.path.isabs(db_name) and os.path.exists(db_name):
            return db_name
        data_dir = _DB_DATA_DIR or DB_DATA_DIR or os.path.join(os.path.dirname(__file__), "data")
        return os.path.join(data_dir, db_name)

    # Try selection from main_sqlite
    try:
        import main_sqlite  # type: ignore
        sel = getattr(main_sqlite, 'SELECTED_DB_NAME', None)
        if sel:
            if os.path.isabs(sel) and os.path.exists(sel):
                return sel
            data_dir = _DB_DATA_DIR or DB_DATA_DIR or os.path.join(os.path.dirname(__file__), "data")
            cand = os.path.join(data_dir, sel)
            if os.path.exists(cand):
                return cand
    except Exception:
        pass

    if get_db_path:
        try:
            p = get_db_path(None)
            if p and os.path.exists(p):
                return p
        except Exception:
            pass

    data_dir = _DB_DATA_DIR or DB_DATA_DIR or os.path.join(os.path.dirname(__file__), "data")
    if os.path.isdir(data_dir):
        dbs = [f for f in os.listdir(data_dir) if f.lower().endswith(".db")]
        if dbs:
            return os.path.join(data_dir, dbs[0])

    logger.warning("hashing: no .db found or path invalid")
    return None


def _open_conn(db_path: str) -> sqlite3.Connection:
    if get_connection:
        try:
            return get_connection(os.path.basename(db_path))
        except Exception:
            pass
    conn = sqlite3.connect(db_path)
    # Do not rely on row_factory; we'll use cursor.description to be robust.
    return conn


def _list_tables(conn: sqlite3.Connection) -> List[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [r[0] for r in cur.fetchall()]


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA table_info({_qident.__name__}(table))")
    except Exception:
        # Fallback to raw quoting
        cur.execute(f'PRAGMA table_info("{table}")')
    return [r[1] for r in cur.fetchall()]


def _to_hash(value: Any) -> Optional[imagehash.ImageHash]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.hex()
        except Exception:
            value = value.decode('utf-8', errors='ignore')
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return None
        if s.startswith('0x'):
            s = s[2:]
        try:
            return imagehash.hex_to_hash(s)
        except Exception:
            s = ''.join(ch for ch in s if ch in '0123456789abcdef')
            if not s:
                return None
            try:
                return imagehash.hex_to_hash(s)
            except Exception:
                return None
    if isinstance(value, int):
        hx = format(value, 'x')
        try:
            return imagehash.hex_to_hash(hx)
        except Exception:
            return None
    try:
        return imagehash.hex_to_hash(str(value))
    except Exception:
        return None


def _load_hashes_from_db(db_name: Optional[str]) -> bool:
    """
    Load hashes from ALL tables that have (id, r_phash, g_phash, b_phash)-style schema.
    Uses cursor.description so it works regardless of row_factory.
    """
    global PRECOMPUTED_HASHES, _LOADED_DB_NAME, _LOADED_TABLES, _LOADED_COLS, _LOADED_ID_COL

    PRECOMPUTED_HASHES.clear()
    _LOADED_DB_NAME = None
    _LOADED_TABLES = []
    _LOADED_COLS = None
    _LOADED_ID_COL = None

    db_path = _resolve_db_path(db_name)
    if not db_path or not os.path.exists(db_path):
        logger.warning("hashing: no .db found or path invalid")
        return False

    try:
        with _open_conn(db_path) as conn:
            tables = _list_tables(conn)
            tables = [t for t in tables if not t.startswith("sqlite_")]

            any_loaded = False
            chosen_cols: Optional[Tuple[str, str, str]] = None
            chosen_id: Optional[str] = None

            for table in tables:
                cols = _table_columns(conn, table)
                id_col = next((c for c in ID_COL_CHOICES if c in cols), None)
                if not id_col:
                    continue
                rgb = next((rgb for rgb in RGB_COL_CHOICES if all(c in cols for c in rgb)), None)
                if not rgb:
                    continue

                if chosen_cols is None:
                    chosen_cols = rgb
                if chosen_id is None:
                    chosen_id = id_col

                r_col, g_col, b_col = rgb
                cur = conn.cursor()
                try:
                    cur.execute(f'SELECT "{id_col}", "{r_col}", "{g_col}", "{b_col}" FROM "{table}"')
                except sqlite3.Error:
                    # Last-resort unquoted (shouldn't happen given we quoted above)
                    cur.execute(f"SELECT {id_col}, {r_col}, {g_col}, {b_col} FROM {table}")
                rows = cur.fetchall()
                # Build a column index map from description
                col_names = [d[0] for d in cur.description]
                try:
                    idx_id = col_names.index(id_col)
                    idx_r = col_names.index(r_col)
                    idx_g = col_names.index(g_col)
                    idx_b = col_names.index(b_col)
                except ValueError:
                    # If names are case-sensitive differences, fall back to lowercase compare
                    lname_map = {n.lower(): i for i, n in enumerate(col_names)}
                    idx_id = lname_map.get(id_col.lower())
                    idx_r = lname_map.get(r_col.lower())
                    idx_g = lname_map.get(g_col.lower())
                    idx_b = lname_map.get(b_col.lower())
                    if None in (idx_id, idx_r, idx_g, idx_b):
                        logger.warning(f"hashing: could not map columns for table {table}; skipping")
                        continue

                loaded_here = 0
                for row in rows:
                    rid = row[idx_id]
                    r = _to_hash(row[idx_r])
                    g = _to_hash(row[idx_g])
                    b = _to_hash(row[idx_b])
                    if r is not None and g is not None and b is not None:
                        PRECOMPUTED_HASHES.append((table, rid, r, g, b))
                        loaded_here += 1

                if loaded_here:
                    _LOADED_TABLES.append(table)
                    any_loaded = True
                    logger.info(f"hashing: loaded {loaded_here} hashes from table {table}")

            if not any_loaded:
                logger.warning("hashing: found tables but 0 rows with usable hashes")
                return False

            _LOADED_DB_NAME = os.path.basename(db_path)
            _LOADED_COLS = chosen_cols
            _LOADED_ID_COL = chosen_id
            logger.info(f"hashing: total loaded hashes: {len(PRECOMPUTED_HASHES)} across {len(_LOADED_TABLES)} tables")
            return True

    except sqlite3.Error as e:
        logger.exception(f"hashing: sqlite error while loading hashes: {e}")
        return False


def ensure_loaded(db_name: Optional[str] = None) -> bool:
    if PRECOMPUTED_HASHES and (db_name is None or db_name == _LOADED_DB_NAME):
        return True
    return _load_hashes_from_db(db_name)


# ---------- Visibility helpers ----------

def get_status() -> Dict[str, Any]:
    """Return loader status for debugging/telemetry."""
    return {
        'loaded': bool(PRECOMPUTED_HASHES),
        'count': len(PRECOMPUTED_HASHES),
        'db_name': _LOADED_DB_NAME,
        'tables': list(_LOADED_TABLES),
        'cols_rgb': _LOADED_COLS,
        'id_col': _LOADED_ID_COL,
    }


def top_k_for_image(img: Image.Image, k: int = 3, hash_size: int = DEFAULT_HASH_SIZE) -> List[Tuple[Tuple[str, Any], float]]:
    """Return top-k nearest neighbors by avg RGB pHash distance."""
    if not PRECOMPUTED_HASHES:
        ensure_loaded(None)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    r, g, b = img.split()
    r_hash = imagehash.phash(r, hash_size)
    g_hash = imagehash.phash(g, hash_size)
    b_hash = imagehash.phash(b, hash_size)
    distances: List[Tuple[Tuple[str, Any], float]] = []
    for table, rid, sr, sg, sb in PRECOMPUTED_HASHES:
        avg = ((r_hash - sr) + (g_hash - sg) + (b_hash - sb)) / 3.0
        distances.append(((table, rid), avg))
    distances.sort(key=lambda x: x[1])
    return distances[:k]


# ---------- Public API ----------

def hash_image_color(img: Image.Image, hash_size: int = DEFAULT_HASH_SIZE) -> Tuple[Optional[Tuple[str, Any]], float]:
    """
    Returns ((table_name, id), avg_distance)
    """
    if not PRECOMPUTED_HASHES:
        ensure_loaded(None)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    r, g, b = img.split()
    r_hash = imagehash.phash(r, hash_size)
    g_hash = imagehash.phash(g, hash_size)
    b_hash = imagehash.phash(b, hash_size)

    best_key: Optional[Tuple[str, Any]] = None
    best_dist = float('inf')
    for table, rid, sr, sg, sb in PRECOMPUTED_HASHES:
        avg = ((r_hash - sr) + (g_hash - sg) + (b_hash - sb)) / 3.0
        if avg < best_dist:
            best_dist = avg
            best_key = (table, rid)
    return best_key, best_dist


def compute_distances_for_image(img: Image.Image, hash_size: int = DEFAULT_HASH_SIZE) -> List[Tuple[Tuple[str, Any], float]]:
    """Return list of ((table, id), avg_distance) for all known hashes."""
    if not PRECOMPUTED_HASHES:
        ensure_loaded(None)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    r, g, b = img.split()
    r_hash = imagehash.phash(r, hash_size)
    g_hash = imagehash.phash(g, hash_size)
    b_hash = imagehash.phash(b, hash_size)
    out: List[Tuple[Tuple[str, Any], float]] = []
    for table, rid, sr, sg, sb in PRECOMPUTED_HASHES:
        avg = ((r_hash - sr) + (g_hash - sg) + (b_hash - sb)) / 3.0
        out.append(((table, rid), avg))
    return out


# Preload at import (best-effort)
try:
    ensure_loaded(None)
except Exception:
    pass
