"""Regression checks for the automatic sorting safety gate (no camera needed)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identification_safety import choose_safe_match


def c(pid, name, set_code, distance, conf=95):
    return {'product_id': pid, 'name': name, 'set': set_code,
            'distance': distance, 'phash_confidence': conf}


class SafetyTests(unittest.TestCase):
    def test_unique_high_confidence(self):
        a = c('1', 'Island', 'LEA', 5)
        self.assertEqual(choose_safe_match([a]), (a, 'verified'))

    def test_near_tie_rejected(self):
        a, b = c('1', 'Island', 'LEA', 5), c('2', 'Mountain', 'LEA', 6)
        self.assertEqual(choose_safe_match([a, b])[1], 'ambiguous_phash_candidates')

    def test_low_phash_rejected_even_if_display_confidence_high(self):
        a = c('1', 'Island', 'LEA', 5, 82)
        a['confidence'] = 99
        self.assertEqual(choose_safe_match([a])[1], 'low_phash_confidence_or_missing_identity')

    def test_symbol_selects_actual_printing_not_mutated_id(self):
        a, b = c('old', 'Island', 'LEA', 5), c('new', 'Island', 'M21', 6)
        result, reason = choose_safe_match([a, b], symbol_set='M21', symbol_confidence=.92,
                                            symbol_scores={'LEA': .56, 'M21': .92})
        self.assertEqual((result['product_id'], result['set'], reason), ('new', 'M21', 'verified'))

    def test_symbol_cannot_invent_printing(self):
        a = c('old', 'Island', 'LEA', 5)
        self.assertEqual(choose_safe_match([a], symbol_set='M21', symbol_confidence=.92,
                                           symbol_scores={'LEA': .56, 'M21': .92})[1],
                         'symbol_printing_not_in_candidates')

    def test_symbol_does_not_choose_wrong_card_name(self):
        a, b = c('old', 'Island', 'LEA', 5), c('other', 'Mountain', 'M21', 6)
        self.assertEqual(choose_safe_match([a, b], symbol_set='LEA', symbol_confidence=.92,
                                           symbol_scores={'LEA': .92, 'M21': .55})[1],
                         'ambiguous_phash_candidates')

    def test_uncertain_symbol_does_not_override_near_tie(self):
        a, b = c('old', 'Island', 'LEA', 5), c('new', 'Island', 'M21', 6)
        self.assertEqual(choose_safe_match([a, b], symbol_set='M21', symbol_confidence=.7,
                                           symbol_scores={'LEA': .68, 'M21': .7})[1],
                         'ambiguous_phash_candidates')

    def test_same_set_ambiguous_variant_rejected(self):
        a, b = c('old', 'Island', 'M21', 5), c('new', 'Island', 'M21', 6)
        self.assertEqual(choose_safe_match([a, b], symbol_set='M21', symbol_confidence=.9,
                                           symbol_scores={'LEA': .4, 'M21': .9})[1],
                         'ambiguous_phash_candidates')

    def test_decisive_symbol_not_distant_phash(self):
        a, b = c('old', 'Island', 'LEA', 2), c('new', 'Island', 'M21', 30)
        self.assertEqual(choose_safe_match([a, b], symbol_set='M21', symbol_confidence=.95,
                                           symbol_scores={'LEA': .5, 'M21': .95})[1],
                         'symbol_phash_disagreement')

    def test_missing_identity(self):
        a = {'product_id': '1', 'name': 'Unknown', 'set': 'LEA', 'distance': 1, 'confidence': 99}
        self.assertEqual(choose_safe_match([a])[1], 'missing_product_identity')


if __name__ == '__main__':
    unittest.main()
