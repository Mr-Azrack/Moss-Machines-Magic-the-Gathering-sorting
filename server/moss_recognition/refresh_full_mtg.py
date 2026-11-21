"""Publish a full paper Magic index using Scryfall bulk data and MOSS references."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import gzip
import json
import os
from pathlib import Path
import sqlite3
from threading import Event
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from catalog_match import ALGORITHM, parse_hashes
from refresh_catalog import TRACKING, USER_AGENT, get_json, image_hashes

SEED_URL = 'https://www.tcgtraders.app/recognition_data/phash_cards_1.db'
MANIFEST_URL = 'https://api.scryfall.com/bulk-data'
MIN_PAPER_PRINTINGS = 50_000
MIN_RECOGNIZABLE_PRINTINGS = 50_000
METADATA_SCHEMA_VERSION = 2


def json_read(path, fallback):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return fallback


def atomic_json(path, value):
    staging = path.with_suffix('.new')
    with staging.open('w', encoding='utf-8') as file:
        json.dump(value, file, separators=(',', ':'), ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(staging, path)


def download_seed(path):
    if path.is_file():
        return
    temporary = path.with_suffix('.part')
    with urlopen(Request(SEED_URL, headers={'User-Agent': USER_AGENT}), timeout=45) as response:
        with temporary.open('wb') as output:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise ValueError('MOSS fingerprint export exceeded its size limit')
                output.write(chunk)
    with temporary.open('rb') as stream:
        if stream.read(16) != b'SQLite format 3\0':
            raise ValueError('MOSS fingerprint export is not SQLite')
    os.replace(temporary, path)


def valid_seed_hashes(values):
    try:
        result = [value.hex() if isinstance(value, bytes) and len(value) == 32
                  else value.decode('ascii') if isinstance(value, bytes) else value
                  for value in values]
        parse_hashes(result)
        return result
    except (TypeError, ValueError, UnicodeError):
        return None


def seed_hashes(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('Invalid MOSS fingerprint database')
        rows = db.execute('SELECT product_id,r_phash,g_phash,b_phash FROM cards')
        return {int(product_id): h for product_id, *parts in rows if (h := valid_seed_hashes(parts))}


def metadata_rows(path):
    """Optional compact card-only export from the sorter's local cards_1 table."""
    if not path.is_file():
        return {}
    result = {}
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        for product_id, name, set_code, number in db.execute(
                'SELECT product_id,name,set_code,number FROM cards WHERE sealed=0'):
            if str(product_id).isdigit() and name and set_code:
                result[int(product_id)] = {
                    'product_id': int(product_id), 'scryfall_id': None, 'name': name,
                    'set': str(set_code).lower(), 'collector_number': str(number or ''),
                    'images': [], 'finishes': [], 'source': 'moss_local',
                }
    return result


def paper_rows(stream):
    cards = {}
    for line in stream:
        card = json.loads(line)
        if 'paper' not in card.get('games', []):
            continue
        image_refs = [face.get('image_uris', {}) for face in card.get('card_faces', [])]
        if card.get('image_uris'):
            image_refs = [card['image_uris']]
        images = [image['normal'] for image in image_refs if image.get('normal')]
        if card.get('image_status') in {'missing', 'placeholder'}:
            images = []
        regular = card.get('tcgplayer_id')
        etched = card.get('tcgplayer_etched_id')
        face = (card.get('card_faces') or [card])[0]
        routing = {'colors': face.get('colors', card.get('colors')),
                   'type_line': face.get('type_line', card.get('type_line')),
                   'mana_value': card.get('cmc'), 'rarity': card.get('rarity')}
        for product_id in dict.fromkeys([p for p in (regular, etched) if p] or [None]):
            finishes = card.get('finishes', [])
            if etched and regular != etched:
                finishes = ['etched'] if product_id == etched else [f for f in finishes if f != 'etched']
            item = {'product_id': product_id, 'scryfall_id': card['id'], 'name': card['name'],
                    'set': card['set'], 'collector_number': card['collector_number'],
                    'images': images, 'finishes': finishes, 'released_at': card.get('released_at', ''),
                    'source': 'scryfall', 'routing': routing}
            cards.setdefault(product_id or card['id'], item)
    return list(cards.values())


