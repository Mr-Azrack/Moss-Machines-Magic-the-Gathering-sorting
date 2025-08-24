# hashing.py — SQLite-backed pHash matching with auto hash-size detection and RGB/single-column support
from __future__ import annotations
import sqlite3
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from PIL import Image, ImageOps
import imagehash

# ---------------- State ----------------
@dataclass
class _HashState:
    loaded: bool = False
    count: int = 0
    db_name: Optional[str] = None
    # What columns we found
    mode: str = "rgb"  # "rgb" or "single"
    id_col: str = "id"
    cols_rgb: Tuple[str, str, str] = ("r_phash", "g_phash", "b_phash")
    col_single: str = "phash"
    # Tables that matched the chosen mode
    tables: List[str] = None
    # For rgb mode: entries = [((table, id), rH, gH, bH)]
    # For single mode: entries = [((table, id), h)]
    entries: List[Tuple[Any, ...]] = None
    # Detected hash size (e.g., 16 => 256-bit)
    hash_n: int = 16

_STATE = _HashState(loaded=False, count=0, db_name=None, tables=[], entries=[], hash_n=16)

def get_status() -> Dict:
    return dict(
        loaded=_STATE.loaded,
        count=_STATE.count,
        db_name=_STATE.db_name,
        mode=_STATE.mode,
        tables=list(_STATE.tables or []),
        id_col=_STATE.id_col,
        cols_rgb=_STATE.cols_rgb if _STATE.mode == "rgb" else None,
        col_single=_STATE.col_single if _STATE.mode == "single" else None,
        hash_size=_STATE.hash_n,
    )

# ---------------- SQLite helpers ----------------
def _qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'

def _table_columns(conn: sqlite3.Connection, table: str):
    cur = conn.cursor()
    cur.execute(f'PRAGMA table_info({_qident(table)})')
    return [r[1] for r in cur.fetchall()]

