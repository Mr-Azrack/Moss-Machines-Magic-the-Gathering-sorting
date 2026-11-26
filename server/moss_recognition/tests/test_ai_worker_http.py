"""Exercise the private OCR worker over actual loopback HTTP, with a fake ML model."""
import base64
import io
import json
import sys
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_worker import make_handler


class FakeModel:
    def predict(self, image):
        return {"engine": "paddleocr", "evidence": {"set_abbreviation": "sos",
                "collector_number": "194", "foil_mark": "star"}, "review_reason": None}


def body():
    image = Image.new("RGB", (745, 1043), "white")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return json.dumps({"image_base64": base64.b64encode(out.getvalue()).decode("ascii")}).encode()


class WorkerProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), make_handler(FakeModel(), "X" * 40))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(3)

    def test_health_is_ready(self):
        with urlopen(self.base + "/health", timeout=3) as r:
            self.assertEqual(json.load(r)["status"], "ready")

    def test_rejects_missing_credential(self):
        req = Request(self.base + "/infer", data=body(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as exc:
            urlopen(req, timeout=3)
        self.assertEqual(exc.exception.code, 401)

    def test_recognizes_authenticated_image(self):
        req = Request(self.base + "/infer", data=body(),
                      headers={"Content-Type": "application/json", "X-MOSS-Worker-Token": "X" * 40},
                      method="POST")
        with urlopen(req, timeout=3) as r:
            result = json.load(r)
        self.assertEqual(result["evidence"]["set_abbreviation"], "sos")
        self.assertEqual(result["evidence"]["foil_mark"], "star")

    def test_malformed_image_rejected(self):
        req = Request(self.base + "/infer", data=b'{"image_base64":"fake"}',
                      headers={"Content-Type": "application/json", "X-MOSS-Worker-Token": "X" * 40},
                      method="POST")
        with self.assertRaises(HTTPError) as exc:
            urlopen(req, timeout=3)
        self.assertEqual(exc.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
