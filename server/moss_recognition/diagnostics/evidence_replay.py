"""Evaluate printed Magic footer evidence on saved crops without sorting cards."""

import argparse
from collections import defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
LANGUAGES = 'EN|DE|ES|FR|IT|PT|JA|JP|KO|KR|RU|ZHS|ZHT|CS|CT|HE|LA|GRC|AR|SA|PH'
SET_LINE = re.compile(rf'^([A-Z][A-Z0-9]{{2,7}})(?:\s*([★☆*•·∙])\s*|\s+)({LANGUAGES})\b')
NUMBER = re.compile(r'^(?:[CURMLST]\s+)?([0-9]{1,5}[A-Z]?)\s*(?:/\s*[0-9]{1,5})?(?:\s*[CURMLST])?\s*$')
YEAR = re.compile(r'^(?:19|20)\d\d$')
MARKS = {'★': 'star', '☆': 'star', '*': 'star', '•': 'dot', '·': 'dot', '∙': 'dot'}


def footer_from_lines(lines):
    # Look for a plausible collector line and the adjacent SET / language line.
    normalized = [unicodedata.normalize('NFKC', raw).upper().strip().replace('\x07', '•')
                  for raw in lines]
    numbers = {}
    for index, line in enumerate(normalized):
        match = NUMBER.fullmatch(line)
        if match and not YEAR.fullmatch(match[1]):
            numbers[index] = match[1].lstrip('0').lower() or '0'

    # The modern footer prints the collector number above the set and language.
    # OCR can place part of the copyright year after that set row.
    pairs = []
    for index, line in enumerate(normalized):
        match = SET_LINE.match(line)
        if not match:
            continue
        number = numbers.get(index - 1) or numbers.get(index + 1)
        if number is not None:
            pairs.append((number, match[1], MARKS.get(match[2])))

    identities = {(number, code) for number, code, _ in pairs}
    if len(identities) == 1:
        number, code = identities.pop()
        evidence = {'collector_number': number, 'set_abbreviation': code}
        marks = {mark for _, _, mark in pairs if mark}
        if len(marks) == 1:
            evidence['foil_mark'] = marks.pop()
        return evidence
    if not pairs and len(set(numbers.values())) == 1:
        return {'collector_number': next(iter(numbers.values()))}
    return {}


def consensus(observations):
    confirmed = {}
    for field in ('set_abbreviation', 'collector_number', 'foil_mark'):
        readings = [row[field] for row in observations if field in row]
        if len(readings) >= 2 and len(set(readings)) == 1:
            confirmed[field] = readings[0]
    return confirmed


def footer_probes(image):
    """Rectify the footer scale for OCR from either a tight or padded portrait crop."""
    image = ImageOps.exif_transpose(image)
    if image.width > image.height:
        image = image.transpose(Image.Transpose.ROTATE_90)
    padded = image.size == (793, 1091)
    # Keep the full printed footer height. Exclude copyright text to the right.
    if padded:
        box = (12, 24 + round(1043 * .865), 24 + round(745 * .5), image.height - 7)
    else:
        box = (0, round(image.height * .865), round(image.width * .5), image.height)
    image = ImageOps.grayscale(image)
    footer = image.crop(box)
    enlarged = footer.resize((800, max(1, round(footer.height * 800 / footer.width))),
                            Image.Resampling.LANCZOS)
    grayscale = ImageOps.autocontrast(enlarged, cutoff=1)
    yield 'gray', ImageOps.expand(grayscale, border=12, fill=0)
    yield 'inverse', ImageOps.expand(ImageOps.invert(grayscale), border=12, fill=255)
    yield 'binary', ImageOps.expand(grayscale.point(lambda x: 255 if x >= 140 else 0), border=12, fill=0)
    if padded:
        # Restrict these probes to the two printed lines in a padded camera capture.
        for variant, right, top in (('tight_gray', .23, .915), ('tight_binary', .25, .92)):
            footer = image.crop((36, round(image.height * top), round(image.width * right),
                                 round(image.height * .978)))
            height = max(1, round(footer.height * 680 / footer.width))
            probe = ImageOps.autocontrast(footer.resize((680, height), Image.Resampling.LANCZOS),
                                          cutoff=1)
            if variant == 'tight_binary':
                probe = probe.point(lambda value: 255 if value >= 135 else 0)
            yield variant, ImageOps.expand(probe, border=16, fill=0)


def read_windows(paths, manifest):
    manifest.write_text(json.dumps([str(path) for path in paths]), encoding='utf-8')
    completed = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'RemoteSigned',
         '-File', str(HERE / 'read_footer_winrt.ps1'), '-Manifest', str(manifest)],
        check=True, capture_output=True, text=True, encoding='utf-8', timeout=90)
    return json.loads(completed.stdout.lstrip('\ufeff'))


def replay(samples, directory):
    groups = defaultdict(list)
    capture_details = {}
    with tempfile.TemporaryDirectory(prefix='moss-footer-') as temp:
        scratch = Path(temp)
        paths = {}
        for index, sample in enumerate(samples):
            image_path = Path(sample['image'])
            if not image_path.is_absolute():
                image_path = directory / image_path
            sidecar = image_path.with_suffix('.json')
            if sidecar.is_file():
                try:
                    capture_details[index] = json.loads(sidecar.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    capture_details[index] = {}
            with Image.open(image_path) as image:
                for variant, probe in footer_probes(image):
                    path = scratch / f'{index:03d}-{variant}.png'
                    probe.save(path)
                    paths[str(path)] = index, variant
        for row in read_windows([Path(path) for path in paths], scratch / 'batch.json'):
            index, variant = paths[row['path']]
            groups[index].append({'variant': variant, 'text': row['lines'],
                                  'fields': footer_from_lines(row['lines']), 'error': row['error']})
    report = []
    for index, sample in enumerate(samples):
        readings = groups[index]
        evidence = consensus([row['fields'] for row in readings])
        capture = capture_details.get(index, {})
        review = []
        if capture.get('footer_at_frame_edge'):
            review.append('footer_may_be_clipped')
        if 'set_abbreviation' not in evidence or 'collector_number' not in evidence:
            review.append('printing_footer_incomplete')
        record = {'label': sample['label'], 'image': sample['image'],
                  'reference_set': sample.get('reference_set'),
                  'reference_number': sample.get('reference_number'),
                  'reference_source': sample.get('reference_source'),
                  'evidence': evidence, 'capture': capture, 'review': review, 'ocr': readings}
        report.append(record)
        notes = f" (review: {', '.join(review)})" if review else ''
        print(f"{sample['label']}: {evidence or 'unreadable'}{notes}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--samples', type=Path)
    source.add_argument('--image', type=Path)
    source.add_argument('--captures', type=Path, help='Directory of saved camera evidence images')
    args = parser.parse_args()
    if args.samples:
        with args.samples.open(encoding='utf-8-sig', newline='') as manifest:
            samples = list(csv.DictReader(manifest))
        directory = args.samples.resolve().parent
    elif args.captures:
        directory = args.captures.resolve()
        samples = [{'image': str(path), 'label': path.name}
                   for path in sorted(directory.rglob('*_evidence.png'))[-20:]]
    else:
        directory = args.image.resolve().parent
        samples = [{'image': str(args.image.resolve()), 'label': args.image.name}]
    if not samples:
        parser.error('No saved card images')
    report = replay(samples, directory)
    output = directory / f'footer_replay_{datetime.now():%Y%m%d_%H%M%S_%f}.json'
    with output.open('x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(f'Report: {output}', flush=True)


if __name__ == '__main__':
    main()
