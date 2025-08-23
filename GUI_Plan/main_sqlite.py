# main_sqlite.py — Interactive DB & sort selection + persistent controls + loading spinner
# Keys: [ / ] threshold, d ROI window, h hash neighbor logs, c toggle fallback ROI, e edges window, q/Esc quit

import os
import sys
import time
import threading
import logging
from typing import Optional, Tuple, List

import cv2
import numpy as np
from PIL import Image

from config import (
    WIDTH, HEIGHT,
    SORTING_MODES,
    DEFAULT_HASH_SIZE, MAX_DISTANCE_THRESHOLD,
)

from detection import find_card_contour, get_perspective_corrected_card
from sorting import print_sorting_options, draw_info_as_json, get_bin_number
from cards import extract_card_info, card_is_allowed
from detectname import find_text as detect_name, compare_strings

from hashing import (
    hash_image_color as _hash_image_color_core,
    ensure_loaded,
    top_k_for_image,
    get_status,
)

# ---------------------------
# Globals / toggles
# ---------------------------
SELECTED_DB_NAME: Optional[str] = None
current_sorting_mode: str = "color"
distance_threshold: float = float(MAX_DISTANCE_THRESHOLD)
show_debug_roi = False        # 'd' to toggle ROI + top-3
show_hash_debug = False       # 'h' to toggle hash neighbor logging (WARNING-level)
show_edges = False            # 'e' to toggle an edges window
allow_fallback_roi = True     # 'c' to toggle center-crop fallback when no contour found
status_banner_secs = 3.0      # show "hashes loaded" banner on screen for N seconds

# ---------------------------
# Hash wrapper (neighbors only when show_hash_debug)
# ---------------------------
def _current_db():
    return SELECTED_DB_NAME

def hash_image_color(img: Image.Image, hash_size: int = DEFAULT_HASH_SIZE) -> Tuple[Optional[str], float]:
    try:
        ensure_loaded(_current_db())
    except Exception:
        pass

    best_id, best_dist = _hash_image_color_core(img, hash_size)

    if show_hash_debug:
        try:
            logger = logging.getLogger(__name__)
            if best_id is None:
                logger.warning("hash debug: no best_id from hash_image_color")
            if best_dist is None or float(best_dist) > distance_threshold:
                try:
                    neighbors = top_k_for_image(img, k=3, hash_size=hash_size)
                    logger.warning(f"hash debug: best={best_id} dist={best_dist}; top3={neighbors}")
                except Exception as e:
                    logger.warning(f"hash debug: neighbor calc failed: {e}")
        except Exception:
            pass

    return best_id, best_dist

# ---------------------------
# Spinner (console) while hashes load
# ---------------------------
def _spinner(message: str, stop_event: threading.Event, period: float = 0.1):
    glyphs = ["|", "/", "-", "\\"]
    i = 0
    try:
        while not stop_event.is_set():
            sys.stdout.write("\r" + message + " " + glyphs[i % len(glyphs)])
            sys.stdout.flush()
            time.sleep(period)
            i += 1
    finally:
        # clear the line
        sys.stdout.write("\r" + " " * (len(message) + 2) + "\r")
        sys.stdout.flush()

# ---------------------------
# Helpers
# ---------------------------
def _list_db_files() -> list:
    try:
        from database import list_db_files
        dbs = list_db_files()
        dbs = [d for d in dbs if d.lower().endswith('.db')]
        return dbs
    except Exception:
        data_dir = os.path.join(os.path.dirname(__file__), "data")
        if not os.path.isdir(data_dir):
            return []
        return [f for f in os.listdir(data_dir) if f.lower().endswith(".db")]

def choose_db_interactive() -> Optional[str]:
    dbs = _list_db_files()
    if not dbs:
        logging.getLogger(__name__).warning("No .db files found under ./data")
        return None

    print("\nAvailable databases:")
    for i, name in enumerate(dbs, 1):
        print(f"  {i}) {name}")
    choice = input("Select DB number (Enter for 1): ").strip()
    try:
        idx = 0 if choice == "" else max(0, min(len(dbs) - 1, int(choice) - 1))
    except Exception:
        idx = 0
    return dbs[idx]

def _open_camera(index: int = 0):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if hasattr(cv2, "CAP_DSHOW") else cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {index}")
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 30)
    except Exception:
        pass
    return cap

