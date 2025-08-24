# main_sqlite.py — Responsive UI + background matcher + debug tools
# Keys: [ / ] thr, d ROI, h hash, c fallback, e edges, x art-crop, ,/. match-rate, p pause, m manual, s save ROI, t top-10, i inspect DB, q/Esc quit

import os
import sys
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Any
from queue import Queue, Empty

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
    set_center_crop_ratio,
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
art_crop_levels = [1.0, 0.9, 0.8, 0.7, 0.6]
art_crop_idx = 0  # start with 1.0 (no crop)

# Match cadence (ms)
MATCH_INTERVAL_MS = 300
paused_matching = False

# ---------------------------
# Background matching
# ---------------------------
@dataclass
class MatchJob:
    pil_img: Image.Image
    want_neighbors: bool = False

@dataclass
class MatchResult:
    best_id: Optional[Any] = None
    best_dist: float = 1e9
    neighbors: List[Tuple[Any, float]] = field(default_factory=list)
    timestamp: float = 0.0
    busy: bool = False

match_queue: Queue = Queue(maxsize=1)
match_result = MatchResult()

def _match_worker():
    global match_result
    while True:
        job: MatchJob = match_queue.get()
        if job is None:
            break  # shutdown
        try:
            match_result.busy = True
            best_id, best_dist = _hash_image_color_core(job.pil_img, None)
            nbs = []
            if job.want_neighbors:
                try:
                    nbs = top_k_for_image(job.pil_img, k=3, hash_size=None)
                except Exception:
                    nbs = []
            match_result = MatchResult(
                best_id=best_id,
                best_dist=float(best_dist if best_dist is not None else 1e9),
                neighbors=nbs,
                timestamp=time.time(),
                busy=False,
            )
        except Exception:
            match_result = MatchResult(best_id=None, best_dist=1e9, neighbors=[], timestamp=time.time(), busy=False)

worker_thread = None

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
        print("No .db files found under ./data")
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
    h, w = frame_bgr.shape[:2]
    cw, ch = int(w * scale), int(h * scale)
    x1 = (w - cw) // 2
    y1 = (h - ch) // 2
    crop = frame_bgr[y1:y1+ch, x1:x1+cw]
    resized = cv2.resize(crop, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)

