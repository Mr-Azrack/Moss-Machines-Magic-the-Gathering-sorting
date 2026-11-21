"""Regression checks for hosted finish-bin routing."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from optimized_scanner import OptimizedCardScanner


class HostedFinishRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scanner = OptimizedCardScanner.__new__(OptimizedCardScanner)

    def test_uses_observed_foil_even_when_catalog_allows_both(self):
        card = {
            "recognition_source": "hosted_ai",
            "actual_finish": "foil",
            "finishes": ["nonfoil", "foil"],
        }
        self.assertEqual(self.scanner.get_bin_number(card, "finish"), "Foil")

    def test_uses_observed_nonfoil_even_when_catalog_allows_both(self):
        card = {
            "recognition_source": "hosted_ai",
            "actual_finish": "nonfoil",
            "finishes": ["nonfoil", "foil"],
        }
        self.assertEqual(self.scanner.get_bin_number(card, "finish"), "Nonfoil")

    def test_unknown_hosted_finish_is_review_not_nonfoil(self):
        card = {
            "recognition_source": "hosted_ai",
            "actual_finish": None,
            "finishes": ["nonfoil", "foil"],
        }
        self.assertEqual(self.scanner.get_bin_number(card, "finish"), "Review")

    def test_legacy_recognition_behavior_is_preserved(self):
        card = {"finishes": ["nonfoil", "foil"]}
        self.assertEqual(self.scanner.get_bin_number(card, "finish"), "Both")


if __name__ == "__main__":
    unittest.main()
