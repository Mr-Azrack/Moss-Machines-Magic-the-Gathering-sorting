# main_tab.py
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import cv2
from PIL import Image, ImageTk
import time
import json
import os
import serial
import logging
from datetime import datetime
from collections import Counter

from config import (
    SORTING_MODES,
    CROP_SIZE,
    EXCLUDED_SETS,
    SERIAL_PORT,
    BAUD_RATE,
    START_MARKER,
    END_MARKER,
    MAX_ATTEMPTS_NAME,
    TIMEOUT_NAME,
)

from detection import find_card_contour
from detectname import find_text
from hashing import ensure_loaded_for_db
from sorting import get_name

import main_sqlite
from database import list_db_files

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("ultralytics").setLevel(logging.WARNING)


class BusyPopup:
    """Tiny non-blocking notifier shown while we build the hash index."""
    def __init__(self, parent, text="Building hash index…"):
        self.parent = parent
        self.top = tk.Toplevel(parent)
        self.top.title("Please wait")
        self.top.resizable(False, False)
        self.top.transient(parent)
        self.top.attributes("-topmost", True)
        frm = ttk.Frame(self.top, padding=10)
        frm.grid(sticky="nsew")
        ttk.Label(frm, text=text).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.pb = ttk.Progressbar(frm, mode="indeterminate", length=200)
        self.pb.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.pb.start(12)
        self.top.update_idletasks()
        try:
            px = self.parent.winfo_rootx()
            py = self.parent.winfo_rooty()
            pw = self.parent.winfo_width()
            ph = self.parent.winfo_height()
            tw = self.top.winfo_width()
            th = self.top.winfo_height()
            x = px + (pw - tw) // 2
            y = py + (ph - th) // 3
            self.top.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def close(self):
        try: self.pb.stop()
        except Exception: pass
        try: self.top.destroy()
        except Exception: pass


