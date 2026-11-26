"""Build the server's Magic pilot snapshot from Scryfall and TCGTracking."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
from io import BytesIO
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image

from catalog_match import ALGORITHM, fingerprint

USER_AGENT = 'MOSS Recognition Center/0.2 (cardsorter.thegamestationlw.com)'
SCRYFALL = 'https://api.scryfall.com/cards/search?'
TRACKING = 'https://openapi.tcgtracking.com/v1/1/sets'
IMAGE_HOSTS = {'cards.scryfall.io', 'cdn.tcgtracking.com'}
MAX_IMAGE_SIZE = 2_500_000


def fetch(url, accept, max_size=8_000_000):
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': accept})
    with urlopen(request, timeout=20) as response:
        body = response.read(max_size + 1)
    if len(body) > max_size:
        raise ValueError('Upstream response exceeded size budget')
    return body


def get_json(url):
    return json.loads(fetch(url, 'application/json;q=0.9,*/*;q=0.8'))


def scryfall_cards(set_code):
    query = urlencode({'q': f'set:{set_code} game:paper lang:en', 'unique': 'prints'})
    url = SCRYFALL + query
    expected_count = None
    cards = []
    while url:
        if urlsplit(url).hostname != 'api.scryfall.com':
            raise ValueError('Unexpected Scryfall pagination host')
        page = get_json(url)
        if expected_count is None:
            expected_count = page['total_cards']
        cards.extend(page['data'])
        if len(cards) > expected_count:
            raise ValueError('Scryfall pagination exceeded the expected row count')
        url = page['next_page'] if page['has_more'] else None
        time.sleep(0.12)
    if not cards or len(cards) != expected_count or len({c['id'] for c in cards}) != len(cards):
        raise ValueError(f'Incomplete Scryfall data for {set_code}')
    return cards


def printings(set_code, tracking_set):
    catalog = {}
    for card in scryfall_cards(set_code):
        images = [face.get('image_uris', {}) for face in card.get('card_faces', [])]
        if card.get('image_uris'):
            images = [card['image_uris']]
        urls = [image['normal'] for image in images if image.get('normal')]
        if card.get('image_status') in {'missing', 'placeholder'}:
            urls = []
        # A single face image can represent both etched and normal SKUs.
        # Preserve both candidate IDs so equal fingerprints trigger ambiguity.
        ids = dict.fromkeys(value for value in (card.get('tcgplayer_id'), card.get('tcgplayer_etched_id')) if value)
        for product_id in ids or [None]:
            item = {'product_id': product_id, 'scryfall_id': card['id'], 'name': card['name'],
                    'set': set_code, 'collector_number': card['collector_number'], 'images': urls}
            catalog[product_id or card['id']] = item

    if tracking_set:
        feed = get_json(f'{TRACKING}/{tracking_set}/cards')
        if feed['set_id'] != tracking_set or feed['set_abbr'].lower() != set_code:
            raise ValueError('TCGTracking returned the wrong set')
        products = feed['products']
        if not products or len(products) != feed['product_count'] or len({p['id'] for p in products}) != len(products):
            raise ValueError('Incomplete TCGTracking data')
        for product in products:
            product_id = product['id']
            scryfall_match = catalog.get(product.get('scryfall_id'))
            if scryfall_match:
                catalog.setdefault(product_id, {**scryfall_match, 'product_id': product_id})
            if product_id not in catalog:
                catalog[product_id] = {'product_id': product_id, 'scryfall_id': product.get('scryfall_id'),
                                       'name': product['name'], 'set': set_code,
                                       'collector_number': product.get('number'),
                                       'images': [product['image_url']] if product.get('image_url') else []}
    # A mapped record subsumes its same-face unmapped Scryfall record.
    mapped_faces = {row['scryfall_id'] for row in catalog.values() if row['product_id']}
    return [row for row in catalog.values() if row['product_id'] or row['scryfall_id'] not in mapped_faces]


def image_hashes(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in IMAGE_HOSTS:
        raise ValueError('Unexpected card image host')
    with Image.open(BytesIO(fetch(url, 'image/*', MAX_IMAGE_SIZE))) as image:
        if image.width * image.height > 4_000_000:
            raise ValueError('Reference image exceeded pixel budget')
        return fingerprint(image)


def refresh(path, requested_sets):
    previous = json.loads(path.read_bytes()) if path.exists() else {}
    old_cards = previous.get('cards', []) if previous.get('algorithm') == ALGORITHM else []
    hashes = {url: value for row in old_cards
              for url, value in zip(row['images'], row['hashes'])}
    old_count = {code: sum(row['set'] == code for row in old_cards) for code in requested_sets}
    cards = {row['product_id'] or row['scryfall_id']: row for row in old_cards}
    for code, tracking in requested_sets.items():
        new_cards = printings(code, tracking)
        if len(new_cards) < max(50, old_count[code] * 0.8):
            raise ValueError(f'Catalog size dropped unexpectedly for {code}')
        pending_urls = sorted({url for row in new_cards for url in row['images'] if url not in hashes})
        if pending_urls:
            print(f'{code}: computing {len(pending_urls)} new image fingerprints', flush=True)

        def load_image(url):
            try:
                return image_hashes(url)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                if isinstance(exc, HTTPError) and exc.code == 429:
                    raise
                return None

        with ThreadPoolExecutor(max_workers=4) as workers:
            for url, result in zip(pending_urls, workers.map(load_image, pending_urls)):
                if result is not None:
                    hashes[url] = result

        ready = 0
        for row in new_cards:
            available = [url for url in row['images'] if url in hashes]
            if not available:
                continue
            ready += 1
            row['images'] = available
            row['hashes'] = [hashes[url] for url in available]
            key = row['product_id'] or row['scryfall_id']
            if key not in cards or len(available) >= len(cards[key]['hashes']):
                cards[key] = row
        active = sum(row['set'] == code for row in cards.values())
        if ready < max(50, 0.9 * len(new_cards)):
            raise ValueError(f'Insufficient reference photos for {code}: {ready}/{len(new_cards)}')
        print(f'{code}: missing usable photos for {len(new_cards) - ready} source rows', flush=True)
        print(f'{code}: {active} searchable printings', flush=True)
    ordered = sorted(cards.values(), key=lambda row: (row['set'], str(row['product_id'] or row['scryfall_id'])))
    if ordered == old_cards:
        print('Index unchanged', flush=True)
        return
    version = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    snapshot = {'algorithm': ALGORITHM, 'sets': sorted({row['set'] for row in ordered}),
                'version': version, 'cards': ordered}
    pending = path.with_suffix('.new')
    with pending.open('w', encoding='utf-8') as file:
        json.dump(snapshot, file, separators=(',', ':'), ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(pending, path)
    print(f'Published {len(ordered)} printings at {version}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--sets', default='fdn:23556,dft',
                        help='Comma-separated Scryfall set codes, optionally followed by :TCGTrackingSetID')
    options = parser.parse_args()
    requested_sets = {}
    for item in options.sets.split(','):
        code, _, tracking = item.partition(':')
        if not re.fullmatch(r'[a-z0-9]{2,8}', code) or (tracking and not tracking.isdecimal()):
            parser.error('Invalid set selector')
        requested_sets[code] = int(tracking) if tracking else None
    directory = options.data_dir.resolve()
    if 'public_html' in directory.parts or not directory.is_dir():
        parser.error('Supply the existing private app data directory')
    os.umask(0o077)
    with (directory / 'catalog-refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Refresh already in progress', flush=True)
            return
        refresh(directory / 'catalog-index.json', requested_sets)


if __name__ == '__main__':
    main()
