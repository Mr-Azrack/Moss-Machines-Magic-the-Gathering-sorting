"""Read-only MOSS AI deployment preflight. Never prints keys or passwords."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys
from urllib.request import urlopen


def get_host_facts(data_dir):
    facts = {"python": platform.python_version(), "platform": sys.platform,
             "cpu_count": os.cpu_count(), "pillow_installed": importlib.util.find_spec("PIL") is not None,
             "paddleocr_installed": importlib.util.find_spec("paddleocr") is not None,
             "paddlepaddle_installed": importlib.util.find_spec("paddle") is not None,
             "worker_token_configured": len(os.getenv("MOSS_AI_WORKER_TOKEN", "")) >= 32}
    if sys.platform.startswith("linux"):
        try:
            mem = (Path("/proc/meminfo")).read_text()
            line = next(line for line in mem.splitlines() if line.startswith("MemAvailable:"))
            facts["mem_available_mb"] = round(int(line.split()[1]) / 1024)
        except (OSError, ValueError, StopIteration):
            pass
    if data_dir:
        root = Path(data_dir).resolve()
        index = root / "catalog-index.json"
        facts["catalog_exists"] = index.is_file()
        facts["catalog_size_mb"] = round(index.stat().st_size / (1024 ** 2), 1) if index.is_file() else None
        facts["data_dir_private"] = "public_html" not in root.parts
        try:
            facts["disk_free_gb"] = round(shutil.disk_usage(root).free / (1024 ** 3), 2)
        except OSError:
            facts["disk_free_gb"] = None
    try:
        with urlopen("http://127.0.0.1:8765/health", timeout=3) as response:
            body = json.loads(response.read(512))
            facts["worker_running"] = response.status == 200 and body.get("status") == "ready"
            facts["worker_engine"] = body.get("engine")
    except (OSError, ValueError, KeyError):
        facts["worker_running"] = False
    facts["ready_for_image_endpoint"] = (
        facts["pillow_installed"] and facts["worker_token_configured"] and
        facts["worker_running"] and facts.get("catalog_exists", False) and
        facts.get("data_dir_private", False)
    )
    return facts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--strict", action="store_true",
                        help="Exit nonzero until the worker, catalog, and auth are ready")
    args = parser.parse_args()
    facts = get_host_facts(args.data_dir)
    print(json.dumps(facts, indent=2, sort_keys=True))
    if args.strict and not facts["ready_for_image_endpoint"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
