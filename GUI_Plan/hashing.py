# hashing.py
"""
SQLite-backed image hashing for card identification.

Builds an in-memory index of perceptual hashes (pHash) directly from the
SELECTED SQLite database (chosen in the GUI). No JSON files are used.

Public API:
    - ensure_loaded_for_db(db_name, on_status=None) -> int
    - compute_distances_for_image(PIL.Image, hash_size=16, top_k=10) -> [(card_id, distance), ...]
    - hash_image_color(PIL.Image, hash_size=16) -> (best_id, distance) or (None, None)

Features:
- Scans ALL tables and auto-detects hash columns:
  * Triplets: r/g/b pHash (accepts many aliases, case-insensitive)
  * Single-column fallback: phash → replicated to R/G/B
- **Quotes identifiers** so tables like "4ED", "10E" are handled correctly.
- Accepts hex strings or ints; normalizes to imagehash.ImageHash.
- Detailed per-table logging (+loaded / skipped).
"""
from __future__ import annotations

import os
import sqlite3
from typing import Callable, List, Tuple, Optional, Any, Set, Dict

from PIL import Image
import imagehash

# Optional: use database.get_db_path if available; otherwise fall back to ./data/<db_name>
try:
    from database import get_db_path as _get_db_path  # type: ignore
except Exception:
    _get_db_path = None  # resolved locally

# In-memory cache: (card_id, r_hash, g_hash, b_hash)
PRECOMPUTED_HASHES: List[Tuple[Any, imagehash.ImageHash, imagehash.ImageHash, imagehash.ImageHash]] = []
LAST_LOADED_DB: Optional[str] = None


# ---------------- Path resolution ----------------

def _resolve_db_path(db_name: str) -> str:
    if not db_name:
        raise ValueError("db_name is empty")
    if os.path.isabs(db_name) and os.path.isfile(db_name):
        return db_name
    if os.path.isfile(db_name):
        return os.path.abspath(db_name)
    if _get_db_path:
        try:
            p = _get_db_path(db_name)  # typically ./data/<db_name>
            if os.path.isfile(p):
                return os.path.abspath(p)
        except Exception:
            pass
    base = os.path.join(os.path.dirname(__file__), "data")
    return os.path.abspath(os.path.join(base, db_name))


# ---------------- SQL identifier quoting ----------------

def _qident(name: str) -> str:
    """Quote an SQLite identifier with double-quotes, escaping embedded quotes."""
    return '"' + str(name).replace('"', '""') + '"'


# ---------------- Hash helpers ----------------

def _to_hash_or_none(v: Optional[str | int]) -> Optional[imagehash.ImageHash]:
    """Accept a hex string or int and convert to ImageHash; return None if invalid/empty."""
    if v is None:
        return None
    try:
        if isinstance(v, int):
            hx = format(v, "x")
            if len(hx) % 2:
                hx = "0" + hx
            return imagehash.hex_to_hash(hx)
        s = str(v).strip()
        if not s:
            return None
        if s.startswith(("0x", "0X")):
            s = s[2:]
        return imagehash.hex_to_hash(s)
    except Exception:
        return None


def _color_hash_triplet(img: Image.Image, hash_size: int = 16):
    """Return (r_phash, g_phash, b_phash) for the image."""
    r, g, b = img.convert("RGB").split()
    return (
        imagehash.phash(r, hash_size=hash_size),
        imagehash.phash(g, hash_size=hash_size),
        imagehash.phash(b, hash_size=hash_size),
    )


# Column name variants (lowercased) we’ll accept
_R_ALIASES = {"r_phash", "rhash", "r_hash", "phash_r", "rphash"}
_G_ALIASES = {"g_phash", "ghash", "g_hash", "phash_g", "gphash"}
_B_ALIASES = {"b_phash", "bhash", "b_hash", "phash_b", "bphash"}
_SINGLE_ALIASES = {"phash", "p_hash"}  # if only one exists, copy into R/G/B

def _find_hash_columns(cols_lower: List[str]) -> Optional[Dict[str, str]]:
    """
    Return {'r': colname, 'g': colname, 'b': colname} mapping (lowercased),
    or {'single': colname} if only a grayscale 'phash' column exists.
    """
    r = next((c for c in cols_lower if c in _R_ALIASES), None)
    g = next((c for c in cols_lower if c in _G_ALIASES), None)
    b = next((c for c in cols_lower if c in _B_ALIASES), None)
    if r and g and b:
        return {"r": r, "g": g, "b": b}
    single = next((c for c in cols_lower if c in _SINGLE_ALIASES), None)
    if single:
        return {"single": single}
    return None


# ---------------- Index building ----------------

