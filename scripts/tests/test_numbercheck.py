"""Tests for numbercheck.py. Run: python3 -I -m unittest discover <plugin>/scripts/tests"""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numbercheck as N  # noqa: E402

PNL = {"chart_type": "variance", "columns": ["P&L line", "AC", "PL", "PY"],
       "measure": {"name": "P&L", "Val": "AC", "PL": "PL", "PY": "PY"},
       "rows": [["Revenue", 1026887.77, 1066100, 1194673.33], ["Cost of Goods Sold", -443898.3, -542300, -539313.86]]}
MONTHS = {"chart_type": "bar", "time_axis": True, "columns": ["Month", "AC"], "subtitle": "k, 2026 AC",
          "rows": [["Sep", 60.5], ["Oct", 71.5]]}


class Untraced(unittest.TestCase):
    def test_variances_and_percents_pass(self):
        self.assertEqual(N.untraced("Revenue missed plan by **39.2k** (−3.7%).", [PNL]), [])
        self.assertEqual(N.untraced("Revenue missed plan by 39.2k (-3.7%), at 1.07M planned.", [PNL]), [])

    def test_a_number_the_rows_do_not_hold_is_flagged(self):
        self.assertEqual(N.untraced("Revenue missed plan by 39.3k.", [PNL]), ["39.3k"])

    def test_rows_already_in_thousands(self):
        self.assertEqual(N.untraced("Sales ran between 60.5k and 71.5k, up 11.0k (+18.2%).", [MONTHS]), [])

    def test_european_formats_are_flagged_not_misread(self):
        self.assertNotEqual(N.untraced("Revenue was 1.026.887,77.", [PNL]), [])
        self.assertNotEqual(N.untraced("EBITDA 359,7k.", [PNL]), [])

    def test_years_labels_and_kept_numbers_are_skipped(self):
        self.assertEqual(N.untraced("Oct 2026 vs Q4 FY26: 39.2k.", [PNL]), [])
        self.assertEqual(N.untraced("Headcount 42.", [PNL], kept=["42"]), [])
        self.assertEqual(N.untraced("Headcount 42.", [PNL]), ["42"])


class Parts(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(N.parse("+113.1k and −3.7%")[0], N.Num("+113.1k", 113.1, 1, "k", False))
        self.assertTrue(N.parse("−3.7%")[0].percent)

    def test_contribution(self):
        self.assertAlmostEqual(N.contribution(PNL), 39212.23 + 98401.7, places=2)
        self.assertEqual(N.contribution({"columns": ["A", "AC"], "rows": [["x", 1]]}), 0.0)


if __name__ == "__main__":
    unittest.main()
