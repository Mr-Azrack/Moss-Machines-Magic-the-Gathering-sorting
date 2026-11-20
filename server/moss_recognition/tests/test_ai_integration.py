"""Exercise WSGI AI authentication and verified-example export, no AI weights."""
from contextlib import closing
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_image_api import recognize_image
from export_verified_training import export_manifest
from server import create_app
from test_ai_pipeline import Catalog, sample
from test_server import Client


class WSGITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.app = create_app({"data_dir": self.tmp.name, "api_key": "X" * 40,
                               "admin_password": "P" * 40, "session_secret": "S" * 40})
        self.client = Client(self.app)
        self.key = "a" * 64
        (self.home / "lookup-token.sha256").write_text(hashlib.sha256(self.key.encode()).hexdigest())
        self.app.catalog = Catalog()

    def test_auth_and_live_ocr_advisory_response(self):
        data = sample()
        self.assertEqual(self.client.request("POST", "/api/v1/recognize-image", data=data)[0], 401)
        self.assertEqual(self.client.request("POST", "/api/v1/recognize-image",
                                             data=data, lookup_key="b" * 64)[0], 401)
        with patch("ai_image_api.recognize_image",
                   side_effect=lambda catalog, payload: recognize_image(
                       catalog, payload, worker=lambda image: {
                         "engine": "paddleocr", "evidence": {
                          "set_abbreviation": "DFT", "collector_number": "63"}})):
            code, _, result = self.client.request("POST", "/api/v1/recognize-image",
                                                   data=data, lookup_key=self.key)
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "candidate")
        self.assertTrue(result["advisory_only"])
        self.assertEqual(result["ai"]["evidence"]["set_abbreviation"], "dft")
        self.assertEqual(self.client.request("GET", "/api/v1/metrics", lookup_key=self.key)[0], 401)

    def test_missing_worker_is_unavailable_not_a_match(self):
        with patch.dict("os.environ", {"MOSS_AI_WORKER_TOKEN": ""}):
            code, _, body = self.client.request("POST", "/api/v1/recognize-image",
                                                data=sample(), lookup_key=self.key)
        self.assertEqual(code, 503)
        self.assertEqual(body["error"], "ai_unavailable")

    def test_training_manifest_contains_only_approved(self):
        image = self.home / "scan_images"
        scans = list()
        with closing(sqlite3.connect(self.home / "moss_recognition.db")) as db:
            for state, ident in (("verified", "id-a"), ("pending", "id-b"), ("rejected", "id-c")):
                file = ident + ".jpg"
                (image / file).write_bytes(b"jpeg")
                db.execute("""INSERT INTO scans
                    (id,created_at,device_id,image_file,image_dhash,candidates_json,state,
                     verified_product_id,verified_name,verified_set,verified_number,reviewed_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ident, "2026", "MOSS", file, "abc", "[]", state,
                     "123" if state == "verified" else None,
                     "Card" if state == "verified" else None,
                     "dft" if state == "verified" else None,
                     "63" if state == "verified" else None,
                     "2026" if state == "verified" else None))
            db.commit()
        out = self.home / "training"
        counts = export_manifest(self.home, out)
        self.assertEqual(counts["approved"], 1)
        self.assertEqual(counts["train"] + counts["validation"], 1)
        records = (out / "train.jsonl").read_text() + (out / "validation.jsonl").read_text()
        self.assertIn("id-a", records)
        self.assertNotIn("id-b", records)
        self.assertNotIn("id-c", records)


if __name__ == "__main__":
    unittest.main()
