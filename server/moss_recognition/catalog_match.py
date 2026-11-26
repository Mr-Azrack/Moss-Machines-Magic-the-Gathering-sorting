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


SET_CODE = re.compile(r'[a-z0-9]{2,8}', re.I)
COLLECTOR = re.compile(r'([a-z]{0,3})([0-9]{1,6})([a-z★†]{0,3})(?:/[0-9]{1,6})?', re.I)


def collector_key(value):
    match = COLLECTOR.fullmatch(value.strip()) if isinstance(value, str) else None
    return f'{match[1].lower()}{int(match[2])}{match[3].lower()}' if match else None


def parse_evidence(value):
    if not isinstance(value, dict) or set(value) - {'set_abbreviation', 'symbol_set', 'collector_number', 'foil_mark'}:
        raise ValueError('Invalid card evidence fields')
    clean = {}
    for field in ('set_abbreviation', 'symbol_set'):
        code = value.get(field)
        if code is not None:
            if not isinstance(code, str) or not SET_CODE.fullmatch(code):
                raise ValueError('Expected an MTG set code')
            clean[field] = code.lower()
    if value.get('collector_number') is not None:
        normalized = collector_key(value['collector_number'])
        if normalized is None:
            raise ValueError('Expected a collector number')
        clean['collector_number'] = normalized
    mark = value.get('foil_mark', 'unknown')
    if mark not in ('unknown', 'star', 'dot'):
        raise ValueError('Expected unknown, star, or dot as the foil mark')
    if mark != 'unknown':
        clean['foil_mark'] = mark
    return clean


def finish_from_mark(allowed, mark):
    if mark == 'dot':
        return 'nonfoil' if 'nonfoil' in allowed else None
    if mark == 'star':
        options = [finish for finish in allowed if finish in ('foil', 'etched')]
        return options[0] if len(options) == 1 else None
    return None


class CatalogIndex:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        self.stamp = None
        self.snapshot = None
        self.set_aliases = {}

    def load(self):
        with self.lock:
            info = self.path.stat()
            registry_path = self.path.parent / 'set-registry.json'
            try:
                registry_info = registry_path.stat()
                registry_stamp = (registry_info.st_ino, registry_info.st_mtime_ns, registry_info.st_size)
            except FileNotFoundError:
                registry_stamp = None
            stamp = (info.st_ino, info.st_mtime_ns, info.st_size, registry_stamp)
            if stamp != self.stamp:
                with self.path.open(encoding='utf-8') as file:
                    data = json.load(file)
                if data['algorithm'] != ALGORITHM or not data['cards']:
                    raise ValueError('Invalid recognition snapshot')
                set_names = {}
                aliases = {}
                try:
                    registry = json.loads(registry_path.read_text(encoding='utf-8'))
                except (FileNotFoundError, json.JSONDecodeError):
                    registry = {}
                for set_row in registry.get('sets', []):
                    code = str(set_row.get('code', '')).lower()
                    name = set_row.get('name')
                    if code and name:
                        set_names[code] = name
                        aliases[code] = code
                    for field in ('mtgo_code', 'arena_code', 'parent_set_code'):
                        alias = set_row.get(field)
                        if isinstance(alias, str) and alias:
                            aliases.setdefault(alias.lower(), code)
                self.set_aliases = aliases
                rows = []
                for card in data['cards']:
                    identity = {key: card.get(key) for key in
                                ('product_id', 'scryfall_id', 'name', 'set', 'collector_number')}
                    identity['set_name'] = set_names.get(str(identity.get('set', '')).lower())
                    identity['finishes'] = card.get('finishes', [])
                    if 'routing' in card:
                        identity['routing'] = card['routing']
                    rows.append((identity, collector_key(identity['collector_number']),
                                 tuple(parse_hashes(h) for h in card['hashes'])))
                self.snapshot = data['version'], tuple(data['sets']), rows, data.get('coverage')
                self.stamp = stamp
            return self.snapshot

    def match(self, hashes, set_code=None, evidence=None):
        version, sets, rows, coverage = self.load()
        evidence = evidence or {}
        stated_sets = {self.set_aliases.get(str(value).lower(), str(value).lower()) for value in
                       (set_code, evidence.get('set_abbreviation'), evidence.get('symbol_set')) if value}
        collector = evidence.get('collector_number')
        mark = evidence.get('foil_mark')
        stated_set = next(iter(stated_sets)) if len(stated_sets) == 1 else None
        result = {'status': 'no_match', 'candidate': None, 'suggestions': [],
                  'catalog_version': version, 'catalog_set_count': len(sets), 'advisory_only': True,
                  'evidence': evidence}
        if len(sets) <= 32:
            result['catalog_sets'] = list(sets)
        if coverage is not None:
            result['catalog_coverage'] = coverage
        if len(stated_sets) > 1:
            return {**result, 'status': 'set_evidence_conflict'}
        if stated_set and stated_set not in sets:
            return {**result, 'status': 'unsupported_scope'}
        both_footer_fields = bool(evidence.get('set_abbreviation') and collector)

        eligible = []
        for card, number, references in rows:
            if (stated_set and card['set'] != stated_set) or (collector and number != collector):
                continue
            # Some imported IDs have no declared finishes; do not rule them out.
            if mark and card['finishes'] and mark == 'dot' and 'nonfoil' not in card['finishes']:
                continue
            if mark == 'star' and card['finishes'] and not ({'foil', 'etched'} & set(card['finishes'])):
                continue
            eligible.append((card, references))

        ranked = ((min(sum((a ^ b).bit_count() for a, b in zip(hashes, ref)) for ref in refs), card)
                  for card, refs in eligible if refs)
        top = heapq.nsmallest(3, ranked, key=lambda entry: entry[0])
        suggestions = [{**card, 'distance': round(distance / 3, 2),
                        'finish': finish_from_mark(card['finishes'], mark)}
                       for distance, card in top]
        result['suggestions'] = suggestions
        if not top or top[0][0] > (132 if both_footer_fields else 96):
            return result
        if len(top) > 1 and top[1][0] - top[0][0] < 15:
            result['status'] = 'ambiguous'
        elif top[0][1]['product_id'] is None:
            result['status'] = 'unmapped_printing'
        else:
            result.update(status='candidate', candidate=suggestions[0])
        return result
