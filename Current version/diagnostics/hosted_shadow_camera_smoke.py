"""Camera-only end-to-end smoke test for the hosted sorter plugin.

This opens the camera, applies the maintained scanner's card crop, calls the
hosted AI recognition plugin, verifies the exact printing against local scanner
results when available, and proposes a bin. It never opens a serial connection,
writes collection/inventory data, or sends a movement command.
"""
import json
import os
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimized_scanner import OptimizedCardScanner
from plugins.recognition_stub import Plugin


def identity(card):
    if not isinstance(card, dict):
        return None
    return {
        "product_id": str(card.get("product_id") or card.get("UniqueID") or ""),
        "name": card.get("name"),
        "set": str(card.get("set") or card.get("set_code") or "").lower(),
        "number": str(card.get("number") or card.get("collector_number") or "").lower(),
    }


def main():
    if os.environ.get("MOSS_AI_SORTING_ENABLED") != "1":
        raise SystemExit("Set MOSS_AI_SORTING_ENABLED=1 to run this smoke test.")
    if os.environ.get("MOSS_AI_SHADOW_MODE") != "1":
        raise SystemExit("Set MOSS_AI_SHADOW_MODE=1; this test is camera-only.")

    scanner = OptimizedCardScanner(
        db_path=str(ROOT / "recognition_data" / "unified_card_database.db"),
        enable_collection=False,
        auto_vector_when_unfiltered=False,
        prompt_for_details=False,
    )
    plugin = Plugin()
    if not plugin.enabled or plugin.lookup is None:
        raise SystemExit("Hosted AI plugin is unavailable; check local credential and endpoint.")

    camera_index = int(os.environ.get("MOSS_CAMERA_INDEX", "0"))
    capture = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not capture.isOpened():
        capture.release()
        capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        raise SystemExit(f"Camera index {camera_index} could not be opened.")

    try:
        # Prefer the known camera mode; drivers may clamp unsupported settings.
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1600)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 1200)
        started = time.perf_counter()
        timeout = float(os.environ.get("MOSS_CAMERA_TIMEOUT_SECONDS", "30"))
        last_attempt = 0.0
        while time.perf_counter() - started < timeout:
            ok, frame = capture.read()
            if not ok or frame is None:
                time.sleep(0.1)
                continue
            now = time.perf_counter()
            if now - last_attempt < 0.5:
                continue
            last_attempt = now
            contour = scanner._find_card_contour(frame)
            if contour is None:
                continue

            began = time.perf_counter()
            warped = scanner._get_perspective_corrected_card(frame, contour)
            result = plugin.recognize(frame, contour, scanner, warped)
            elapsed_ms = round((time.perf_counter() - began) * 1000)
            status = result.get("_moss_ai_status") if isinstance(result, dict) else None
            record = {
                "status": status or "invalid",
                "elapsed_ms": elapsed_ms,
                "camera_resolution": [int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                      int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))],
            }
            if status == "candidate":
                hosted = identity(result)
                local = identity(scanner._process_card_from_contour(frame, contour))
                agrees = (local is None or (
                    local["product_id"] == hosted["product_id"]
                    and local["set"] == hosted["set"]
                    and local["number"].lstrip("0") == hosted["number"].lstrip("0")
                ))
                if not result.get("printing_verified") or not agrees:
                    record.update({"status": "review",
                                   "reason": "Hosted/local exact-printing disagreement",
                                   "hosted": hosted, "local": local})
                else:
                    mode = os.environ.get("MOSS_SHADOW_SORT_MODE", "color")
                    record.update({
                        "printing_verified": True,
                        "local_agreement": local is not None,
                        "card": hosted,
                        "finish": result.get("actual_finish") or "unconfirmed",
                        "bin_mode": mode,
                        "proposed_bin": scanner.get_bin_number(result, mode),
                    })
            elif status in {"review", "unavailable"}:
                record["reason"] = result.get("_moss_reason", "No candidate")
            print(json.dumps(record, ensure_ascii=False), flush=True)
            if status in {"candidate", "review", "unavailable"}:
                return 0 if record["status"] == "candidate" else 2
        print(json.dumps({"status": "no_card_detected",
                          "elapsed_ms": round((time.perf_counter() - started) * 1000)}),
              flush=True)
        return 3
    finally:
        capture.release()
        scanner.close()


if __name__ == "__main__":
    raise SystemExit(main())