class MainTab:
    def __init__(self, parent, shared_data):
        self.parent = parent
        self.shared_data = shared_data
        self.camera = None
        self.video_label = None

        # Serial
        self.ser = None
        self.serial_connected = False

        # State
        self.frame_count = 0
        self.total_processing_time = 0
        self.hashing_in_progress = False

        # Layout
        self.main_container = None
        self.left_panel = None
        self.right_panel = None
        self.camera_frame = None

        # Camera display size
        self.camera_display_size = (640, 480)

        # Fonts
        self.base_font_size = 10
        self.small_font_size = 9

        # DB selection
        self.db_var = tk.StringVar(value="")
        self.sort_var = tk.StringVar(value="color")
        self.threshold_var = tk.StringVar(value="5.00")

        self.setup_ui()
        self.refresh_db_list()

    # ---------------- Serial ----------------
    def init_serial(self):
        try:
            self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE)
            self.serial_connected = True
            self.add_log(f"Serial port {SERIAL_PORT} opened. Baudrate {BAUD_RATE}.")
            self.wait_for_arduino()
            return True
        except Exception as e:
            self.add_log(f"Failed to initialize serial: {e}")
            self.serial_connected = False
            return False

    def wait_for_arduino(self):
        try:
            ck = ""
            x = b"z"
            while ord(x) != START_MARKER:
                x = self.ser.read()
            while ord(x) != END_MARKER:
                if ord(x) != START_MARKER:
                    ck += x.decode("utf-8")
                x = self.ser.read()
        except Exception as e:
            self.add_log(f"Error waiting for Arduino: {e}")

    def send_to_arduino(self, send_str):
        if self.serial_connected and self.ser and send_str:
            try:
                self.ser.write(send_str.encode("utf-8"))
                self.wait_for_arduino()
                self.add_log(f"Sent to Arduino: {send_str}")
            except Exception as e:
                self.add_log(f"Error sending to Arduino: {e}")
                self.serial_connected = False

    # ---------------- UI ----------------
    def get_scaled_font(self, base_size=None):
        return ("TkDefaultFont", base_size or self.base_font_size)

    def get_small_font(self):
        return ("TkDefaultFont", self.small_font_size)

    def setup_ui(self):
        self.parent.rowconfigure(0, weight=1)
        self.parent.columnconfigure(0, weight=1)

        self.main_container = ttk.Frame(self.parent)
        self.main_container.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.main_container.rowconfigure(0, weight=1)
        self.main_container.columnconfigure(0, weight=0)
        self.main_container.columnconfigure(1, weight=1)

        self.left_panel = ttk.Frame(self.main_container, width=300)
        self.left_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        self.left_panel.grid_propagate(False)

        self.right_panel = ttk.Frame(self.main_container)
        self.right_panel.grid(row=0, column=1, sticky="nsew")
        self.right_panel.rowconfigure(0, weight=0)
        self.right_panel.rowconfigure(1, weight=2)
        self.right_panel.rowconfigure(2, weight=1)
        self.right_panel.columnconfigure(0, weight=1)

        self.setup_controls(self.left_panel)
        self.setup_camera_area(self.right_panel)

    def setup_controls(self, parent):
        parent.columnconfigure(0, weight=1)
        pad = 6

        db_frame = ttk.LabelFrame(parent, text="Database (.db)", padding=pad)
        db_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        db_frame.columnconfigure(1, weight=1)

        ttk.Label(db_frame, text="Select DB:", font=self.get_small_font()).grid(row=0, column=0, sticky="w")
        self.db_combo = ttk.Combobox(db_frame, textvariable=self.db_var, state="readonly", font=self.get_scaled_font())
        self.db_combo.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        # No auto-hashing on combobox change; user must click Select
        ttk.Button(db_frame, text="Select", command=self.on_db_confirm).grid(row=0, column=2, padx=(4, 0))

        sort_frame = ttk.LabelFrame(parent, text="Sort Method", padding=pad)
        sort_frame.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        for i, (label, value) in enumerate([
            ("Color", "color"),
            ("Set", "set"),
            ("Price", "price"),
            ("Buy Mode", "buy"),
        ]):
            ttk.Radiobutton(sort_frame, text=label, value=value, variable=self.sort_var).grid(row=i, column=0, sticky="w")

        thresh = ttk.Frame(sort_frame)
        thresh.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        thresh.columnconfigure(1, weight=1)
        ttk.Label(thresh, text="Price $", font=self.get_small_font()).grid(row=0, column=0, sticky="w")
        ttk.Entry(thresh, textvariable=self.threshold_var, width=10).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ctrl = ttk.LabelFrame(parent, text="Sorter Control", padding=pad)
        ctrl.grid(row=3, column=0, sticky="ew")
        self.start_button = ttk.Button(ctrl, text="Start", command=self.start_sorting)
        self.start_button.grid(row=0, column=0, sticky="ew", pady=2)
        self.pause_button = ttk.Button(ctrl, text="Pause", command=self.pause_sorting, state="disabled")
        self.pause_button.grid(row=1, column=0, sticky="ew", pady=2)
        self.stop_button = ttk.Button(ctrl, text="Stop", command=self.stop_sorting, state="disabled")
        self.stop_button.grid(row=2, column=0, sticky="ew", pady=2)

    def setup_camera_area(self, parent):
        status = ttk.LabelFrame(parent, text="Current Status", padding=6)
        status.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.status_state_label = ttk.Label(status, text="IDLE", font=self.get_scaled_font())
        self.status_state_label.grid(row=0, column=0, sticky="w")

        self.camera_frame = ttk.LabelFrame(parent, text="Live Camera Feed", padding=6)
        self.camera_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        self.video_label = tk.Label(self.camera_frame, text="Camera feed will appear here",
                                    bg="black", fg="white", font=self.get_scaled_font())
        self.video_label.grid(row=0, column=0, sticky="nsew")

        data_frame = ttk.LabelFrame(parent, text="Current Card Data", padding=6)
        data_frame.grid(row=2, column=0, sticky="nsew")
        self.card_data_text = tk.Text(data_frame, height=6, wrap=tk.WORD, font=self.get_small_font())
        self.card_data_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(data_frame, orient="vertical", command=self.card_data_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.card_data_text.configure(yscrollcommand=sb.set)

    # ---------------- DB ----------------
    def refresh_db_list(self):
        try:
            dbs = list_db_files()
            display = [os.path.splitext(db)[0] for db in dbs]
            self.db_combo["values"] = display
            if main_sqlite.SELECTED_DB_NAME and os.path.splitext(main_sqlite.SELECTED_DB_NAME)[0] in display:
                self.db_var.set(os.path.splitext(main_sqlite.SELECTED_DB_NAME)[0])
            elif display:
                self.db_var.set(display[0])
        except Exception as e:
            messagebox.showerror("DB Error", f"Failed to list DB files in ./data\n{e}")

    def on_db_confirm(self):
        if self.hashing_in_progress:
            self.add_log("Hash indexing already in progress; please wait…")
            return

        chosen = self.db_var.get().strip()
        main_sqlite.SELECTED_DB_NAME = chosen + ".db" if chosen else None
        self.add_log(f"DB set to: {main_sqlite.SELECTED_DB_NAME}")

        if not main_sqlite.SELECTED_DB_NAME:
            messagebox.showwarning("Missing DB", "Please choose a database from the dropdown.")
            return

        self.hashing_in_progress = True
        self.status_state_label.config(text="HASHING…")
        popup = BusyPopup(self.parent, text=f"Building hash index for {main_sqlite.SELECTED_DB_NAME}…")
        self.add_log(f"Indexing hashes from {main_sqlite.SELECTED_DB_NAME} …")

        def worker():
            err = None
            count = 0
            try:
                count = ensure_loaded_for_db(main_sqlite.SELECTED_DB_NAME, on_status=self.add_log)
            except Exception as e:
                err = e
            def finish():
                try: popup.close()
                except Exception: pass
                if err:
                    messagebox.showerror("Hash Index Error", str(err))
                    self.add_log(f"Hash indexing failed: {err}")
                else:
                    self.add_log(f"Hash index ready ({count} cards).")
                self.status_state_label.config(text="IDLE")
                self.hashing_in_progress = False
            self.parent.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Sorting ----------------
    def start_sorting(self):
        if not self.db_var.get():
            messagebox.showwarning("Missing DB", "Please select a database (.db) and click Select.")
            return
        self.shared_data["sorting_active"] = True
        self.shared_data["current_sort_method"] = self.sort_var.get()
        try:
            self.shared_data["current_threshold"] = float(self.threshold_var.get())
        except ValueError:
            self.shared_data["current_threshold"] = 1000000

        self.start_button.config(state="disabled")
        self.pause_button.config(state="normal")
        self.stop_button.config(state="normal")

        if self.camera is None:
            self.start_camera()
        self.add_log("Sorting started")

    def pause_sorting(self):
        active = self.shared_data.get("sorting_active", False)
        self.shared_data["sorting_active"] = not active
        self.pause_button.config(text=("Resume" if active else "Pause"))
        self.add_log("Sorting paused" if active else "Sorting resumed")

    def stop_sorting(self):
        self.shared_data["sorting_active"] = False
        self.start_button.config(state="normal")
        self.pause_button.config(state="disabled", text="Pause")
        self.stop_button.config(state="disabled")
        if self.camera:
            self.camera.release()
            self.camera = None
        self.add_log("Sorting stopped")

    # ---------------- Camera ----------------
    def start_camera(self):
        try:
            self.camera = cv2.VideoCapture(0)
            if self.camera.isOpened():
                self.camera_display_size = (640, 480)
                self.update_camera()
                self.add_log("Camera started")
            else:
                self.add_log("Failed to start camera")
        except Exception as e:
            self.add_log(f"Camera error: {e}")

    def update_camera(self):
        if self.camera and self.camera.isOpened():
            ret, frame = self.camera.read()
            if ret:
                display_frame = self.process_frame_for_cards(frame) if self.shared_data.get("sorting_active") else frame
                img = Image.fromarray(cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)).resize(self.camera_display_size, Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.video_label.config(image=photo)
                self.video_label.image = photo
            self.parent.after(33, self.update_camera)

    # ---------------- Processing ----------------
    def detect_card_name(self, frame, card_approx):
        start = time.time()
        names, attempts = [], 0
        outer = time.time()
        while len(names) < 2 and attempts < MAX_ATTEMPTS_NAME and time.time() - outer < TIMEOUT_NAME:
            time.sleep(0.1)
            t = find_text(frame, card_approx)
            attempts += 1
            if t:
                names.append(t)
        if not names:
            logger.warning("Could not find name.")
            return None
        name = Counter(names).most_common(1)[0][0]
        logger.info(f"OCR {time.time()-start:.2f}s attempts={attempts} best={name}")
        return name

    def process_frame_for_cards(self, frame):
        disp = frame.copy()
        card_approx = find_card_contour(frame)
        if card_approx is None:
            return disp
        name = self.detect_card_name(frame, card_approx)
        if not name:
            main_sqlite.handle_unrecognized_card(disp, card_approx, reason="Name not found")
            self._set_shared_card_status_unrecognized("Name not found")
            return disp
        main_sqlite.process_card_with_db(disp, frame, card_approx, name, self.shared_data, self.serial_connected, self.send_to_arduino)
        return disp

    # ---------------- Utils ----------------
    def _set_shared_card_status_unrecognized(self, reason):
        self.shared_data["current_card_data"] = {"status": "unrecognized", "reason": reason, "bin": 33}

    def add_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.shared_data.setdefault("system_log", []).append(entry)
        if len(self.shared_data["system_log"]) > 1000:
            self.shared_data["system_log"] = self.shared_data["system_log"][-1000:]

    def update(self):
        self.status_state_label.config(text=("SORTING" if self.shared_data.get("sorting_active") else "IDLE"))
        self.card_data_text.delete(1.0, tk.END)
        data = self.shared_data.get("current_card_data", {})
        self.card_data_text.insert(tk.END, json.dumps(data, indent=2) if data else "No card data")
