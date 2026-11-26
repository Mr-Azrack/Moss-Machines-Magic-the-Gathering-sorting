"""Synchronize all Scryfall MTG set names and set-symbol SVGs to private data."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

USER_AGENT = "MOSS Recognition Center/0.2 (cardsorter.thegamestationlw.com)"
SETS_URL = "https://api.scryfall.com/sets"
ICON_HOST = "svgs.scryfall.io"
CODE = re.compile(r"^[a-z0-9]{2,8}$")
MAX_LIST_BYTES = 8_000_000
MAX_ICON_BYTES = 250_000


def fetch(url, accept, max_bytes):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urlopen(request, timeout=20) as response:
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError("Scryfall response exceeded size limit")
    return body


def atomic_json(path, value):
    staging = path.with_suffix(".new")
    with staging.open("w", encoding="utf-8") as output:
        json.dump(value, output, separators=(",", ":"), ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(staging, path)


def sync_set_symbols(data_dir):
    """Fetch the full Scryfall set registry and cache each icon SVG privately."""
    data_dir = Path(data_dir).resolve()
    if not data_dir.is_dir() or "public_html" in data_dir.parts:
        raise ValueError("Set symbols must be stored in the existing private data directory")
    symbols_dir = data_dir / "set-symbols"
    symbols_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(symbols_dir, 0o700)
    registry_path = data_dir / "set-registry.json"
    try:
        previous = json.loads(registry_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        previous = {}
    prior = {row.get("code"): row for row in previous.get("sets", []) if isinstance(row, dict)}

    payload = json.loads(fetch(SETS_URL, "application/json", MAX_LIST_BYTES))
    records = payload.get("data")
    if payload.get("object") != "list" or not isinstance(records, list) or len(records) < 100:
        raise ValueError("Scryfall set list was incomplete")

    clean = []
    pending = []
    for row in records:
        code = str(row.get("code", "")).lower()
        name = str(row.get("name", "")).strip()
        icon_url = row.get("icon_svg_uri")
        if not CODE.fullmatch(code) or not name:
            continue
        icon_path = symbols_dir / f"{code}.svg"
        parts = urlsplit(icon_url or "")
        if parts.scheme != "https" or parts.hostname != ICON_HOST or not parts.path.endswith(".svg"):
            icon_url = None
        metadata = {
            "code": code, "name": name, "set_type": row.get("set_type"),
            "released_at": row.get("released_at"), "block": row.get("block"),
            "block_code": row.get("block_code"),
            "parent_set_code": row.get("parent_set_code"),
            "mtgo_code": row.get("mtgo_code"), "arena_code": row.get("arena_code"),
            "digital": bool(row.get("digital")), "icon_svg_uri": icon_url,
            "icon_file": f"set-symbols/{code}.svg" if icon_url else None,
        }
        clean.append(metadata)
        prior_row = prior.get(code, {})
        if icon_url and (prior_row.get("icon_svg_uri") != icon_url or not icon_path.is_file()):
            pending.append((code, icon_url, icon_path))

    failures = []
    def download(item):
        code, url, path = item
        time.sleep(0.11)
        body = fetch(url, "image/svg+xml,*/*;q=0.8", MAX_ICON_BYTES)
        prefix = body[:4096].lower()
        if b"<svg" not in prefix or b"<script" in prefix:
            raise ValueError("Invalid SVG set icon")
        temp = path.with_suffix(".svg.new")
        with temp.open("wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        return code

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(download, item): item[0] for item in pending}
        for future in as_completed(futures):
            code = futures[future]
            try:
                future.result()
            except Exception as exc:
                failures.append((code, type(exc).__name__))
    # A failure is tolerated only when a previously cached icon still exists.
    missing = [row["code"] for row in clean
               if row.get("icon_file") and not (data_dir / row["icon_file"]).is_file()]
    if missing:
        raise RuntimeError(f"Set symbol refresh incomplete; missing {len(missing)} SVG icons")
    registry = {
        "source": SETS_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "set_count": len(clean),
        "sets": clean,
    }
    atomic_json(registry_path, registry)
    print(f"Scryfall sets: {len(clean)} names; "
          f"{len(clean) - len(prior)} new/changed; {len(pending)} icons fetched; "
          f"{len(failures)} fetch failures; stored privately at {data_dir}", flush=True)
    return registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    sync_set_symbols(args.data_dir)


if __name__ == "__main__":
    main()