def _candidate_tables(conn: sqlite3.Connection) -> List[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    return [r[0] for r in cur.fetchall()]

def _parse_hash(val) -> Optional[imagehash.ImageHash]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # Try hex first
    try:
        return imagehash.hex_to_hash(s)
    except Exception:
        pass
    # Try integer-to-hex
    try:
        i = int(s)
        return imagehash.hex_to_hash(format(i, 'x'))
    except Exception:
        return None

# ---------------- Load ----------------
def _load_hashes_from_db(db_name: str):
    _STATE.loaded = False
    _STATE.db_name = db_name
    _STATE.tables = []
    _STATE.entries = []
    _STATE.count = 0
    _STATE.mode = "rgb"
    _STATE.hash_n = 16  # default, will be overwritten by first parsed hash

    conn = sqlite3.connect(f"data/{db_name}")
    try:
        tables = _candidate_tables(conn)

        # First prefer rgb tables, else fallback to single 'phash' tables
        rgb_tables: List[str] = []
        single_tables: List[str] = []
        for t in tables:
            cols = set(_table_columns(conn, t))
            if {'id', 'r_phash', 'g_phash', 'b_phash'}.issubset(cols):
                rgb_tables.append(t)
            elif {'id', 'phash'}.issubset(cols):
                single_tables.append(t)

        cur = conn.cursor()

        if rgb_tables:
            _STATE.mode = "rgb"
            _STATE.tables = rgb_tables
            first_hash_size_set = False
            for t in rgb_tables:
                cur.execute(
                    f"SELECT {_qident('id')}, {_qident('r_phash')}, {_qident('g_phash')}, {_qident('b_phash')} "
                    f"FROM {_qident(t)}"
                )
                for rid, r_hex, g_hex, b_hex in cur.fetchall():
                    rH = _parse_hash(r_hex)
                    gH = _parse_hash(g_hex)
                    bH = _parse_hash(b_hex)
                    if rH is None or gH is None or bH is None:
                        continue
                    if not first_hash_size_set:
                        # infer hash_n from ImageHash shape
                        try:
                            _STATE.hash_n = int(getattr(rH, "hash").shape[0])
                            first_hash_size_set = True
                        except Exception:
                            pass
                    _STATE.entries.append(((t, int(rid)), rH, gH, bH))
                    _STATE.count += 1
        elif single_tables:
            _STATE.mode = "single"
            _STATE.tables = single_tables
            first_hash_size_set = False
            for t in single_tables:
                cur.execute(
                    f"SELECT {_qident('id')}, {_qident('phash')} FROM {_qident(t)}"
                )
                for rid, h_hex in cur.fetchall():
                    h = _parse_hash(h_hex)
                    if h is None:
                        continue
                    if not first_hash_size_set:
                        try:
                            _STATE.hash_n = int(getattr(h, "hash").shape[0])
                            first_hash_size_set = True
                        except Exception:
                            pass
                    _STATE.entries.append(((t, int(rid)), h))
                    _STATE.count += 1
        else:
            # Nothing matched
            _STATE.mode = "rgb"
            _STATE.tables = []
            _STATE.entries = []
            _STATE.count = 0

        _STATE.loaded = _STATE.count > 0
    finally:
        conn.close()

def ensure_loaded(db_name: Optional[str]):
    if not db_name:
        return
    if _STATE.loaded and _STATE.db_name == db_name and _STATE.count > 0:
        return
    _load_hashes_from_db(db_name)

# ---------------- Preprocess ----------------
_CENTER_CROP_RATIO = 1.0
def set_center_crop_ratio(r: float):
    global _CENTER_CROP_RATIO
    _CENTER_CROP_RATIO = max(0.5, min(1.0, float(r)))

def _center_crop(pil: Image.Image, ratio: float) -> Image.Image:
    if ratio >= 0.999:
        return pil
    w, h = pil.size
    cw, ch = int(w * ratio), int(h * ratio)
    x1 = (w - cw) // 2
    y1 = (h - ch) // 2
    return pil.crop((x1, y1, x1 + cw, y1 + ch))

def _preprocess(pil: Image.Image) -> Image.Image:
    img = pil.convert("RGB")
    img = _center_crop(img, _CENTER_CROP_RATIO)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img

# ---------------- Matching ----------------
def _hash_rgb(pil: Image.Image, n: int):
    r, g, b = pil.split()
    return (imagehash.phash(r, n), imagehash.phash(g, n), imagehash.phash(b, n))

def _hash_single(pil: Image.Image, n: int):
    # grayscale phash
    return imagehash.phash(pil.convert("L"), n)

def _dist_rgb(h1, h2) -> float:
    # average the Hamming distance across channels
    return float((h1[0] - h2[0]) + (h1[1] - h2[1]) + (h1[2] - h2[2])) / 3.0

def top_k_for_image(pil: Image.Image, k: int = 3, hash_size: Optional[int] = None):
    ensure_loaded(_STATE.db_name)
    if not _STATE.loaded or not _STATE.entries:
        return []
    img = _preprocess(pil)
    n = _STATE.hash_n if hash_size is None else int(hash_size)

    cand = []
    if _STATE.mode == "rgb":
        h = _hash_rgb(img, n)
        for (tbl_id, rH, gH, bH) in _STATE.entries:
            try:
                d = _dist_rgb(h, (rH, gH, bH))
            except Exception:
                # different hash sizes — skip
                continue
            cand.append((tbl_id, d))
    else:
        h = _hash_single(img, n)
        for (tbl_id, hH) in _STATE.entries:
            try:
                d = float(h - hH)
            except Exception:
                continue
            cand.append((tbl_id, d))

    cand.sort(key=lambda x: x[1])
    return cand[:k]

def hash_image_color(pil: Image.Image, hash_size: Optional[int] = None):
    res = top_k_for_image(pil, k=1, hash_size=hash_size)
    if not res:
        return None, 1e9
    return res[0][0], float(res[0][1])
