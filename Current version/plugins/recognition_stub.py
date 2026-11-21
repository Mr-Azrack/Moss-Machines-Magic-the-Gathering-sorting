"""Hosted MOSS AI recognition plugin for the maintained sorter.

It is opt-in through MOSS_AI_SORTING_ENABLED=1. Disabled mode preserves the
existing local matcher. Enabled mode fails closed: service errors are surfaced
to the GUI as an unavailable status, never as a local-recognition fallback.
"""
import os
import re
import subprocess
import sys

import cv2
from PIL import Image

from .base import RecognitionPluginBase


_DIAGNOSTICS = os.environ.get(
    "MOSS_AI_DIAGNOSTICS_PATH",
    r"D:\MOSS_Diagnostics\MOSS_AI_Camera_Test\moss_recognition\diagnostics",
)
_KEY_RE = re.compile(r"[0-9a-f]{64}", re.I)
_COLOR_NAMES = {
    "W": "White", "U": "Blue", "B": "Black",
    "R": "Red", "G": "Green",
}


def _lookup_key():
    key = os.environ.get("MOSS_LOOKUP_KEY", "").strip()
    if _KEY_RE.fullmatch(key):
        return key.lower()
    if os.name != "nt":
        return ""
    script = r"""
$ErrorActionPreference = 'Stop'
$file = Join-Path $env:LOCALAPPDATA 'MOSS\Hosted\lookup.dpapi'
$secure = Get-Content -LiteralPath $file | ConvertTo-SecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { [Console]::Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False, capture_output=True, text=True, timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    key = result.stdout.strip()
    return key.lower() if result.returncode == 0 and _KEY_RE.fullmatch(key) else ""


class Plugin(RecognitionPluginBase):
    name = "recognition"

    def __init__(self):
        self.enabled = os.environ.get("MOSS_AI_SORTING_ENABLED") == "1"
        self.lookup = None
        self.init_error = None
        if not self.enabled:
            return
        try:
            if _DIAGNOSTICS not in sys.path:
                sys.path.insert(0, _DIAGNOSTICS)
            from server_ai_lookup import HostedLookup
            key = _lookup_key()
            if not key:
                raise RuntimeError("lookup credential unavailable")
            previous = os.environ.get("MOSS_LOOKUP_KEY")
            os.environ["MOSS_LOOKUP_KEY"] = key
            try:
                self.lookup = HostedLookup()
            finally:
                if previous is None:
                    os.environ.pop("MOSS_LOOKUP_KEY", None)
                else:
                    os.environ["MOSS_LOOKUP_KEY"] = previous
        except Exception as exc:
            self.init_error = type(exc).__name__

    @staticmethod
    def _footer_clipped(frame, card_approx):
        if frame is None or card_approx is None:
            return True
        height, width = frame.shape[:2]
        try:
            points = card_approx.reshape(-1, 2)
        except Exception:
            return True
        return any(
            int(x) <= 0 or int(y) <= 0
            or int(x) >= width - 1 or int(y) >= height - 1
            for x, y in points
        )

    @staticmethod
    def _card_info(candidate):
        routing = candidate.get("routing") or {}
        colors = routing.get("colors")
        colors = colors if isinstance(colors, list) else None
        color_names = [_COLOR_NAMES[value] for value in colors
                       if value in _COLOR_NAMES] if colors is not None else None
        product_id = str(candidate.get("product_id") or "")
        collector = str(candidate.get("collector_number") or "")
        finish = candidate.get("finish")
        return {
            "_moss_ai_status": "candidate",
            "printing_verified": True,
            "recognition_source": "hosted_ai",
            "product_id": product_id,
            "UniqueID": product_id,
            "name": candidate.get("name"),
            "game": "Magic",
            "set": candidate.get("set"),
            "set_code": candidate.get("set"),
            "number": collector,
            "collector_number": collector,
            "type": routing.get("type_line"),
            "colors": color_names,
            "mana_value": routing.get("mana_value"),
            "cmc": routing.get("mana_value"),
            "rarity": routing.get("rarity"),
            "finishes": candidate.get("finishes", []),
            "actual_finish": finish,
            "finish": finish,
            "finish_source": "footer" if finish else "unconfirmed",
            "market_price": None,
            "hosted_distance": candidate.get("distance"),
        }

    def recognize(self, frame, card_approx, scanner, card_image=None):
        if not self.enabled:
            return None
        if self.lookup is None:
            return {"_moss_ai_status": "unavailable",
                    "_moss_reason": "Hosted recognition configuration is unavailable"}
        try:
            if card_image is None:
                card_image = scanner._get_perspective_corrected_card(frame, card_approx)
            if card_image is None:
                return {"_moss_ai_status": "review",
                        "_moss_reason": "Could not crop the card"}
            height, width = card_image.shape[:2]
            if width > height:
                card_image = cv2.rotate(card_image, cv2.ROTATE_90_CLOCKWISE)
            image = Image.fromarray(cv2.cvtColor(card_image, cv2.COLOR_BGR2RGB))
            image = image.resize((745, 1043), Image.Resampling.LANCZOS)
            card, reason, elapsed = self.lookup.recognize(
                image, 0, self._footer_clipped(frame, card_approx))
            if card is None:
                if reason.startswith("Hold:") or "unavailable" in reason.lower():
                    return {"_moss_ai_status": "unavailable",
                            "_moss_reason": reason, "_moss_ai_ms": elapsed}
                return {"_moss_ai_status": "review",
                        "_moss_reason": reason, "_moss_ai_ms": elapsed}
            return {**self._card_info(card), "_moss_ai_ms": elapsed}
        except Exception as exc:
            return {"_moss_ai_status": "unavailable",
                    "_moss_reason": f"Recognition request failed ({type(exc).__name__})"}