def _compute_edges(frame_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    return edges

def _center_crop_roi(frame_bgr: np.ndarray, scale: float = 0.7) -> Image.Image:
    """Crop the center of the frame (scale of width/height), resize to WIDTHxHEIGHT, return PIL Image."""
    h, w = frame_bgr.shape[:2]
    cw, ch = int(w * scale), int(h * scale)
    x1 = (w - cw) // 2
    y1 = (h - ch) // 2
    crop = frame_bgr[y1:y1+ch, x1:x1+cw]
    resized = cv2.resize(crop, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)

def _extract_card_roi(frame_bgr) -> Optional[Tuple[Image.Image, str]]:
    """
    Try to find card via contour. If not found and allow_fallback_roi is True,
    use center-crop fallback. Returns (PIL Image, mode) where mode is "contour" or "fallback".
    """
    contour = find_card_contour(frame_bgr)
    if contour is not None:
        try:
            card_bgr = get_perspective_corrected_card(frame_bgr, contour, target_w=WIDTH, target_h=HEIGHT)
            card_rgb = cv2.cvtColor(card_bgr, cv2.COLOR_BGR2RGB)
            return Image.fromarray(card_rgb), "contour"
        except Exception:
            pass

    if allow_fallback_roi:
        return _center_crop_roi(frame_bgr), "fallback"

    return None

def _overlay_text(frame, text, x, y, color=(255, 255, 255)):
    try:
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    except Exception:
        pass

def _draw_controls_panel(frame, lines: List[str], margin: int = 10):
    """Draw a semi-transparent control/help panel in the top-right corner."""
    try:
        h, w = frame.shape[:2]
        sizes = [cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0] for line in lines]
        text_w = max(sz[0] for sz in sizes) if sizes else 0
        text_h = sum(sz[1] + 8 for sz in sizes) if sizes else 0
        panel_w = text_w + 2 * margin
        panel_h = text_h + 2 * margin

        x1 = max(0, w - panel_w - margin)
        y1 = margin
        x2 = min(w - 1, w - margin)
        y2 = min(h - 1, y1 + panel_h)

        overlay = frame.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 0, 0), thickness=-1)
        cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)

        cur_y = y1 + margin + (sizes[0][1] if sizes else 16)
        for i, line in enumerate(lines):
            cv2.putText(frame, line, (x1 + margin, cur_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            lh = sizes[i][1] if i < len(sizes) else 16
            cur_y += lh + 8
    except Exception:
        pass

# ---------------------------
# Main
# ---------------------------
def main():
    global SELECTED_DB_NAME, current_sorting_mode
    global distance_threshold, show_debug_roi, show_hash_debug, show_edges, allow_fallback_roi

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
    print("Starting sorter in SQLite mode...")

    # --- DB selection ---
    SELECTED_DB_NAME = choose_db_interactive()
    print(f"Selected DB: {SELECTED_DB_NAME!r}")

    # --- Load hashes with spinner ---
    stop_event = threading.Event()
    spinner_thread = threading.Thread(target=_spinner, args=(f"Loading hashes from {SELECTED_DB_NAME}...", stop_event), daemon=True)
    spinner_thread.start()
    t0 = time.time()
    try:
        ensure_loaded(SELECTED_DB_NAME)
    finally:
        stop_event.set()
        spinner_thread.join()
    elapsed = time.time() - t0

    # --- Report status (print once + on-screen banner) ---
    status = get_status()
    print(f"Loaded {status.get('count', 0)} hashes from {len(status.get('tables', []))} tables in {elapsed:.1f}s (DB={status.get('db_name')})")
    banner_until = time.time() + status_banner_secs

    # --- Sorting selection ---
    print_sorting_options()
    choice = input("Select sorting option number (Enter for 1): ").strip() or "1"
    current_sorting_mode = SORTING_MODES.get(choice, "color")
    print(f"Sorting mode set to: {current_sorting_mode}")

    # --- Camera ---
    cam = _open_camera(0)
    print("Camera opened. Hotkeys: [ ] thr, d ROI, h hash, c fallback, e edges, q quit.")
    try:
        cv2.namedWindow("Sorter", cv2.WINDOW_NORMAL)
    except Exception:
        pass

    frame_count = 0
    try:
        while True:
            ret, frame = cam.read()
            if not ret:
                logging.getLogger(__name__).warning("Failed to read frame from camera.")
                continue

            # Optional edges window
            if show_edges:
                try:
                    edges = _compute_edges(frame)
                    cv2.imshow("Edges", edges)
                except Exception:
                    pass
            else:
                try:
                    cv2.destroyWindow("Edges")
                except Exception:
                    pass

            # ROI
            roi = _extract_card_roi(frame)
            if roi is None:
                _overlay_text(frame, "No card contour detected (fallback off)", 10, 30, (0, 200, 255))
            else:
                pil_img, roi_mode = roi
                overlay_y = 30
                if roi_mode == "fallback":
                    _overlay_text(frame, "Using fallback center crop", 10, overlay_y, (255, 180, 60))
                    overlay_y += 22

                # Hash match
                best_id, best_dist = hash_image_color(pil_img, hash_size=DEFAULT_HASH_SIZE)
                _overlay_text(frame, f"Best: {best_id}  dist: {best_dist:.1f}  thr: {distance_threshold:.0f}", 10, overlay_y, (200, 200, 200))

                # Optional ROI & top-3 neighbor window
                if show_debug_roi:
                    try:
                        roi_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
                        cv2.imshow("ROI", roi_bgr)
                    except Exception:
                        pass
                    try:
                        nbs = top_k_for_image(pil_img, k=3, hash_size=DEFAULT_HASH_SIZE)
                        y0 = overlay_y + 30
                        for (tbl_id, dist) in nbs:
                            tbl, rid = tbl_id
                            info = extract_card_info((tbl, rid), db_name=SELECTED_DB_NAME)
                            nm = (info or {}).get("Name", "?")
                            _overlay_text(frame, f"  {tbl}:{rid}  {dist:.1f}  {nm}", 10, y0, (160, 160, 255))
                            y0 += 22
                    except Exception:
                        pass

                # Decision
                if best_id is not None and best_dist <= distance_threshold:
                    info = extract_card_info(best_id, db_name=SELECTED_DB_NAME)
                    if info and card_is_allowed(best_id, db_name=SELECTED_DB_NAME):
                        try:
                            draw_info_as_json(frame, info, start_x=10, start_y=120, line_height=18)
                        except Exception:
                            pass
                        bin_name = get_bin_number(info, current_sorting_mode, 1000000)
                        _overlay_text(frame, f"BIN: {bin_name}", 10, 90, (0, 255, 0))
                    else:
                        _overlay_text(frame, "Not allowed / excluded set", 10, 90, (0, 200, 200))
                else:
                    _overlay_text(frame, "Matching...", 10, 90, (200, 200, 200))

            # One-time banner (bottom-left)
            if time.time() < banner_until:
                _overlay_text(frame, f"Loaded {status.get('count',0)} hashes from {len(status.get('tables',[]))} tables ({status.get('db_name','?')})", 10, HEIGHT - 20, (100, 255, 100))

            # Controls panel (top-right)
            panel_lines = [
                f"DB: {SELECTED_DB_NAME or '?'}",
                f"Mode: {current_sorting_mode}",
                f"Thr: {distance_threshold:.0f}",
                f"ROI debug: {'ON' if show_debug_roi else 'OFF'}",
                f"Edges: {'ON' if show_edges else 'OFF'}",
                f"Fallback ROI: {'ON' if allow_fallback_roi else 'OFF'}",
                "Hotkeys: [ ] thr, d ROI, h hash, c fallback, e edges, q quit",
            ]
            _draw_controls_panel(frame, panel_lines)

            # Show
            try:
                cv2.imshow("Sorter", frame)
            except Exception:
                pass

            # Hotkeys
            k = cv2.waitKeyEx(1)
            if k != -1:
                kc = k & 0xFF
                # FIX: avoid 'in (27)' mistake; use equality
                if kc == 27 or k == 27:  # Esc
                    break
                elif kc in (ord('q'), ord('Q')):
                    break
                elif kc == ord('[') or k in (219, 0xDB):
                    distance_threshold = max(0, distance_threshold - 5)
                    print(f"Threshold -> {distance_threshold}")
                elif kc == ord(']') or k in (221, 0xDD):
                    distance_threshold = distance_threshold + 5
                    print(f"Threshold -> {distance_threshold}")
                elif kc in (ord('d'), ord('D')):
                    show_debug_roi = not show_debug_roi
                    print(f"Debug ROI -> {show_debug_roi}")
                    try:
                        if show_debug_roi:
                            cv2.namedWindow('ROI', cv2.WINDOW_NORMAL)
                        else:
                            cv2.destroyWindow('ROI')
                    except Exception:
                        pass
                elif kc in (ord('h'), ord('H')):
                    show_hash_debug = not show_hash_debug
                    print(f"Hash debug logging -> {show_hash_debug} (WARNING-level)")
                elif kc in (ord('e'), ord('E')):
                    show_edges = not show_edges
                    print(f"Edges window -> {show_edges}")
                    try:
                        if show_edges:
                            cv2.namedWindow('Edges', cv2.WINDOW_NORMAL)
                        else:
                            cv2.destroyWindow('Edges')
                    except Exception:
                        pass
                elif kc in (ord('c'), ord('C')):
                    allow_fallback_roi = not allow_fallback_roi
                    print(f"Fallback ROI -> {allow_fallback_roi}")

            frame_count += 1

    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        try:
            cam.release()
        except Exception:
            pass
        for win in ("Sorter", "ROI", "Edges"):
            try:
                cv2.destroyWindow(win)
            except Exception:
                pass
        print("Exiting.")

if __name__ == "__main__":
    main()