def _extract_card_roi(frame_bgr) -> Optional[Tuple[Image.Image, str]]:
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
    global SELECTED_DB_NAME, current_sorting_mode, distance_threshold
    global show_debug_roi, show_hash_debug, show_edges, allow_fallback_roi
    global art_crop_idx, MATCH_INTERVAL_MS, paused_matching, worker_thread

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

    # --- Report status ---
    status = get_status()
    print(f"Loaded {status.get('count', 0)} hashes from {len(status.get('tables', []))} tables in {elapsed:.1f}s (DB={status.get('db_name')}) | mode={status.get('mode')} size={status.get('hash_size')}")
    banner_until = time.time() + status_banner_secs

    # --- Sorting selection ---
    print_sorting_options()
    choice = input("Select sorting option number (Enter for 1): ").strip() or "1"
    current_sorting_mode = SORTING_MODES.get(choice, "color")
    print(f"Sorting mode set to: {current_sorting_mode}")

    # --- Camera ---
    cam = _open_camera(0)
    print("Camera opened. Hotkeys: [ ] thr, d ROI, h hash, c fallback, e edges, x art-crop, ,/. rate, p pause, m manual, s save ROI, t top-10, i inspect DB, q quit.")
    try:
        cv2.namedWindow("Sorter", cv2.WINDOW_NORMAL)
    except Exception:
        pass

    # Apply initial art-crop ratio
    set_center_crop_ratio(art_crop_levels[art_crop_idx])

    # Start worker
    worker_thread = threading.Thread(target=_match_worker, daemon=True)
    worker_thread.start()
    last_submit_ts = 0.0

    frame_count = 0
    last_roi = None
    try:
        while True:
            ret, frame = cam.read()
            if not ret:
                print("Failed to read frame from camera.")
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
                pil_img = None
                roi_mode = None
            else:
                pil_img, roi_mode = roi
                last_roi = pil_img
                overlay_y = 30
                if roi_mode == "fallback":
                    _overlay_text(frame, "Using fallback center crop", 10, overlay_y, (255, 180, 60))
                    overlay_y += 22

            # Submit matching job at cadence
            now = time.time()
            want_neighbors = show_debug_roi
            if pil_img is not None and not paused_matching and (now - last_submit_ts) * 1000.0 >= MATCH_INTERVAL_MS:
                try:
                    if match_queue.full():
                        try:
                            match_queue.get_nowait()
                        except Empty:
                            pass
                    match_queue.put_nowait(MatchJob(pil_img=pil_img, want_neighbors=want_neighbors))
                    last_submit_ts = now
                    match_result.busy = True
                except Exception:
                    pass

            # Read current result & overlay
            br = match_result
            if pil_img is not None:
                _overlay_text(frame, f"Best: {br.best_id}  dist: {br.best_dist:.1f}  thr: {distance_threshold:.0f}", 10, 30 if roi_mode != 'fallback' else 52, (200, 200, 200))
                if show_debug_roi and br.neighbors:
                    y0 = (52 if roi_mode == 'fallback' else 30) + 30
                    for (tbl_id, dist) in br.neighbors:
                        try:
                            tbl, rid = tbl_id
                            nm = (extract_card_info((tbl, rid), db_name=SELECTED_DB_NAME) or {}).get("Name", "?")
                            _overlay_text(frame, f"  {tbl}:{rid}  {dist:.1f}  {nm}", 10, y0, (160, 160, 255))
                            y0 += 22
                        except Exception:
                            pass

                # Decision
                if br.best_id is not None and br.best_dist <= distance_threshold:
                    info = extract_card_info(br.best_id, db_name=SELECTED_DB_NAME)
                    if info and card_is_allowed(br.best_id, db_name=SELECTED_DB_NAME):
                        try:
                            draw_info_as_json(frame, info, start_x=10, start_y=120, line_height=18)
                        except Exception:
                            pass
                        bin_name = get_bin_number(info, current_sorting_mode, 1000000)
                        _overlay_text(frame, f"BIN: {bin_name}", 10, 90, (0, 255, 0))
                    else:
                        _overlay_text(frame, "Not allowed / excluded set", 10, 90, (0, 200, 200))
                else:
                    _overlay_text(frame, "Matching..." + (" (busy)" if br.busy else ""), 10, 90, (200, 200, 200))

            # One-time banner (bottom-left)
            if time.time() < banner_until:
                _overlay_text(frame, f"Loaded {status.get('count',0)} hashes from {len(status.get('tables',[]))} tables ({status.get('db_name','?')})", 10, HEIGHT - 20, (100, 255, 100))

            # Controls panel (top-right)
            status = get_status()
            panel_lines = [
                f"DB: {SELECTED_DB_NAME or '?'}",
                f"Mode: {current_sorting_mode}",
                f"Thr: {distance_threshold:.0f}",
                f"ROI debug: {'ON' if show_debug_roi else 'OFF'}",
                f"Edges: {'ON' if show_edges else 'OFF'}",
                f"Fallback ROI: {'ON' if allow_fallback_roi else 'OFF'}",
                f"Art crop: {art_crop_levels[art_crop_idx]:.1f} (x to cycle)",
                f"DB hash mode: {status.get('mode')} size: {status.get('hash_size')}",
                f"Match every: {MATCH_INTERVAL_MS} ms  ({'PAUSED' if paused_matching else 'RUN'})",
                "Hotkeys: [ ] thr, d ROI, h hash, c fallback, e edges, x crop, ,/. rate, p pause, m manual, s save, t top-10, i inspect DB, q quit",
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
                elif kc in (ord('x'), ord('X')):
                    art_crop_idx = (art_crop_idx + 1) % len(art_crop_levels)
                    set_center_crop_ratio(art_crop_levels[art_crop_idx])
                    print(f"Art-crop ratio -> {art_crop_levels[art_crop_idx]:.1f}")
                elif kc in (ord(','),):  # slower
                    MATCH_INTERVAL_MS = min(2000, MATCH_INTERVAL_MS + 50)
                    print(f"Match interval -> {MATCH_INTERVAL_MS} ms")
                elif kc in (ord('.'),):  # faster
                    MATCH_INTERVAL_MS = max(50, MATCH_INTERVAL_MS - 50)
                    print(f"Match interval -> {MATCH_INTERVAL_MS} ms")
                elif kc in (ord('p'), ord('P')):
                    paused_matching = not paused_matching
                    print(f"Matching paused -> {paused_matching}")
                elif kc in (ord('m'), ord('M')):
                    if last_roi is not None:
                        try:
                            if match_queue.full():
                                try:
                                    match_queue.get_nowait()
                                except Empty:
                                    pass
                            match_queue.put_nowait(MatchJob(pil_img=last_roi, want_neighbors=True))
                            match_result.busy = True
                            print("Manual match queued.")
                        except Exception:
                            pass
                elif kc in (ord('s'), ord('S')):
                    if last_roi is not None:
                        os.makedirs("captures", exist_ok=True)
                        fname = time.strftime("captures/roi_%Y%m%d_%H%M%S.png")
                        last_roi.save(fname)
                        print(f"Saved ROI -> {fname}")
                elif kc in (ord('t'), ord('T')):
                    if last_roi is not None:
                        # Synchronous top-10 dump for debugging
                        try:
                            neighbors = top_k_for_image(last_roi, k=10, hash_size=None)
                            print("Top-10 neighbors:")
                            for tbl_id, dist in neighbors:
                                try:
                                    tbl, rid = tbl_id
                                except Exception:
                                    tbl, rid = str(tbl_id), "?"
                                print(f"  {tbl}:{rid}  dist={dist:.1f}")
                        except Exception as e:
                            print(f"Top-10 failed: {e}")
                elif kc in (ord('i'), ord('I')):
                    st = get_status()
                    print("DB status:", st)

            frame_count += 1

    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        try:
            match_queue.put_nowait(None)
        except Exception:
            pass
        try:
            if worker_thread is not None:
                worker_thread.join(timeout=0.2)
        except Exception:
            pass
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
