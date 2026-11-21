"""Server-side MOSS image recognition entrypoint. Never authorizes sorter motion.

The WSGI application keeps the MTG catalog and fingerprint matcher in memory;
a private loopback PaddleOCR worker reads the physical card footer. Both the
image and evidence are processed locally (no Scryfall calls during a scan).
"""
import base64
import binascii
import io
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image
from catalog_match import fingerprint, parse_evidence

WORKER_ENDPOINT = "http://127.0.0.1:8765/infer"
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 9_000_000


class ImageRequestError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def load_image(encoded):
    if not isinstance(encoded, str) or len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 8:
        raise ImageRequestError(413, "image_size", "Image missing or too large")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ImageRequestError(400, "invalid_image", "Invalid image encoding")
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ImageRequestError(413, "image_size", "Image empty or too large")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ("JPEG", "PNG"):
                raise ImageRequestError(415, "image_format", "Expected JPEG or PNG")
            width, height = source.size
            if width < 120 or height < 160 or width * height > MAX_PIXELS:
                raise ImageRequestError(400, "image_dimensions", "Invalid image dimensions")
            source.load()
            return source.convert("RGB")
    except ImageRequestError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ImageRequestError(400, "invalid_image", "Unreadable image")


def query_worker(image):
    token = os.environ.get("MOSS_AI_WORKER_TOKEN", "")
    if len(token) < 32:
        raise ImageRequestError(503, "ai_unavailable", "Vision worker is not provisioned")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92)
    payload = json.dumps({"image_base64": base64.b64encode(output.getvalue()).decode("ascii")}).encode("utf-8")
    request = Request(WORKER_ENDPOINT, data=payload,
                      headers={"Content-Type": "application/json", "Accept": "application/json",
                               "X-MOSS-Worker-Token": token}, method="POST")
    try:
        with build_opener(NoRedirects()).open(request, timeout=14) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise ImageRequestError(503, "ai_unavailable", "Unexpected vision worker reply")
            result = json.load(io.TextIOWrapper(response, encoding="utf-8"))
    except ImageRequestError:
        raise
    except (HTTPError, URLError, OSError, TimeoutError, ValueError):
        raise ImageRequestError(503, "ai_unavailable", "Vision worker could not process the image")
    if not isinstance(result, dict) or result.get("engine") != "paddleocr":
        raise ImageRequestError(503, "ai_unavailable", "Invalid vision worker result")
    return result


def recognize_image(catalog, payload, *, worker=query_worker):
    """Return an ADVISORY candidate only when the physical footer is verified."""
    if not isinstance(payload, dict) or payload.get("game") != "magic":
        raise ImageRequestError(400, "invalid_game", "Expected a Magic scan")
    if set(payload) - {"game", "image_base64", "footer_at_frame_edge"}:
        raise ImageRequestError(400, "invalid_fields", "Unsupported image request fields")
    if not isinstance(payload.get("footer_at_frame_edge", False), bool):
        raise ImageRequestError(400, "invalid_fields", "footer_at_frame_edge must be boolean")
    if payload.get("footer_at_frame_edge"):
        return {"status": "image_incomplete", "candidate": None, "suggestions": [],
                "advisory_only": True, "reason": "Printed footer may be outside camera view"}
    image = load_image(payload.get("image_base64"))
    worker_result = worker(image)
    if not isinstance(worker_result, dict):
        raise ImageRequestError(503, "ai_unavailable", "Invalid OCR worker response")
    try:
        evidence = parse_evidence(worker_result.get("evidence", {}))
    except (TypeError, ValueError):
        raise ImageRequestError(503, "ai_unavailable", "Invalid OCR worker evidence")
    if worker_result.get("engine") != "paddleocr":
        raise ImageRequestError(503, "ai_unavailable", "Unexpected OCR worker response")
    evidence_meta = {"engine": "paddleocr", "evidence": evidence,
                     "review_reason": worker_result.get("review_reason")}
    image_hashes = tuple(int(part, 16) for part in fingerprint(image))
    if not evidence.get("set_abbreviation") or not evidence.get("collector_number"):
        # For old cards without modern footer codes, give the operator image
        # suggestions but NEVER return a machine-acceptable candidate.
        preliminary = catalog.match(image_hashes, evidence=evidence)
        return {"status": "needs_review", "candidate": None,
                "suggestions": preliminary.get("suggestions", [])[:3],
                "advisory_only": True, "ai": evidence_meta}
    result = catalog.match(image_hashes, evidence=evidence)
    result["ai"] = evidence_meta
    # The server is an advisor; only a separate verified physical sorter gate can move a card.
    result["advisory_only"] = True
    return result
