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


class Scale(unittest.TestCase):
    """k, M and bn mean what they say: a number 1000 times off is flagged."""

    def test_a_number_in_the_wrong_unit_is_flagged(self):
        self.assertEqual(N.untraced("Revenue missed plan by 39.2M.", [PNL]), ["39.2M"])  # rows in units: 39.2k
        self.assertEqual(N.untraced("Revenue was 1.03bn.", [PNL]), ["1.03bn"])
        self.assertEqual(N.untraced("Sales reached 60.5M.", [MONTHS]), ["60.5M"])  # subtitle "k": rows in thousands

    def test_the_unit_in_the_subtitle_sets_the_scale(self):
        millions = dict(MONTHS, subtitle="EUR millions, 2026 AC", rows=[["Sep", 39.2], ["Oct", 40.0]])
        self.assertEqual(N.untraced("Sep reached 39.2M.", [millions]), [])
        self.assertEqual(N.untraced("Sep reached 39.2k.", [millions]), ["39.2k"])
        self.assertEqual(N.untraced("Sep reached 39.2m, up 0.8m.", [millions]), [])  # lowercase m is million

    def test_an_unknown_suffix_is_flagged_whole(self):
        millions = dict(MONTHS, subtitle="EUR millions, 2026 AC", rows=[["Sep", 39.2], ["Oct", 40.0]])
        self.assertEqual(N.untraced("Sep reached 39.7m.", [millions]), ["39.7m"])  # never checked as 39
        self.assertEqual(N.untraced("Margin rose 2.1pp; cover 1.1x.", [PNL]), ["2.1pp", "1.1x"])

    def test_a_number_after_letters_is_checked_unless_it_is_a_label(self):
        self.assertEqual(N.untraced("Revenue was EUR1.07M planned.", [PNL]), [])
        self.assertEqual(N.untraced("Revenue missed by EUR41.0k.", [PNL]), ["41.0k"])
        self.assertEqual(N.untraced("Missed by _39.3k_.", [PNL]), ["39.3k"])
        self.assertEqual(N.untraced("Q4, H1, FY26, W45 and P10 are labels.", [PNL]), [])

    def test_only_a_bare_four_digit_number_is_a_year(self):
        self.assertEqual(N.untraced("Costs fell by 2,045 and -1,950.", [PNL]), ["2,045", "-1,950"])
        self.assertEqual(N.untraced("Since 2025.", [PNL]), [])

    def test_contribution_counts_the_unit(self):
        thousands = dict(MONTHS, columns=["Month", "AC", "PL"], measure={"Val": "AC", "PL": "PL"},
                         rows=[["Sep", 60.5, 50.0]])
        self.assertAlmostEqual(N.contribution(thousands), 10500.0)  # 10.5 in thousands


class Parts(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(N.parse("+113.1k and −3.7%")[0], N.Num("+113.1k", 113.1, 1, "k", False))
        self.assertTrue(N.parse("−3.7%")[0].percent)

    def test_contribution(self):
        self.assertAlmostEqual(N.contribution(PNL), 39212.23 + 98401.7, places=2)
        self.assertEqual(N.contribution({"columns": ["A", "AC"], "rows": [["x", 1]]}), 0.0)


if __name__ == "__main__":
    unittest.main()
