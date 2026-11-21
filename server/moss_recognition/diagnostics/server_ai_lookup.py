"""Opt-in MOSS camera client: the PC sends pixels, not an OCR guess.

No motor commands are issued here. Reuses the existing DPAPI-protected
lookup-only sorter credential and proposed-bin decision rules.
"""
import base64
import io
import json
import os
import re
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from hosted_live_lookup import LookupUnavailable, NoRedirect, proposed_bin, tight_camera_image
from PIL import Image

ENDPOINT = os.environ.get("MOSS_AI_ENDPOINT", "https://vps143818.inmotionhosting.com/api/v1/recognize-image")


class HostedLookup:
    def __init__(self):
        self.key = os.environ.get("MOSS_LOOKUP_KEY", "").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", self.key):
            raise LookupUnavailable("Sorter's lookup-only credential is missing")
        self.opener = build_opener(NoRedirect())

    def recognize(self, image, margin_mm, footer_at_edge):
        start = perf_counter()
        if footer_at_edge:
            return None, "Printed footer may be clipped", None
        if not isinstance(image, Image.Image):
            raise LookupUnavailable("Camera image is missing")
        try:
            card = tight_camera_image(image, margin_mm)
            out = io.BytesIO()
            card.save(out, format="JPEG", quality=91)
        except (ValueError, OSError) as exc:
            raise LookupUnavailable("Camera crop is invalid") from exc
        payload = {"game": "magic",
                   "image_base64": base64.b64encode(out.getvalue()).decode("ascii"),
                   "footer_at_frame_edge": False}
        req = Request(ENDPOINT, data=json.dumps(payload).encode("utf-8"), method="POST",
                      headers={"Content-Type": "application/json", "Accept": "application/json",
                               "X-MOSS-Lookup-Key": self.key, "User-Agent": "MOSS-AI-Camera/0.1"})
        try:
            with self.opener.open(req, timeout=18) as response:
                result = json.load(response)
        except HTTPError as err:
            raise LookupUnavailable("Hosted AI HTTP error " + str(err.code)) from err
        except (URLError, TimeoutError, ValueError, OSError) as err:
            raise LookupUnavailable("Hosted AI lookup unavailable") from err
        ms = round((perf_counter() - start) * 1000)
        if not isinstance(result, dict) or result.get("advisory_only") is not True:
            raise LookupUnavailable("Invalid hosted AI response")
        if result.get("status") != "candidate":
            return None, result.get("status", "needs_review"), ms
        candidate = result.get("candidate")
        evidence = (result.get("ai") or {}).get("evidence") or {}
        if (not isinstance(candidate, dict) or not str(candidate.get("product_id", "")).isdigit()
            or not candidate.get("name") or not candidate.get("set")
            or candidate.get("set") != evidence.get("set_abbreviation")):
            return None, "candidate_identity_not_verified", ms
        from hosted_live_lookup import collector_key
        if collector_key(candidate.get("collector_number")) != collector_key(evidence.get("collector_number")):
            return None, "candidate_collector_not_verified", ms
        return candidate, "candidate", ms
