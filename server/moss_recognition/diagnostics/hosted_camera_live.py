"""Verify the hosted recognition and bin decision using the live camera."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image

from fixed_height_preview import Preview
import os
if os.environ.get("MOSS_AI_SERVER_ENABLED") == "1":
    from server_ai_lookup import HostedLookup, LookupUnavailable, proposed_bin
else:
    from hosted_live_lookup import HostedLookup, LookupUnavailable, proposed_bin

HERE = Path(__file__).resolve().parent
LOG = HERE / "hosted-camera-live.jsonl"
APP = Path(r"D:\sorter\Moss-Machines-Magic-the-Gathering-sorting\Current version")


def get_sorter_rules():
    sys.path.insert(0, str(APP))
    from optimized_scanner import OptimizedCardScanner
    # No initialization, serial port, or local database. The routing methods are pure.
    return object.__new__(OptimizedCardScanner)


class HostedPreview(Preview):
    def __init__(self, root, camera, hosted, sorter):
        super().__init__(root, camera)
        root.title("MOSS AI Camera Test" if os.environ.get("MOSS_AI_SERVER_ENABLED") == "1" else "MOSS Hosted Camera Test")
        self.save_button.pack_forget()
        self.lookup = hosted
        self.sorter = sorter
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.baseline = None
        self.stable = 0
        self.clear = 0
        self.generation = 0
        self.submitted = False
        self.mode = tk.StringVar(value="color")
        self.manual_finish = tk.StringVar(value="Unconfirmed")
        self._last_lookup = None
        strip = ttk.Frame(root, padding=8)
        strip.pack(fill="x")
        ttk.Label(strip, text="Tray rule").pack(side="left")
        mode_box = ttk.Combobox(strip, textvariable=self.mode, values=("color", "finish", "set"),
                                state="readonly", width=12)
        mode_box.pack(side="left", padx=8)
        mode_box.bind("<<ComboboxSelected>>", self.refresh_result)
        ttk.Label(strip, text="Manual finish").pack(side="left", padx=(12, 4))
        finish_box = ttk.Combobox(strip, textvariable=self.manual_finish,
                                  values=("Unconfirmed", "Nonfoil", "Foil", "Etched"),
                                  state="readonly", width=14)
        finish_box.pack(side="left")
        finish_box.bind("<<ComboboxSelected>>", self.refresh_result)
        self.decision = ttk.Label(root, text="Place one card in view", wraplength=660, padding=(8, 0, 8, 8))
        self.decision.pack(fill="x")
        root.after(175, self.check_lookup)

    def check_lookup(self):
        current = self.evidence
        if current is None:
            self.clear += 1
            self.stable = 0
            self.baseline = None
            if self.clear == 3:
                self.submitted = False
                self.generation += 1
                self._last_lookup = None
                self.manual_finish.set("Unconfirmed")
        else:
            self.clear = 0
            stamp = cv2.resize(cv2.cvtColor(current.image, cv2.COLOR_BGR2GRAY), (40, 56))
            if self.baseline is not None and cv2.mean(cv2.absdiff(self.baseline, stamp))[0] < 10:
                self.stable += 1
            else:
                self.stable = 0
                if self.submitted:
                    self.generation += 1
                self.submitted = False
            self.baseline = stamp
        if self.pending and self.pending.done():
            self.show_lookup()
        if current and self.stable >= 3 and not self.submitted and self.pending is None:
            self.submitted = True
            self.lookup_generation = self.generation
            image = Image.fromarray(cv2.cvtColor(current.image, cv2.COLOR_BGR2RGB))
            self.decision.configure(text="Checking the card against the hosted catalog…")
            self.pending = self.executor.submit(
                self.lookup.recognize, image, current.outside_margin_mm, current.footer_at_frame_edge)
        self.root.after(175, self.check_lookup)

    def show_lookup(self):
        try:
            card, reason, elapsed = self.pending.result()
        except LookupUnavailable as exc:
            card, reason, elapsed = None, f"Hold: {exc}", None
        except Exception as exc:
            card, reason, elapsed = None, f"Hold: lookup failed ({type(exc).__name__})", None
        self.pending = None
        if self.lookup_generation != self.generation:
            self.decision.configure(text="Card moved during lookup. Place it back to retry.")
            return
        self._last_lookup = (dict(card) if card else None, reason, elapsed)
        self.refresh_result(event_type="scan")

    def refresh_result(self, _event=None, event_type="operator_update"):
        if self._last_lookup is None:
            return
        card, reason, elapsed = self._last_lookup
        mode = self.mode.get()
        finish_source = None
        if card:
            card = dict(card)
            manual_finish = self.manual_finish.get().lower()
            if manual_finish in {"nonfoil", "foil", "etched"}:
                card["finish"] = manual_finish
                finish_source = "operator"
            else:
                finish_source = "footer" if card.get("finish") in {"nonfoil", "foil", "etched"} else "unconfirmed"
            tray = proposed_bin(self.sorter, card, mode)
            finish = card.get("finish") or "finish unconfirmed"
            if finish_source == "operator":
                finish += " (manual)"
            message = (f"{card['name']} {card['set'].upper()} #{card['collector_number']} "
                       f"({finish}) → {tray} [{elapsed} ms]")
        else:
            tray = "Hold" if reason.startswith("Hold:") else "RejectCard"
            message = f"Review: {reason}"
        self.decision.configure(text=message)
        if event_type:
            event = {"utc": datetime.now(timezone.utc).isoformat(), "event_type": event_type,
                     "printing": card, "finish_source": finish_source, "mode": mode,
                     "proposed_tray": tray, "result": reason, "elapsed_ms": elapsed}
            with LOG.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().close()


def run_saved_controls(hosted, sorter):
    with (HERE / "sol-ring-reference" / "foil-and-controls.csv").open(encoding="utf-8-sig", newline="") as stream:
        cases = [row for row in csv.DictReader(stream) if row["reference_source"] == "Actual sorter capture"]
    if not cases:
        raise RuntimeError("No saved controls found")
    for case in cases:
        path = Path(case["image"])
        capture = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        with Image.open(path) as image:
            card, reason, ms = hosted.recognize(image, capture["outside_margin_mm"], capture["footer_at_frame_edge"])
        if not card:
            raise RuntimeError(f"{case['label']}: {reason}")
        expected_finish = "foil" if card["set"] == "sos" else "nonfoil"
        if (card["set"] != case["reference_set"].lower()
                or str(card["collector_number"]).lstrip("0") != case["reference_number"].lstrip("0")
                or card.get("finish") != expected_finish):
            raise RuntimeError(f"{case['label']}: printing or finish disagreed with the control")
        print(f"{card['name']} {card['set']} #{card['collector_number']}: "
              f"{proposed_bin(sorter, card, 'finish')}, color {proposed_bin(sorter, card, 'color')}, {ms} ms")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controls", action="store_true")
    args = parser.parse_args()
    hosted = HostedLookup()
    sorter = get_sorter_rules()
    if args.controls:
        run_saved_controls(hosted, sorter)
        return
    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    try:
        if not camera.isOpened():
            raise SystemExit("Close the other sorter camera windows and reopen this test.")
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1600)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1200)
        root = tk.Tk()
        HostedPreview(root, camera, hosted, sorter)
        root.mainloop()
    finally:
        camera.release()


if __name__ == "__main__":
    main()
