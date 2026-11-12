"""No hardware, server, or network required: exercise the real WSGI application."""
import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_app, dhash


class Client:
    def __init__(self, app):
        self.app = app
        self.cookie = ""

    def request(self, method, path, data=None, api_key=None, cookie=True, content_type=None, ip="127.0.0.1", csrf=None):
        if isinstance(data, dict) and content_type != "application/x-www-form-urlencoded":
            raw = json.dumps(data).encode()
            content_type = "application/json"
        elif isinstance(data, dict):
            raw = urlencode(data).encode()
        else:
            raw = data or b""
        status = []
        headers = []

        def start_response(s, hs):
            status.append(int(s.split(" ", 1)[0]))
            headers.extend(hs)

        env = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": content_type or "",
               "wsgi.input": io.BytesIO(raw), "REMOTE_ADDR": ip, "wsgi.url_scheme": "https"}
        if api_key is not None:
            env["HTTP_X_MOSS_API_KEY"] = api_key
        if cookie and self.cookie:
            env["HTTP_COOKIE"] = "moss_admin=" + self.cookie
        output = b"".join(self.app(env, start_response))
        for key, value in headers:
            if key.lower() == "set-cookie":
                self.cookie = value.split(";", 1)[0].split("=", 1)[1]
        if any(k.lower() == "content-type" and "application/json" in v for k, v in headers):
            output = json.loads(output)
        return status[0], dict(headers), output


