"""Fail-closed tests for server-side image and OCR routing; no models required."""
import base64
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_image_api import ImageRequestError, load_image, recognize_image
from ai_worker import footer_evidence


def sample():
    picture = Image.new("RGB", (745, 1043), (220, 216, 208))
    stream = io.BytesIO()
    picture.save(stream, format="JPEG")
    return {"game": "magic", "image_base64": base64.b64encode(stream.getvalue()).decode()}


class Catalog:
    def __init__(self):
        self.calls = []

    def match(self, hashes, evidence):
        self.calls.append((hashes, evidence))
        return {"status": "candidate", "candidate": {"name": "Spectral Interference",
                "set": "dft", "collector_number": "63", "product_id": 123},
                "advisory_only": True}


class AIImageTests(unittest.TestCase):
    def test_image_and_footer_required(self):
        catalog = Catalog()
        self.assertEqual(recognize_image(catalog, dict(sample(), footer_at_frame_edge=True))["status"],
                         "image_incomplete")
        self.assertFalse(catalog.calls)

    def test_unreadable_footer_must_not_be_sorted(self):
        catalog = Catalog()
        result = recognize_image(catalog, sample(),
                                 worker=lambda img: {"engine": "paddleocr", "evidence": {},
                                                     "review_reason": "no_pair"})
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["candidate"])
        self.assertFalse(catalog.calls)

    def test_verified_footer_calls_catalog_once(self):
        catalog = Catalog()
        result = recognize_image(catalog, sample(),
                    worker=lambda img: {"engine": "paddleocr",
                        "evidence": {"set_abbreviation": "DFT", "collector_number": "063",
                                     "foil_mark": "dot"}})
        self.assertEqual(result["status"], "candidate")
        self.assertTrue(result["advisory_only"])
        self.assertEqual(catalog.calls[0][1]["collector_number"], "63")
        self.assertEqual(catalog.calls[0][1]["set_abbreviation"], "dft")
        self.assertEqual(len(catalog.calls[0][0]), 3)

    def test_invalid_worker_evidence_is_fail_closed(self):
        with self.assertRaises(ImageRequestError) as caught:
            recognize_image(Catalog(), sample(), worker=lambda img: {"evidence": {"foil_mark": "invented"}})
        self.assertEqual(caught.exception.status, 503)

    def test_worker_unavailable_is_fail_closed(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ImageRequestError) as caught:
                recognize_image(Catalog(), sample())
        self.assertEqual(caught.exception.code, "ai_unavailable")

    def test_reject_invalid_data(self):
        with self.assertRaises(ImageRequestError):
            recognize_image(Catalog(), {"game": "pokemon", "image_base64": sample()["image_base64"]})
        with self.assertRaises(ImageRequestError):
            load_image("invalid%%")
        with self.assertRaises(ImageRequestError):
            recognize_image(Catalog(), dict(sample(), secret="hi"))
        with self.assertRaises(ImageRequestError):
            recognize_image(Catalog(), dict(sample(), footer_at_frame_edge="false"))


class FooterParserTests(unittest.TestCase):
    def test_mtg_modern_number_and_set(self):
        evidence, reason = footer_evidence([("194", 0.97), ("SOS ★ EN", 0.95)])
        self.assertIsNone(reason)
        self.assertEqual(evidence, {"set_abbreviation": "sos", "collector_number": "194",
                                    "foil_mark": "star"})

    def test_nonfoil_and_leading_zeroes(self):
        evidence, reason = footer_evidence([("00063", 0.98), ("DFT • EN", 0.99)])
        self.assertEqual(evidence["collector_number"], "63")
        self.assertEqual(evidence["foil_mark"], "dot")

    def test_conflicting_and_low_confidence_ocr(self):
        self.assertEqual(footer_evidence([("63", 0.91), ("DFT EN", 0.96), ("64", 0.93),
                                           ("OTJ EN", 0.97)])[0], {})
        self.assertEqual(footer_evidence([("63", 0.45), ("DFT EN", 0.98)])[0], {})
        self.assertEqual(footer_evidence([("DFT EN", 0.99)])[0], {})


if __name__ == "__main__":
    unittest.main()
