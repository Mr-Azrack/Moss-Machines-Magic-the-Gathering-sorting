"""Read-only deployment smoke check; doesn't upload scans or expose secrets."""
from __future__ import annotations
import json
import sys
from urllib.request import urlopen


def main():
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        print("Usage: python check_endpoint.py https://moss.example.com")
        return 2
    uri = sys.argv[1].rstrip("/") + "/health"
    try:
        with urlopen(uri, timeout=10) as response:
            obj = json.load(response)
        if obj.get("status") != "ok" or obj.get("service") != "moss-recognition":
            print("Unexpected health response:", obj)
            return 1
        print("OK: MOSS Recognition Server", obj.get("version", "?"))
        return 0
    except Exception as exc:
        print("Unable to reach recognition server:", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
