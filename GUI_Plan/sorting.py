# sorting.py — key-tolerant helpers compatible with DB-backed rows

import json
import cv2

# --- Key helpers to tolerate different key casings/legacy data ----
def _g(info, *keys, default=None):
    """Get first present key from keys, case-insensitive, falling back to default."""
    if info is None:
        return default
    # exact first
    for k in keys:
        if k in info:
            return info[k]
    # case-insensitive
    lower_map = {str(k).lower(): v for k, v in info.items()}
    for k in keys:
        v = lower_map.get(str(k).lower(), None)
        if v is not None:
            return v
    return default


def draw_info_as_json(frame, info, start_x=10, start_y=30, line_height=20):
    json_str = json.dumps(info, indent=2)
    lines = json_str.split('\\n')
    for i, line in enumerate(lines):
        y = start_y + i * line_height
        cv2.putText(frame, line, (start_x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)


# ---- Sorting helpers ----

def is_basic_land(name):
    s = (name or "").strip().lower()
    return s in {"plains", "island", "swamp", "mountain", "forest", "wastes"}


def is_land_card(types):
    try:
        return "land" in [t.lower() for t in (types or [])]
    except Exception:
        return False


def get_bin_color(info):
    types = _g(info, "Types", "types", default=[]) or []
    if is_land_card(types):
        return "Basic land" if is_basic_land(_g(info, "Name", "name", default="")) else "Nonbasic land"
    colors = _g(info, "Colors", "colors", default=[]) or []
    if not colors:
        return "Colorless"
    return "Multicolor" if len(colors) > 1 else (colors[0] or "Colorless")


def get_bin_mana(info):
    mv = _g(info, "CMC", "cmc", default=0) or 0
    try:
        mv = int(round(float(mv)))
    except Exception:
        mv = 0
    if mv <= 1:
        return "One"
    elif mv <= 8:
        return str(mv).capitalize()
    else:
        return "RejectCard"


def get_bin_set(info):
    set_code = str(_g(info, "Set", "set", default="???") or "???").lower()
    types = _g(info, "Types", "types", default=[]) or []
    return "token" if any((str(t).lower() == "token") for t in types) else (set_code or "RejectCard")


def _parse_price_to_float(price_value):
    """Accepts DB-style numeric/str or legacy '$x.xx' -> float|None."""
    if price_value is None:
        return None
    try:
        return float(price_value)
    except Exception:
        s = str(price_value).strip()
        if s.lower() == "null" or s == "":
            return None
        if s.startswith("$"):
            s = s[1:]
        try:
            return float(s)
        except Exception:
            return None


def get_bin_price(info, threshold):
    price_value = _g(info, "Price", "price", default=None)
    price = _parse_price_to_float(price_value)
    if price is None:
        return "RejectCard"

    bins = {
        0.02: "tray1",
        0.05: "tray7",
        0.10: "tray14",
        0.25: "tray18",
        0.50: "tray21",
        1.00: "tray24",
        2.00: "tray25",
        4.00: "tray26",
        8.00: "tray27",
        16.00: "tray28",
        32.00: "tray29",
        64.00: "tray30",
        128.00: "tray31",
        float('inf'): "tray32"
    }
    try:
        thr = float(threshold)
    except Exception:
        thr = float('inf')

    for upper_limit, bin_name in bins.items():
        if price <= upper_limit and price <= thr:
            return bin_name
    return "RejectCard"


def get_bin_type(info):
    types = _g(info, "Types", "types", default=[]) or []
    type_mapping = {
        "creature": "creature", "artifact": "artifact", "enchantment": "enchantment",
        "instant": "instant", "sorcery": "sorcery", "battle": "battle",
        "planeswalker": "planeswalker", "land": "land", "token": "token"
    }
    for card_type in types:
        t = str(card_type).lower()
        if t in type_mapping:
            return type_mapping[t]
    return "RejectCard"


def get_bin_number(info, mode, threshold):
    if not info:
        return "RejectCard"
    if info == "RejectCard":
        return "Rejectcard"
    mode = (mode or "").lower()
    if mode == "color":
        return get_bin_color(info)
    elif mode == "mana_value":
        return get_bin_mana(info)
    elif mode == "set":
        return get_bin_set(info)
    elif mode == "price":
        return get_bin_price(info, 1000000)
    elif mode == "type":
        return get_bin_type(info)
    elif mode == "buy":
        return get_bin_price(info, threshold)
    else:
        return "RejectCard"


def get_mana_cost(info):
    cost = str(_g(info, "Mana Cost", "mana_cost", default="???") or "???").upper()
    return cost.replace("{", "").replace("}", "")


def get_promo(info):
    return _g(info, "Promo", "promo", default=None)


def get_name(info):
    return _g(info, "Name", "name", default="???")


# ---- CLI helpers ----

def print_sorting_options():
    """Print numbered sorting options for CLI usage (main_sqlite.py)."""
    print("\nSelect a sorting method:")
    print("  1) Color")
    print("  2) Mana Value")
    print("  3) Price")
    print("  4) Set")
    print("  5) Type")
    print("  6) Buy (by price)")
