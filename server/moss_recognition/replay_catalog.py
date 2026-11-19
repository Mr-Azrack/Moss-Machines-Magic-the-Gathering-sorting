"""Compare the MOSS fingerprint endpoint against the local matcher on saved crops."""

import argparse
import csv
from datetime import datetime
from getpass import getpass
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENDPOINT = 'https://cardsorter.thegamestationlw.com/api/v1/recognize'
ALGORITHM = 'moss-rgb-phash-256'
FIELDS = ('label', 'group', 'reference_source', 'reference_id', 'local_status',
          'local_id', 'local_ms', 'fingerprint_ms', 'host_status', 'host_id',
          'host_top_id', 'host_roundtrip_ms', 'host_total_ms', 'catalog_version')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def remote_match(opener, hashes, key):
    request = Request(ENDPOINT, method='POST',
                      data=json.dumps({'game': 'magic', 'algorithm': ALGORITHM, 'rgb_phash': hashes}).encode(),
                      headers={'X-MOSS-API-Key': key, 'Content-Type': 'application/json',
                               'Accept': 'application/json', 'User-Agent': 'MOSS-Catalog-Replay/1.0'})
    with opener.open(request, timeout=6) as response:
        result = json.load(response)
    if result.get('advisory_only') is not True:
        raise ValueError('Unexpected recognition response')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sorter', type=Path, required=True, help='Current version folder')
    parser.add_argument('--samples', type=Path, required=True, help='Existing replay samples.csv')
    parser.add_argument('--local-only', action='store_true')
    args = parser.parse_args()
    sorter = args.sorter.resolve()
    manifest = args.samples.resolve()
    database = sorter / 'recognition_data' / 'unified_card_database.db'
    if not database.is_file() or not (sorter / 'optimized_scanner.py').is_file():
        parser.error('Expected installed sorter code and local reference database for comparison')
    sys.path.insert(0, str(sorter))
    from PIL import Image
    from identification_safety import choose_safe_match
    from optimized_scanner import OptimizedCardScanner

    key = None
    if not args.local_only:
        key = (os.environ.get('MOSS_API_KEY') or getpass('MOSS Recognition Center API key (hidden): ')).strip()
        if not key:
            parser.error('Enter the existing MOSS API key or run --local-only')
    opener = build_opener(NoRedirect())
    with manifest.open(encoding='utf-8-sig', newline='') as file:
        samples = list(csv.DictReader(file))
    if not samples:
        parser.error('No saved scans in the manifest')

    scanner = OptimizedCardScanner(db_path=str(database), enable_collection=False,
                                   prompt_for_details=False, serial_port=None, enable_mser_scoring=False)
    # Build the local game's cache before measuring per-card lookup.
    first_image = manifest.parent / samples[0]['image']
    scanner.scan_from_file(first_image, threshold=90, game_filter='Magic')
    report = manifest.parent / f'hosted_catalog_replay_{datetime.now():%Y%m%d_%H%M%S_%f}.csv'
    local_timings, host_timings = [], []
    host_available = True
    with report.open('x', encoding='utf-8-sig', newline='') as output:
        csv_writer = csv.DictWriter(output, fieldnames=FIELDS)
        csv_writer.writeheader()
        for sample in samples:
            image = manifest.parent / sample['image']
            started = perf_counter()
            matches, _ = scanner.scan_from_file(image, threshold=90, game_filter='Magic')
            chosen, reason = choose_safe_match(matches)
            local_ms = (perf_counter() - started) * 1000
            with Image.open(image) as crop:
                started = perf_counter()
                hashes = scanner.compute_phash(crop)
                fingerprint_ms = (perf_counter() - started) * 1000
            row = {name: sample[name] for name in ('label', 'group', 'reference_source', 'reference_id')}
            row.update(local_status=reason, local_id=(chosen or {}).get('product_id', ''),
                       local_ms=round(local_ms, 2), fingerprint_ms=round(fingerprint_ms, 2))
            local_timings.append(local_ms)
            if key and host_available:
                started = perf_counter()
                try:
                    reply = remote_match(opener, list(hashes), key)
                except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                    status = f'HTTP {exc.code}' if isinstance(exc, HTTPError) else type(exc).__name__
                    row.update(host_status=status)
                    host_available = False
                    print(f'Hosted trial stopped at {sample["label"]}: {status}')
                else:
                    roundtrip_ms = (perf_counter() - started) * 1000
                    total = fingerprint_ms + roundtrip_ms
                    host_timings.append(total)
                    row.update(host_status=reply['status'], host_id=(reply.get('candidate') or {}).get('product_id', ''),
                               host_top_id=(reply.get('suggestions') or [{}])[0].get('product_id', ''),
                               host_roundtrip_ms=round(roundtrip_ms, 2), host_total_ms=round(total, 2),
                               catalog_version=reply['catalog_version'])
            csv_writer.writerow(row)
            output.flush()
            print(f'{row["label"]}: local {reason} {local_ms:.0f}ms | '
                  f'host {row.get("host_status", "skipped")} {row.get("host_total_ms", "-")}ms')
    print(f'Local median {median(local_timings):.1f}ms ({len(local_timings)} samples)')
    if host_timings:
        print(f'Hosted median {median(host_timings):.1f}ms ({len(host_timings)} samples, includes fingerprint)')
    print(f'Report: {report}')
    return 0 if host_available else 1


if __name__ == '__main__':
    raise SystemExit(main())
