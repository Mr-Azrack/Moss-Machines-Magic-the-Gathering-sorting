"""Read a camera crop and verify a Magic printing with the hosted catalog."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from evidence_replay import consensus, footer_from_lines, footer_probes, read_windows
CATALOG_SOURCE = Path(__file__).resolve().parent.parent
if (CATALOG_SOURCE / "catalog_match.py").is_file():
    sys.path.insert(0, str(CATALOG_SOURCE))
    from catalog_match import ALGORITHM, collector_key, fingerprint
else:
    from hosted_catalog_match import ALGORITHM, collector_key, fingerprint

ENDPOINT = "https://cardsorter.thegamestationlw.com/api/v1/recognize"
COLORS = dict(W="White", U="Blue", B="Black", R="Red", G="Green")


class LookupUnavailable(Exception):
    """The sorter should hold the card while its lookup service is unavailable."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_footer(image):
    with tempfile.TemporaryDirectory(prefix="moss-live-footer-") as directory:
        scratch = Path(directory)
        paths = []
        for name, probe in footer_probes(image):
            path = scratch / f"{name}.png"
            probe.save(path)
            paths.append(path)
        try:
            results = read_windows(paths, scratch / "probe-files.json")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise LookupUnavailable("Windows OCR unavailable") from exc
    return consensus([footer_from_lines(result["lines"]) for result in results])


def tight_camera_image(image, margin_mm):
    if margin_mm == 2 and image.size == (793, 1091):
        return image.crop((24, 24, 769, 1067))
    if margin_mm == 0 and image.size == (745, 1043):
        return image
    raise ValueError("Unexpected camera crop size")


class HostedLookup:
    def __init__(self):
        self.key = os.environ.get("MOSS_LOOKUP_KEY", "").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", self.key):
            raise LookupUnavailable("The sorter lookup credential is missing")
        self.opener = build_opener(NoRedirect())

    def recognize(self, image, margin_mm, footer_at_edge):
        entered = perf_counter()
        if footer_at_edge:
            return None, "footer is clipped at the frame edge", None
        evidence = read_footer(image)
        set_code = evidence.get("set_abbreviation", "").lower()
        number = collector_key(evidence.get("collector_number"))
        if not re.fullmatch(r"[a-z0-9]{2,8}", set_code) or not number:
            return None, "set and collector number could not be verified", None
        hashes = fingerprint(tight_camera_image(image, margin_mm))
        payload = {"game": "magic", "algorithm": ALGORITHM,
                   "rgb_phash": hashes, "evidence": evidence}
        request = Request(ENDPOINT, json.dumps(payload).encode("utf-8"),
                          {"X-MOSS-Lookup-Key": self.key,
                           "Content-Type": "application/json",
                           "Accept": "application/json",
                           "User-Agent": "MOSS-Sorter-Live/1.0"}, method="POST")
        try:
            with self.opener.open(request, timeout=8) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise LookupUnavailable(f"recognition service returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            raise LookupUnavailable("recognition service could not be reached") from exc
        elapsed = round((perf_counter() - entered) * 1000)
        if not isinstance(result, dict) or result.get("advisory_only") is not True:
            raise LookupUnavailable("unexpected response from recognition service")
        if result.get("status") != "candidate":
            return None, str(result.get("status", "no match")), elapsed
        card = result.get("candidate")
        if (not isinstance(card, dict) or not str(card.get("product_id", "")).isdigit()
                or not isinstance(card.get("name"), str) or not card["name"].strip()
                or card.get("set") != set_code or collector_key(card.get("collector_number")) != number):
            return None, "hosted candidate disagreed with the printed identity", elapsed
        return card, "candidate", elapsed


def proposed_bin(scanner, card, mode):
    """Use the maintained sorter's bin rules, with evidence missing routed for review."""
    route = card.get("routing") or {}
    card_info = {"name": card["name"], "game": "Magic",
                 "product_id": str(card["product_id"]), "set": card["set"],
                 "number": card["collector_number"], "type": route.get("type_line")}
    if mode == "color":
        colors = route.get("colors")
        if (not isinstance(colors, list) or not all(color in COLORS for color in colors)
                or not isinstance(card_info["type"], str) or not card_info["type"]):
            return "Review (color or type data missing)"
        card_info["colors"] = [COLORS[color] for color in colors]
    elif mode == "finish":
        finish = card.get("finish")
        if finish not in {"nonfoil", "foil", "etched"}:
            return "Review (finish unreadable)"
        card_info["isFoil"] = finish != "nonfoil"
    return scanner.get_bin_number(card_info, mode)
