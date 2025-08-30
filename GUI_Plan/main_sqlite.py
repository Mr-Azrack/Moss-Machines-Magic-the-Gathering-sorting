# main_sqlite.py
import cv2
import logging
import time
from typing import Any, Dict, Optional, Tuple, List
from collections import Counter
from PIL import Image

from config import (
    CROP_SIZE, SORTING_MODES, EXCLUDED_SETS,
    MAX_ATTEMPTS_NAME, TIMEOUT_NAME
)
from detection import find_card_contour, get_perspective_corrected_card
from detectname import compare_strings
from hashing import hash_image_color, compute_distances_for_image, ensure_loaded_for_db
from sorting import draw_info_as_json, get_bin_number, get_name
from database import get_card_info

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("ultralytics").setLevel(logging.WARNING)

# The .db filename (e.g., "mtg.db") chosen from UI
SELECTED_DB_NAME: Optional[str] = None


# ----------------- Draw-only helpers (NO cv2.imshow in GUI path) -----------------

def handle_unrecognized_card(display_frame, card_approx, reason="Unknown"):
    """Overlay error info on the frame; do not open any OpenCV windows."""
    try:
        if card_approx is not None:
            cv2.drawContours(display_frame, [card_approx], -1, (0, 0, 255), 2)
        cv2.putText(display_frame, "Unrecognized Card", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        cv2.putText(display_frame, f"Reason: {reason}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        cv2.putText(display_frame, "Bin: 33", (10, 200),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    except Exception as e:
        logger.debug(f"Overlay failed in handle_unrecognized_card: {e}")


def handle_recognized_card(display_frame, chosen_info, current_sorting_mode, threshold, name2):
    """Overlay recognized info on the frame; do not open any OpenCV windows."""
    try:
        draw_info_as_json(display_frame, chosen_info, start_x=10, start_y=30, line_height=20)
        name = get_name(chosen_info)
        _similarity = compare_strings(name, name2)
        bin_code = get_bin_number(chosen_info, current_sorting_mode, threshold)
        cv2.putText(display_frame, f"Bin: {bin_code}", (10, 200),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    except Exception as e:
        logger.debug(f"Overlay failed in handle_recognized_card: {e}")


# ----------------- Internals -----------------

def _append_log(shared_data: Dict[str, Any], msg: str):
    try:
        from datetime import datetime
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        shared_data.setdefault("system_log", []).append(entry)
        if len(shared_data["system_log"]) > 1000:
            shared_data["system_log"] = shared_data["system_log"][-1000:]
    except Exception:
        pass


def _ensure_hash_index(shared_data: Dict[str, Any]):
    """Make sure the color-hash index is loaded for the currently selected DB."""
    if not SELECTED_DB_NAME:
        return
    _append_log(shared_data, f"Indexing hashes from {SELECTED_DB_NAME} …")
    count = ensure_loaded_for_db(SELECTED_DB_NAME, on_status=lambda s: _append_log(shared_data, s))
    _append_log(shared_data, f"Hash index ready ({count} cards).")


def process_card_approx(frame, card_approx, current_sorting_mode, threshold) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Perspective-correct → crop/orient → hash-lookup (no UI)."""
    warped = get_perspective_corrected_card(frame, card_approx)

    # Upright crop
    cropped_upright = warped[:CROP_SIZE, :CROP_SIZE]
    img_pil_upright = Image.fromarray(cv2.cvtColor(cropped_upright, cv2.COLOR_BGR2RGB))

    # 180° crop for robustness
    rotated180 = cv2.rotate(warped, cv2.ROTATE_180)
    cropped_rotated = rotated180[:CROP_SIZE, :CROP_SIZE]
    img_pil_rotated = Image.fromarray(cv2.cvtColor(cropped_rotated, cv2.COLOR_BGR2RGB))

    # Quick best-match probes (kept for sanity/logging)
    _upright_id, _upright_dist = hash_image_color(img_pil_upright, hash_size=16)
    _rotated_id, _rotated_dist = hash_image_color(img_pil_rotated, hash_size=16)

    # Ranked candidates (upright)
    all_distances = compute_distances_for_image(img_pil_upright, hash_size=16, top_k=20)

    # Filter by allowed sets using the selected DB
    allowed: List[Tuple[Any, int]] = []
    for cid, dist in all_distances:
        card = get_card_info(cid, db_name=SELECTED_DB_NAME)
        if card and (card.get("set") or "").lower() not in EXCLUDED_SETS:
            allowed.append((cid, dist))

    allowed.sort(key=lambda x: x[1])

    if allowed:
        chosen_id = allowed[0][0]
        chosen_info = get_card_info(chosen_id, db_name=SELECTED_DB_NAME)
        return chosen_id, chosen_info
    return None, None


# ----------------- GUI entry point -----------------

def process_card_with_db(
    display_frame,
    frame,
    card_approx,
    ocr_name: str,
    shared_data: Dict[str, Any],
    serial_connected: bool,
    send_to_arduino_func
):
    """
    GUI-friendly processing entry (called by MainTab).
    Never raises into Tk; errors are logged & mirrored into shared_data.
    """
    try:
        if not SELECTED_DB_NAME:
            _append_log(shared_data, "No DB selected; cannot process frame.")
            return

        _ensure_hash_index(shared_data)

        current_sorting_mode = shared_data.get("current_sort_method", "color")
        try:
            threshold = float(shared_data.get("current_threshold", 1000000))
        except Exception:
            threshold = 1000000.0

        chosen_id, chosen_info = process_card_approx(frame, card_approx, current_sorting_mode, threshold)

        if not chosen_info:
            handle_unrecognized_card(display_frame, card_approx, reason="No allowed matches / not in DB")
            shared_data["current_card_data"] = {
                "status": "unrecognized",
                "reason": "No allowed matches / not in DB",
                "bin": 33
            }
            return

        # Overlay + bin
        handle_recognized_card(display_frame, chosen_info, current_sorting_mode, threshold, ocr_name)

        # Pack data for the right-side panel
        name_db = get_name(chosen_info)
        sim = compare_strings(name_db, ocr_name)
        bin_code = get_bin_number(chosen_info, current_sorting_mode, threshold)

        shared_data["current_card_data"] = {
            "status": "recognized",
            "id": chosen_info.get("id"),
            "name_ocr": ocr_name,
            "name_db": name_db,
            "similarity": float(sim),
            "set": chosen_info.get("Set") or chosen_info.get("set"),
            "price": chosen_info.get("Price") or chosen_info.get("price"),
            "bin": bin_code,
        }

        # Optional Arduino send if match is good
        if serial_connected and sim >= 0.6 and callable(send_to_arduino_func):
            try:
                send_to_arduino_func(bin_code)
            except Exception as e:
                _append_log(shared_data, f"Arduino send failed: {e}")

    except Exception as e:
        logger.exception("process_card_with_db error")
        shared_data["current_card_data"] = {
            "status": "unrecognized",
            "reason": f"Processing error: {e}",
            "bin": 33
        }
