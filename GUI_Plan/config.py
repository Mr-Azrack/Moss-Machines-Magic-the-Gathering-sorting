# config.py
# Configuration settings for the card sorting application (SQLite-first).

import os

# ---------- Image / Processing ----------
CROP_SIZE = 745           # Size of the image crop for hashing (legacy)
WIDTH = 745               # Width of the perspective-corrected card image
HEIGHT = 1043             # Height of the perspective-corrected card image
MAX_DISTANCE_THRESHOLD = 100  # Hashing match threshold (Hamming distance average)

# ---------- Database (SQLite) ----------
# All .db files are stored under ./data next to this file.
BASE_DIR = os.path.dirname(__file__)
DB_DATA_DIR = os.path.join(BASE_DIR, "data")

# Table where precomputed perceptual hashes (pHash) are stored.
# We'll auto-detect from the following candidates, in order.
HASH_TABLE_CANDIDATES = ["hashes", "card_hashes", "image_hashes"]

# Column name candidates for the RGB pHashes. Values are expected to be
# hex strings produced by str(imagehash.phash(...)).
HASH_COLUMN_SETS = [
    ("r_phash", "g_phash", "b_phash"),
    ("r_hash", "g_hash", "b_hash"),
    ("r", "g", "b"),
]

# Card identifier column candidates
HASH_ID_COLUMN_CANDIDATES = ["id", "card_id", "scryfall_id"]

# Optional: default hash size used by pHash
DEFAULT_HASH_SIZE = 16

# ---------- Legacy paths (deprecated; retained for backward-compat) ----------
# Old JSON-based precomputed hashes file. No longer used when SQLite is present.
HASH_DB_PATH = os.path.join(BASE_DIR, "card_hashes.json")
IMAGES_DIR = os.path.join(BASE_DIR, "downloaded_cards")
LAYOUT_SIGNATURES_JSON = os.path.join(BASE_DIR, "layout_signatures.json")

# ---------- Sorting behavior ----------
# Exclude these sets when deciding eligibility.
EXCLUDED_SETS = {"30a", "lea", "leb", "fbb", "ced", "cei", "4bb", "ptc", "sum"}

# Map UI labels and CLI numeric choices to internal sort modes used by the sorter.
# main_sqlite.py expects a dict and does: SORTING_MODES.get(choice, "color")
SORTING_MODES = {
    # CLI numeric keys
    "1": "color",
    "2": "mana_value",
    "3": "price",
    "4": "set",
    "5": "type",
    "6": "buy",
    # Human-readable keys (GUI)
    "Color": "color",
    "Mana Value": "mana_value",
    "Price": "price",
    "Set": "set",
    "Type": "type",
    "Buy (by price)": "buy",
}

# ---------- Serial communication ----------
SERIAL_PORT = "COM3"   # Update as appropriate on your machine
BAUD_RATE = 9600
START_MARKER = 60
END_MARKER = 62

# ---------- Name detection ----------
MAX_ATTEMPTS_NAME = 5
TIMEOUT_NAME = 10  # seconds

# ---------- Model ----------
MODEL_PATH = os.path.join(BASE_DIR, "mana_v14.pt")
