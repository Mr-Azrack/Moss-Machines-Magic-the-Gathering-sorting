"""Exercise the fingerprint route and a live snapshot replacement."""

import json
import tempfile
import unittest
from pathlib import Path

from test_server import Client, create_app
from catalog_match import ALGORITHM


class CatalogTests(unittest.TestCase):
    key = 'catalog-test-api-key-111111'

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'catalog-index.json'
        self.app = create_app({'data_dir': folder.name, 'api_key': self.key,
                               'admin_password': 'catalog-test-password-111111',
                               'session_secret': 'catalog-test-session-111111'})
        self.client = Client(self.app)
        self.payload = {'game': 'magic', 'algorithm': ALGORITHM, 'rgb_phash': ['0' * 64] * 3}

    def publish(self, version, distance):
        base = {'name': 'Test Card', 'collector_number': '29', 'set': 'fdn'}
        snapshot = {'algorithm': ALGORITHM, 'version': version, 'sets': ['fdn'],
                    'cards': [{**base, 'product_id': 1, 'hashes': [['0' * 64] * 3]},
                              {**base, 'product_id': 2, 'hashes': [[distance * 64] * 3]}]}
        pending = self.path.with_suffix('.tmp')
        pending.write_text(json.dumps(snapshot))
        pending.replace(self.path)

    def request(self, **overrides):
        return self.client.request('POST', '/api/v1/recognize', {**self.payload, **overrides},
                                   api_key=self.key)

    def test_latest_snapshot_and_ambiguous_printings(self):
        self.publish('1', 'f')
        code, _, result = self.request()
        self.assertEqual(code, 200)
        self.assertTrue(result['advisory_only'])
        self.assertEqual(result['candidate']['product_id'], 1)
        self.publish('2', '0')
        code, _, result = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(result['catalog_version'], '2')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertIsNone(result['candidate'])

    def test_invalid_missing_and_unsupported(self):
        self.assertEqual(self.client.request('POST', '/api/v1/recognize', self.payload)[0], 401)
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.request(rgb_phash=['0'])[0], 400)
        self.assertEqual(self.request(set='../../../')[0], 400)
        self.publish('1', 'f')
        self.assertEqual(self.request(set='dft')[2]['status'], 'unsupported_scope')

    def test_printed_evidence_can_disambiguate_but_must_agree(self):
        self.publish('evidence', '0')
        raw = json.loads(self.path.read_bytes())
        raw['cards'][0]['finishes'] = ['foil', 'nonfoil']
        raw['cards'][1]['collector_number'] = '29a'
        self.path.write_text(json.dumps(raw))
        code, _, result = self.request(evidence={'set_abbreviation': 'FDN', 'symbol_set': 'fdn',
                                                'collector_number': '0029/271', 'foil_mark': 'star'})
        self.assertEqual(code, 200)
        self.assertEqual(result['candidate']['product_id'], 1)
        self.assertEqual(result['candidate']['finish'], 'foil')
        result = self.request(evidence={'set_abbreviation': 'fdn', 'symbol_set': 'dft'})[2]
        self.assertEqual(result['status'], 'set_evidence_conflict')
        self.assertEqual(self.request(evidence={'foil_mark': True})[0], 400)
