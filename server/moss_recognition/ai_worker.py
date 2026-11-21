"""Private CPU-only PaddleOCR worker for printed MTG footer evidence.

Never expose this service to the internet. Start on a dedicated Linux Python
3.11/3.12 worker with installed PaddleOCR weights and bind to loopback.
"""
import base64
import binascii
import hmac
import io
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageOps, UnidentifiedImageError

LANGS = r"EN|DE|ES|FR|IT|PT|JA|JP|KO|KR|RU|ZHS|ZHT|CS|CT|HE|LA|GRC|AR|SA|PH"
SET = re.compile(rf"^([A-Z][A-Z0-9]{{1,7}})(?:\s*([★☆*•·∙])\s*|\s+)({LANGS})\b")
NUMBER = re.compile(r"^(?:[CURMLST]\s+)?([0-9]{1,5}[A-Z]?)\s*(?:/\s*[0-9]{1,5})?(?:\s*[CURMLST])?\s*$")
MARK = {"★": "star", "☆": "star", "*": "star", "•": "dot", "·": "dot", "∙": "dot"}
MAX_BODY = 4 * 1024 * 1024


def footer_evidence(lines):
    """Require an adjacent collector-number / set-language pair, not arbitrary OCR."""
    cleaned = [(str(text).upper().strip(), float(score)) for text, score in lines
               if isinstance(text, str) and float(score) >= 0.72]
    pairs = []
    for index, (value, score) in enumerate(cleaned):
        m = SET.match(value)
        if not m:
            continue
        for j in (index - 1, index + 1):
            if not 0 <= j < len(cleaned):
                continue
            n = NUMBER.fullmatch(cleaned[j][0])
            if n and min(score, cleaned[j][1]) >= 0.72:
                number = n.group(1).lower().lstrip("0") or "0"
                mark = MARK.get(m.group(2)) if score >= 0.82 else None
                pairs.append((m.group(1).lower(), number, mark))
    ids = {(a, b) for a, b, _ in pairs}
    if len(ids) != 1:
        return {}, "no_unambiguous_footer_pair"
    set_code, number = next(iter(ids))
    evidence = {"set_abbreviation": set_code, "collector_number": number}
    marks = {mark for _, _, mark in pairs if mark}
    if len(marks) == 1:
        evidence["foil_mark"] = next(iter(marks))
    return evidence, None


def crop_footer(image):
    image = ImageOps.exif_transpose(image)
    if image.width > image.height:
        image = image.transpose(Image.Transpose.ROTATE_90)
    w, h = image.size
    # Keep the bottom edge and the physical set/number line. The PC sends
    # a tight crop; do not remove printed pixels or enforce an outside margin.
    footer = image.crop((0, round(h * 0.79), round(w * 0.64), h))
    scale = min(3.0, 1150.0 / max(1, footer.width))
    return ImageOps.autocontrast(
        ImageOps.grayscale(footer).resize(
            (max(1, round(footer.width * scale)), max(1, round(footer.height * scale))),
            Image.Resampling.LANCZOS))


class PaddleFooterModel:
    def __init__(self):
        from paddleocr import PaddleOCR
        self.model = PaddleOCR(
            lang="en", device="cpu",
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="en_PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False)
        # Run one warm-up so the first physical card does not pay model startup cost.
        self.predict(Image.new("RGB", (800, 1200), "white"))
        print("MOSS PaddleOCR warm-up complete", flush=True)

    def predict(self, image):
        import numpy as np
        roi = crop_footer(image)
        results = self.model.predict(np.asarray(roi.convert("RGB")))
        lines = []
        for item in results:
            data = item.json
            if "res" in data:
                data = data["res"]
            for text, score, box in zip(data.get("rec_texts", []),
                                        data.get("rec_scores", []),
                                        data.get("rec_boxes", [])):
                try:
                    y = (float(box[1]) + float(box[3])) / 2
                except (IndexError, TypeError, ValueError):
                    continue
                lines.append((y, str(text), float(score)))
        lines.sort(key=lambda item: item[0])
        evidence, reason = footer_evidence([(text, score) for _, text, score in lines])
        return {"engine": "paddleocr", "evidence": evidence, "review_reason": reason}


def make_handler(model, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # No scan contents, credentials or images in logs.
            pass

        def reply(self, status, body):
            out = json.dumps(body, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def authenticated(self):
            return hmac.compare_digest(
                self.headers.get("X-MOSS-Worker-Token", ""), token)

        def do_GET(self):
            if self.path != "/health":
                return self.reply(404, {"error": "not_found"})
            return self.reply(200, {"status": "ready", "engine": "paddleocr"})

        def do_POST(self):
            if self.path != "/infer":
                return self.reply(404, {"error": "not_found"})
            if not self.authenticated():
                return self.reply(401, {"error": "unauthorized"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    return self.reply(413, {"error": "size_limit"})
                obj = json.loads(self.rfile.read(length))
                binary = base64.b64decode(obj["image_base64"], validate=True)
                if not binary or len(binary) > 3 * 1024 * 1024:
                    return self.reply(413, {"error": "size_limit"})
                with Image.open(io.BytesIO(binary)) as image:
                    if image.format not in ("JPEG", "PNG"):
                        return self.reply(415, {"error": "unsupported_image"})
                    if image.width * image.height > 9_000_000:
                        return self.reply(413, {"error": "image_dimensions"})
                    image.load()
                    result = model.predict(image.convert("RGB"))
                return self.reply(200, result)
            except (KeyError, TypeError, ValueError, OSError, binascii.Error,
                    UnidentifiedImageError):
                return self.reply(400, {"error": "invalid_image"})
            except Exception:
                return self.reply(503, {"error": "model_inference_unavailable"})
    return Handler


def main():
    token = os.environ.get("MOSS_AI_WORKER_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("Set a strong MOSS_AI_WORKER_TOKEN first")
    model = PaddleFooterModel()
    with HTTPServer(("127.0.0.1", 8765), make_handler(model, token)) as server:
        print("MOSS private OCR worker ready on loopback", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