def load_hashes_from_sqlite(db_name: str, on_status: Optional[Callable[[str], None]] = None) -> int:
    """
    Load pHash triplets from ALL tables that contain:
      - 'id', and
      - either (r,g,b) pHash columns (accepted aliases), or a single 'phash' column.
    Returns the number of entries loaded.
    """
    global PRECOMPUTED_HASHES, LAST_LOADED_DB

    def report(msg: str) -> None:
        (on_status or print)(msg)

    PRECOMPUTED_HASHES = []
    LAST_LOADED_DB = None

    db_path = _resolve_db_path(db_name)
    if not os.path.isfile(db_path):
        raise RuntimeError(f"Selected DB not found: {db_path}")

    report(f"[hashing] Indexing hashes from {db_path} …")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # List tables
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r["name"] for r in cur.fetchall()]
    except Exception as e:
        con.close()
        raise RuntimeError(f"Unable to list tables in DB: {e}")

    candidate_tables: List[Tuple[str, Dict[str, str], Dict[str, str]]] = []
    # tuple = (table_name, mapping_lower -> 'r/g/b' or 'single', original_name_map)
    # original_name_map maps lowercased column name -> original-cased column name

    for t in tables:
        try:
            cur.execute(f"PRAGMA table_info({_qident(t)})")
            pragma_rows = cur.fetchall()
            cols_orig = [row["name"] for row in pragma_rows]
            cols_lower = [c.lower() for c in cols_orig]
            if "id" not in cols_lower:
                continue
            mapping_lower = _find_hash_columns(cols_lower)
            if mapping_lower:
                to_orig = {c.lower(): c for c in cols_orig}
                candidate_tables.append((t, mapping_lower, to_orig))
        except Exception:
            # if PRAGMA fails due to bad quoting etc., skip
            continue

    if not candidate_tables:
        con.close()
        raise RuntimeError(
            "No table with required columns found. Need 'id' and either "
            "(r,g,b) pHash triplets or a single 'phash' column."
        )

    total_loaded = 0
    seen_ids: Set[Any] = set()
    report(f"[hashing] Found {len(candidate_tables)} hash tables: " + ", ".join(t for t, _, _ in candidate_tables))

    for t, mapping_lower, to_orig in candidate_tables:
        loaded_this = 0
        skipped_this = 0

        if "single" in mapping_lower:
            single_col = to_orig[mapping_lower["single"]]
            sel = f"SELECT {_qident('id')} AS id, {_qident(single_col)} AS single_phash FROM {_qident(t)}"
            desc = "single"
        else:
            r_col = to_orig[mapping_lower["r"]]
            g_col = to_orig[mapping_lower["g"]]
            b_col = to_orig[mapping_lower["b"]]
            sel = (
                f"SELECT {_qident('id')} AS id, "
                f"{_qident(r_col)} AS r_phash, "
                f"{_qident(g_col)} AS g_phash, "
                f"{_qident(b_col)} AS b_phash "
                f"FROM {_qident(t)}"
            )
            desc = "triplet"

        try:
            cur.execute(sel)
            rows = cur.fetchall()
        except Exception as e:
            report(f"[hashing] Skipping table '{t}' due to read error: {e}")
            continue

        for row in rows:
            cid = row["id"]
            if cid in seen_ids:
                continue

            if "single" in mapping_lower:
                h = _to_hash_or_none(row["single_phash"])
                if h is None:
                    skipped_this += 1
                    continue
                rr = gg = bb = h
            else:
                rr = _to_hash_or_none(row["r_phash"])
                gg = _to_hash_or_none(row["g_phash"])
                bb = _to_hash_or_none(row["b_phash"])
                if rr is None or gg is None or bb is None:
                    skipped_this += 1
                    continue

            PRECOMPUTED_HASHES.append((cid, rr, gg, bb))
            seen_ids.add(cid)
            total_loaded += 1
            loaded_this += 1

        report(f"[hashing] Loading from '{t}' ({desc}) … +{loaded_this} entries (skipped {skipped_this})")

    con.close()
    LAST_LOADED_DB = os.path.abspath(db_path)
    report(f"[hashing] Loaded {total_loaded} total pHash entries from {len(candidate_tables)} tables.")
    return total_loaded


def ensure_loaded_for_db(db_name: str, on_status: Optional[Callable[[str], None]] = None) -> int:
    """
    Ensure the in-memory hash index corresponds to the given DB.
    If not loaded or DB changed, reload it. Returns current count.
    """
    path = _resolve_db_path(db_name)
    if LAST_LOADED_DB != os.path.abspath(path) or not PRECOMPUTED_HASHES:
        return load_hashes_from_sqlite(db_name, on_status=on_status)
    return len(PRECOMPUTED_HASHES)


# ---------------- Matching ----------------

def compute_distances_for_image(img: Image.Image, hash_size: int = 16, top_k: int = 10) -> List[Tuple[Any, int]]:
    """
    Compute Hamming distance between img's color pHash triplet and all precomputed hashes.
    Returns a sorted list of (card_id, total_distance). Lower is better.
    """
    if not PRECOMPUTED_HASHES:
        raise RuntimeError("Hash index not loaded yet. Call ensure_loaded_for_db(db_name) first.")
    rh, gh, bh = _color_hash_triplet(img, hash_size=hash_size)
    dists: List[Tuple[Any, int]] = []
    for cid, rr, gg, bb in PRECOMPUTED_HASHES:
        d = (rh - rr) + (gh - gg) + (bh - bb)
        dists.append((cid, int(d)))
    dists.sort(key=lambda t: t[1])
    return dists[:top_k] if top_k else dists


def hash_image_color(img: Image.Image, hash_size: int = 16) -> Tuple[Optional[Any], Optional[int]]:
    """
    Return the best single match (card_id, total_distance) or (None, None).
    """
    top = compute_distances_for_image(img, hash_size=16, top_k=1)
    return top[0] if top else (None, None)
