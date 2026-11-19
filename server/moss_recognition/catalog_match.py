"""Read-only image fingerprint matching over an atomically published catalog."""

import heapq
import json
import math
import re
from statistics import median
from threading import Lock

from PIL import Image

ALGORITHM = 'moss-rgb-phash-256'
HASH = re.compile(r'[0-9a-f]{64}', re.I)
BASIS = tuple(tuple(math.cos(math.pi * k * (2 * n + 1) / 128)
                    for n in range(64)) for k in range(16))


def fingerprint(image):
    """Match the sorter's letterbox and ImageHash pHash preprocessing."""
    image = image.convert('RGB')
    if image.width > image.height:
        image = image.transpose(Image.Transpose.ROTATE_90)
    scale = 1024 / max(image.size)
    width, height = (max(1, round(value * scale)) for value in image.size)
    square = Image.new('RGB', (1024, 1024), 'white')
    square.paste(image.resize((width, height), Image.Resampling.LANCZOS),
                 ((1024 - width) // 2, (1024 - height) // 2))
    result = []
    for channel in square.split():
        pixels = list(channel.resize((64, 64), Image.Resampling.LANCZOS).tobytes())
        horizontal = [[sum(p * c for p, c in zip(pixels[y:y + 64], basis))
                       for basis in BASIS] for y in range(0, 4096, 64)]
        columns = list(zip(*horizontal))
        coeffs = [sum(p * c for p, c in zip(column, basis))
                  for basis in BASIS for column in columns]
        cutoff = median(coeffs)
        result.append(format(int(''.join('1' if x > cutoff else '0' for x in coeffs), 2), '064x'))
    return result


def parse_hashes(hashes):
    if (not isinstance(hashes, list) or len(hashes) != 3
            or not all(isinstance(part, str) and HASH.fullmatch(part) for part in hashes)):
        raise ValueError('Expected three 64-character hexadecimal RGB fingerprints')
    return tuple(int(part, 16) for part in hashes)


class CatalogIndex:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        self.stamp = None
        self.snapshot = None

    def load(self):
        with self.lock:
            info = self.path.stat()
            stamp = (info.st_ino, info.st_mtime_ns, info.st_size)
            if stamp != self.stamp:
                data = json.loads(self.path.read_bytes())
                if data['algorithm'] != ALGORITHM or not data['cards']:
                    raise ValueError('Invalid recognition snapshot')
                rows = []
                for card in data['cards']:
                    identity = {key: card.get(key) for key in
                                ('product_id', 'scryfall_id', 'name', 'set', 'collector_number')}
                    for hashes in card['hashes']:
                        rows.append((identity, parse_hashes(hashes)))
                self.snapshot = data['version'], tuple(data['sets']), rows
                self.stamp = stamp
            return self.snapshot

    def match(self, hashes, set_code=None):
        version, sets, rows = self.load()
        best = {}
        for card, reference in rows:
            if set_code and card['set'] != set_code:
                continue
            identity = card['product_id'] or card['scryfall_id']
            distance = sum((a ^ b).bit_count() for a, b in zip(hashes, reference))
            if identity not in best or distance < best[identity][0]:
                best[identity] = (distance, card)
        top = heapq.nsmallest(3, best.values(), key=lambda entry: entry[0])
        suggestions = [{**card, 'distance': round(distance / 3, 2)} for distance, card in top]
        candidate = None
        status = 'no_match'
        if set_code and set_code not in sets:
            status = 'unsupported_scope'
        elif top and top[0][0] <= 96:
            if len(top) > 1 and top[1][0] - top[0][0] < 15:
                status = 'ambiguous'
            elif top[0][1]['product_id'] is None:
                status = 'unmapped_printing'
            else:
                status = 'candidate'
                candidate = suggestions[0]
        return {'status': status, 'candidate': candidate, 'suggestions': suggestions,
                'catalog_version': version, 'catalog_sets': list(sets), 'advisory_only': True}