def catalog_metadata(cache):
    manifest = get_json(MANIFEST_URL)
    entry = next(row for row in manifest['data'] if row['type'] == 'default_cards')
    version = entry['updated_at']
    stored = json_read(cache, {})
    if (stored.get('version') == version and stored.get('schema') == METADATA_SCHEMA_VERSION
            and len(stored.get('cards', [])) >= MIN_PAPER_PRINTINGS):
        return stored
    url = entry.get('jsonl_download_uri')
    parts = urlsplit(url or '')
    if parts.scheme != 'https' or parts.hostname != 'data.scryfall.io':
        raise ValueError('Invalid Scryfall bulk URL')
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/gzip,*/*;q=0.8'})
    with urlopen(request, timeout=45) as source, gzip.GzipFile(fileobj=source) as stream:
        cards = paper_rows(stream)
    if len(cards) < max(MIN_PAPER_PRINTINGS, len(stored.get('cards', [])) * 0.95):
        raise ValueError('Scryfall bulk result is unexpectedly small')
    result = {'version': version, 'schema': METADATA_SCHEMA_VERSION, 'cards': cards}
    atomic_json(cache, result)
    print(f'Scryfall paper catalog: {len(cards):,} records ({version})', flush=True)
    return result


def tracking_foundations(cache):
    """Keep the existing TCGTracking supplement keyed by stable product IDs."""
    try:
        feed = get_json(f'{TRACKING}/23556/cards')
        products = feed['products']
        if (feed['set_id'] != 23556 or feed['set_abbr'].lower() != 'fdn'
                or len(products) != feed['product_count'] or len(products) < 100):
            raise ValueError('Unexpected Foundations supplement')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'TCGTracking supplement unavailable ({type(exc).__name__}), using last copy', flush=True)
        return json_read(cache, {})
    prior = json_read(cache, {})
    current = {str(p['id']): {'product_id': p['id'], 'scryfall_id': p.get('scryfall_id'),
                             'name': p['name'], 'set': 'fdn', 'collector_number': p.get('number'),
                             'images': [p['image_url']] if p.get('image_url') else [],
                             'finishes': [], 'source': 'tcgtracking'} for p in products}
    if current != prior:
        atomic_json(cache, current)
    return current


def fetch_new_hashes(urls, cache):
    limited = Event()

    def process(url):
        if limited.is_set():
            return None, False
        try:
            return image_hashes(url), True
        except HTTPError as exc:
            if exc.code == 429:
                limited.set()
                return None, False
        except (OSError, ValueError):
            pass
        return None, True

    failed = []
    with ThreadPoolExecutor(max_workers=4) as workers:
        for url, (hashes, attempted) in zip(urls, workers.map(process, urls)):
            if hashes:
                cache[url] = hashes
            elif attempted:
                failed.append(url)
    if limited.is_set():
        print('Image source rate limit reached. Remaining images will wait for the next run.', flush=True)
    return failed, limited.is_set()


def refresh(directory, image_budget, *, backfill_all=False, cached_source=False):
    inputs = directory / 'mtg-sources'
    inputs.mkdir(exist_ok=True)
    seed_path = inputs / 'phash_cards_1.db'
    download_seed(seed_path)
    seeded = seed_hashes(seed_path)
    print(f'MOSS fingerprint seed: {len(seeded):,} valid product IDs', flush=True)
    metadata_path = inputs / 'scryfall-paper.json'
    metadata = json_read(metadata_path, {}) if cached_source else catalog_metadata(metadata_path)
    if len(metadata.get('cards', [])) < MIN_PAPER_PRINTINGS or not metadata.get('version'):
        raise ValueError('No validated paper catalog source is available')
    index_path = directory / 'catalog-index.json'
    previous = json_read(index_path, {})
    prior_cards = previous.get('cards', []) if previous.get('algorithm') == ALGORITHM else []
    prior = {(row['product_id'] or row['scryfall_id']): row for row in prior_cards}
    local_metadata = metadata_rows(inputs / 'moss-mtg-metadata.db')
    cross_checks = sum(row['product_id'] in local_metadata for row in metadata['cards'])
    print(f'MOSS metadata rows: {len(local_metadata):,} ({cross_checks:,} Scryfall ID joins)', flush=True)
    tracking_path = inputs / 'tcgtracking-foundations.json'
    tracking = json_read(tracking_path, {}) if cached_source else tracking_foundations(tracking_path)
    tracking_rows = {int(key): row for key, row in tracking.items()}
    print(f'TCGTracking Foundations metadata: {len(tracking):,} product IDs', flush=True)
    cards = {**local_metadata, **tracking_rows,
             **{row['product_id'] or row['scryfall_id']: row for row in metadata['cards']}}

    refs = {url: h for row in prior_cards for url, h in zip(row['images'], row['hashes']) if url}
    seed_matches = sum(bool(row['product_id'] in seeded) for row in cards.values())
    print(f'TCGplayer ID joins to fingerprint seed: {seed_matches:,}', flush=True)
    urls = {}
    # Cover printings with no searchable face before filling supplemental faces.
    priorities = sorted(cards.items(), key=lambda item: (item[0] not in prior,
                        bool(item[1]['product_id']), item[1].get('released_at', '')), reverse=True)
    for _, row in priorities:
        product_id = row['product_id']
        for i, url in enumerate(row['images']):
            if url in refs or (i == 0 and product_id in seeded):
                continue
            urls[url] = None
    retry_path = inputs / 'image-retry-counts.json'
    retries = {url: count for url, count in json_read(retry_path, {}).items() if url in urls}
    # Process new URLs ahead of repeated failures so a bad image cannot block the catalog.
    pending = sorted(urls, key=lambda url: retries.get(url, 0))
    if not backfill_all:
        pending = pending[:image_budget]

    if not pending:
        publish(index_path, previous, prior, cards, refs, seeded, metadata, local_metadata)
    for start in range(0, len(pending), max(1, image_budget)):
        batch = pending[start:start + image_budget]
        print(f'Fetching {len(batch):,} reference images; {len(pending) - start:,} queued this run',
              flush=True)
        failed, rate_limited = fetch_new_hashes(batch, refs)
        for url in failed:
            retries[url] = retries.get(url, 0) + 1
        for url in batch:
            if url in refs:
                retries.pop(url, None)
        previous = publish(index_path, previous, prior, cards, refs, seeded, metadata, local_metadata)
        atomic_json(retry_path, retries)
        if rate_limited:
            break

    unresolved = sum(url not in refs for url in urls)
    print(f'Remaining reference image URLs: {unresolved:,}', flush=True)


def publish(index_path, previous, prior, cards, refs, seeded, metadata, local_metadata):
    result = dict(prior)
    seed_used = 0
    for key, row in cards.items():
        image_urls = [u for u in row['images'] if u in refs]
        hashes = [refs[u] for u in image_urls]
        product_id = row['product_id']
        if product_id in seeded and (not row['images'] or row['images'][0] not in refs):
            hashes.insert(0, seeded[product_id])
            image_urls.insert(0, None)
            seed_used += 1
        if hashes:
            result[key] = {**row, 'images': image_urls, 'hashes': hashes}
        elif key in prior:
            result[key] = {**row, 'images': prior[key]['images'], 'hashes': prior[key]['hashes']}
    ordered = sorted(result.values(), key=lambda row: (row['set'], str(row['product_id'] or row['scryfall_id'])))
    previous_cards = previous.get('cards', [])
    if len(ordered) < max(MIN_RECOGNIZABLE_PRINTINGS, len(previous_cards)):
        raise ValueError('Index coverage fell below the validated minimum')
    count = sum((row['product_id'] or row['scryfall_id']) in result for row in metadata['cards'])
    stats = {'paper_catalog_records': len(metadata['cards']), 'scryfall_searchable_records': count,
             'indexed_records': len(ordered), 'seeds_used': seed_used, 'moss_metadata_rows': len(local_metadata),
             'catalog_records_waiting_for_fingerprints': len(metadata['cards']) - count}
    sets = sorted({row['set'] for row in ordered})
    if ordered != previous_cards or previous.get('coverage') != stats:
        version = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        previous = {'algorithm': ALGORITHM, 'sets': sets, 'version': version,
                    'coverage': stats, 'scryfall_version': metadata['version'], 'cards': ordered}
        atomic_json(index_path, previous)
        print(f'Published {len(ordered):,} searchable printings across {len(sets):,} sets', flush=True)
    else:
        print('Index unchanged', flush=True)
    print(json.dumps(stats), flush=True)
    return previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--image-budget', type=int, default=500)
    parser.add_argument('--backfill-all', action='store_true',
                        help='Process every outstanding image in batches of --image-budget')
    parser.add_argument('--cached-source', action='store_true',
                        help='Backfill using the existing validated bulk metadata without refreshing it')
    options = parser.parse_args()
    directory = options.data_dir.resolve()
    if not directory.is_dir() or 'public_html' in directory.parts:
        parser.error('Supply the private MOSS data directory')
    if not 0 <= options.image_budget <= 3000:
        parser.error('Image budget must be between zero and 3,000')
    if options.backfill_all and not options.image_budget:
        parser.error('--backfill-all requires a positive image budget')
    os.umask(0o077)
    with (directory / 'catalog-refresh.lock').open('a') as lock:
        lock_mode = fcntl.LOCK_EX if options.backfill_all else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(lock, lock_mode)
        except BlockingIOError:
            print('Refresh already in progress', flush=True)
            return
        refresh(directory, options.image_budget, backfill_all=options.backfill_all,
                cached_source=options.cached_source)


if __name__ == '__main__':
    main()