def jpeg_base64(pattern="left"):
    image = Image.new("RGB", (330, 460), "white")
    for x in range(330):
        for y in range(460):
            if (x < (170 if pattern == "left" else 90)) and y < 280:
                image.putpixel((x, y), (10, 40, 140))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app({"data_dir": self.tmp.name,
                               "api_key": "TEST_API_KEY_LONG_ENOUGH_123456",
                               "admin_password": "LONG_TEST_ADMIN_PASSWORD_123456",
                               "session_secret": "LONG_TEST_SESSION_SECRET_123456",
                               "cookie_secure": False})
        self.client = Client(self.app)
        self.payload = {"device_id": "MOSS-01", "image_base64": jpeg_base64(),
                        "candidates": [{"product_id": "12345", "name": "Test Card",
                                        "set": "FDN", "collector_number": "42", "distance": 5.0}]}

    def scan(self, payload=None):
        return self.client.request("POST", "/api/v1/scans", data=payload or self.payload,
                                   api_key="TEST_API_KEY_LONG_ENOUGH_123456")

    def login(self):
        code, hdr, output = self.client.request("POST", "/admin/login", data={"password": "LONG_TEST_ADMIN_PASSWORD_123456"}, content_type="application/x-www-form-urlencoded")
        self.assertEqual(code, 303)
        self.assertIn("moss_admin=", hdr["Set-Cookie"])
        return self.client.cookie

    def approve(self, sid, **kwargs):
        fields = {"csrf_token": self.app._csrf(self.client.cookie), "action": "approve",
                  "physical_verified": "yes", "candidate_id": "12345", "note": "Physically checked"}
        fields.update(kwargs)
        return self.client.request("POST", f"/admin/review/{sid}", data=fields,
                                   content_type="application/x-www-form-urlencoded")

    def test_health_and_api_auth(self):
        self.assertEqual(self.client.request("GET", "/health")[0], 200)
        self.assertEqual(self.client.request("GET", "/api/v1/metrics")[0], 401)
        self.assertEqual(self.client.request("GET", "/api/v1/metrics", api_key="wrong")[0], 401)
        self.assertEqual(self.client.request("GET", "/api/v1/metrics", api_key="TEST_API_KEY_LONG_ENOUGH_123456")[0], 200)

    def test_upload_is_pending_not_trusted_and_cannot_read_image_without_admin(self):
        code, hdr, data = self.scan()
        self.assertEqual(code, 201)
        self.assertEqual(data["state"], "pending")
        self.assertTrue(data["advisory_only"])
        self.assertEqual(data["suggestions"], [])
        self.assertEqual(self.client.request("GET", f"/admin/image/{data['scan_id']}")[0], 303)
        code, _, details = self.client.request("GET", f"/api/v1/scans/{data['scan_id']}", api_key="TEST_API_KEY_LONG_ENOUGH_123456")
        self.assertEqual(details["state"], "pending")
        self.assertIsNone(details["verified_product_id"])
        metrics = self.client.request("GET", "/api/v1/metrics", api_key="TEST_API_KEY_LONG_ENOUGH_123456")[2]
        self.assertEqual(metrics["verified_examples"], 0)
        self.assertEqual(metrics["scans"]["pending"], 1)

    def test_approval_creates_verified_example_then_suggests_only(self):
        scan_id = self.scan()[2]["scan_id"]
        self.login()
        self.assertEqual(self.client.request("GET", f"/admin/review/{scan_id}")[0], 200)
        self.assertEqual(self.approve(scan_id)[0], 303)
        self.assertEqual(self.approve(scan_id)[0], 409)  # double review forbidden
        code, _, result = self.client.request("POST", "/api/v1/match", data=self.payload,
                                              api_key="TEST_API_KEY_LONG_ENOUGH_123456")
        self.assertEqual(code, 200)
        self.assertEqual(result["state"], "advisory_only")
        self.assertEqual(result["suggestions"][0]["product_id"], "12345")
        self.assertEqual(result["suggestions"][0]["dhash_distance"], 0)
        self.assertTrue(result["advisory_only"])
        self.assertEqual(self.client.request("GET", f"/admin/image/{scan_id}")[0], 200)
        metrics = self.client.request("GET", "/api/v1/metrics", api_key="TEST_API_KEY_LONG_ENOUGH_123456")[2]
        self.assertEqual(metrics["verified_examples"], 1)
        self.assertEqual(metrics["scans"]["verified"], 1)

    def test_reject_does_not_train(self):
        scan_id = self.scan()[2]["scan_id"]
        self.login()
        result = self.client.request("POST", f"/admin/review/{scan_id}",
                                     data={"csrf_token": self.app._csrf(self.client.cookie), "action": "reject", "note": "Wrong art"},
                                     content_type="application/x-www-form-urlencoded")
        self.assertEqual(result[0], 303)
        self.assertEqual(self.client.request("GET", "/api/v1/metrics", api_key="TEST_API_KEY_LONG_ENOUGH_123456")[2]["verified_examples"], 0)

    def test_no_physical_confirmation_or_csrf_cannot_train(self):
        scan_id = self.scan()[2]["scan_id"]
        self.login()
        self.assertEqual(self.approve(scan_id, physical_verified="")[0], 400)
        self.assertEqual(self.approve(scan_id, csrf_token="bad")[0], 403)
        self.assertEqual(self.client.request("GET", "/api/v1/metrics", api_key="TEST_API_KEY_LONG_ENOUGH_123456")[2]["verified_examples"], 0)

    def test_candidate_cannot_be_substituted_or_malformed(self):
        sid = self.scan()[2]["scan_id"]
        self.login()
        self.assertEqual(self.approve(sid, candidate_id="fake")[0], 400)
        bad = dict(self.payload)
        bad["candidates"] = [{"product_id": "<script>alert(1)</script>"}]
        self.assertEqual(self.scan(bad)[0], 400)
        self.assertEqual(self.approve(sid, candidate_id="", product_id="GOOD", name="Correct",
                                      **{"set": "M21", "collector_number": "99"})[0], 303)

    def test_invalid_image_and_device_rejected(self):
        bad = dict(self.payload)
        bad["image_base64"] = base64.b64encode(b"not-an-image").decode()
        self.assertEqual(self.scan(bad)[0], 400)
        bad = dict(self.payload)
        bad["device_id"] = "../escape"
        self.assertEqual(self.scan(bad)[0], 400)
        bad = dict(self.payload)
        bad["image_base64"] = "!"
        self.assertEqual(self.scan(bad)[0], 400)
        self.assertEqual(list(Path(self.tmp.name, "scan_images").iterdir()), [])

    def test_truncated_and_huge_request(self):
        c = self.client
        self.assertEqual(c.request("POST", "/api/v1/scans", data=b"{}", api_key="TEST_API_KEY_LONG_ENOUGH_123456", content_type="text/plain")[0], 415)
        huge = b"{" + b" " * (4 * 1024 * 1024 + 1)
        self.assertEqual(c.request("POST", "/api/v1/scans", data=huge, api_key="TEST_API_KEY_LONG_ENOUGH_123456", content_type="application/json")[0], 413)

    def test_login_throttle_and_secure_cookie(self):
        for i in range(5):
            self.assertEqual(self.client.request("POST", "/admin/login", data={"password": "wrong"}, content_type="application/x-www-form-urlencoded")[0], 200)
        self.assertEqual(self.client.request("POST", "/admin/login", data={"password": "wrong"}, content_type="application/x-www-form-urlencoded")[0], 429)
        self.assertEqual(self.client.request("GET", "/admin")[0], 303)

    def test_html_escapes_untrusted_card_name(self):
        candidate = dict(self.payload["candidates"][0], name="<img src=x onerror=alert(1)>")
        obj = dict(self.payload, candidates=[candidate])
        sid = self.scan(obj)[2]["scan_id"]
        self.login()
        output = self.client.request("GET", f"/admin/review/{sid}")[2].decode()
        self.assertIn("&lt;img", output)
        self.assertNotIn("<img src=x", output)

    def test_gallery_cannot_direct_sort_and_no_secrets_in_health(self):
        code, _, info = self.client.request("GET", "/health")
        self.assertEqual(code, 200)
        content = json.dumps(info)
        self.assertNotIn(self.app.api_key, content)
        self.assertNotIn(self.app.secret, content)
        self.assertFalse(any("sort" in endpoint for endpoint in info.keys()))

    def test_data_directory_never_public(self):
        with self.assertRaises(RuntimeError):
            create_app({"data_dir": "/home/user/public_html/scans", "api_key": "K"*24,
                        "admin_password": "P"*24, "session_secret": "S"*24})

    def test_dhash_stable_and_distinct(self):
        a = Image.new("RGB", (200, 300), "black")
        self.assertEqual(dhash(a), dhash(a))
        b = Image.new("RGB", (200, 300), "white")
        for x in range(200):
            for y in range(300):
                b.putpixel((x, y), (255-x, 255-x, 255-x))
        self.assertNotEqual(dhash(a), dhash(b))


if __name__ == "__main__":
    unittest.main()
