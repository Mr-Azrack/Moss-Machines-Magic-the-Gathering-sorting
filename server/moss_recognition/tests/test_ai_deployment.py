"""Read-only AI worker preflight and benchmark checks."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_preflight import get_host_facts
from benchmark_ai_worker import measure


class HealthReply:
    status = 200
    def __init__(self):
        self.body = io.BytesIO(b'{"status":"ready","engine":"paddleocr"}')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.body.close()

    def read(self, amount=-1):
        return self.body.read(amount)


class PreflightTests(unittest.TestCase):
    def test_readiness_requires_catalog_worker_and_token(self):
        with tempfile.TemporaryDirectory() as root:
            index = Path(root) / "catalog-index.json"
            index.write_text("{}")
            token = "unsafe-not-to-print-" + "X" * 48
            with patch.dict(os.environ, {"MOSS_AI_WORKER_TOKEN": token}),                  patch("ai_preflight.urlopen", return_value=HealthReply()):
                state = get_host_facts(root)
            self.assertTrue(state["ready_for_image_endpoint"])
            self.assertTrue(state["worker_running"])
            self.assertTrue(state["catalog_exists"])
            self.assertNotIn(token, json.dumps(state))

    def test_preflight_fails_closed_when_worker_down(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "catalog-index.json").write_text("{}")
            with patch.dict(os.environ, {"MOSS_AI_WORKER_TOKEN": "a" * 40}),                  patch("ai_preflight.urlopen", side_effect=OSError("worker offline")):
                state = get_host_facts(root)
            self.assertFalse(state["ready_for_image_endpoint"])
            self.assertFalse(state["worker_running"])

    def test_benchmark_latency_summary(self):
        with patch("benchmark_ai_worker.trial", side_effect=[125, 145, 235, 150]):
            result = measure([b"not-used"], 4, "fake-token")
        self.assertEqual(result["trials"], 4)
        self.assertEqual(result["max_ms"], 235)
        self.assertEqual(result["median_ms"], 147.5)


if __name__ == "__main__":
    unittest.main()
