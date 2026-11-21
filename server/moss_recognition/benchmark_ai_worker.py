"""Benchmark a RUNNING private MOSS OCR worker without sorter hardware.

Uses a synthetic image for latency only unless --images specifies real physical
card crops. Never treats speed or fake-image OCR as an accuracy measurement.
"""
import argparse
import base64
import io
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.request import Request, urlopen

from PIL import Image


def trial(image_bytes, token):
    payload = json.dumps({"image_base64": base64.b64encode(image_bytes).decode("ascii")}).encode()
    request = Request("http://127.0.0.1:8765/infer", data=payload, method="POST",
                      headers={"Content-Type": "application/json", "X-MOSS-Worker-Token": token})
    start = perf_counter()
    with urlopen(request, timeout=60) as response:
        data = json.load(response)
        if data.get("engine") != "paddleocr":
            raise RuntimeError("OCR worker produced an invalid response")
    return round((perf_counter() - start) * 1000)


def measure(images, count, token):
    samples = []
    for index in range(count):
        samples.append(trial(images[index % len(images)], token))
    ordered = sorted(samples)
    n = len(ordered)
    return {"trials": n, "median_ms": median(ordered), "p95_ms": ordered[max(0, int((n - 1) * 0.95))],
            "max_ms": ordered[-1], "min_ms": ordered[0],
            "meaning": "Latency only; not model-accuracy validation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", nargs="*", type=Path, default=[],
                        help="Real saved card crops, JPEG or PNG")
    parser.add_argument("--iterations", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 50:
        parser.error("Expected 1-50 iterations")
    token = os.environ.get("MOSS_AI_WORKER_TOKEN", "")
    if len(token) < 32:
        parser.error("Configure MOSS_AI_WORKER_TOKEN in the shell")
    if args.images:
        files = [path.read_bytes() for path in args.images]
        for file in files:
            with Image.open(io.BytesIO(file)) as image:
                image.verify()
    else:
        canvas = Image.new("RGB", (745, 1043), (235, 235, 235))
        out = io.BytesIO()
        canvas.save(out, format="JPEG", quality=90)
        files = [out.getvalue()]
    print(json.dumps(measure(files, args.iterations, token), indent=2))


if __name__ == "__main__":
    main()
