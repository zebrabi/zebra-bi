#!/usr/bin/env python3
"""numbercheck.py: trace every number in a report's text to the rows of its charts.

A number passes when one of the charts' rows supports it: a value, a total, the smallest or largest value, a
variance to PL, PY, PM or FC, a variance in percent, a share of a total, or a move between two periods. Signs live
in the words ("39.2k under plan"), so absolute values are compared, within the rounding the text shows. The unit
at the start of a chart's subtitle ("k, 2026 AC", "EUR millions") says what a row is worth, so "60.5k" passes
against a row of 60.5 in thousands and "60.5M" does not; without one, rows of 10,000 or more are taken as units,
and smaller rows may be units, thousands or millions. Numbers are read with English separators and the units k,
M (or m, mn, mln) and bn; a European "359,7k" or "1.026.887,77", or a unit the check does not know ("2.1pp",
"1.1x"), is reported whole as untraced, never read as another number. Standard library only.
"""
import re
from collections import namedtuple

Num = namedtuple("Num", "raw value decimals suffix percent")

REFS = ("PY", "PM", "PL", "FC")
MULT = {"": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "m": 1e6, "mn": 1e6, "mln": 1e6, "bn": 1e9}
# A run of digits with any separators, its sign, its unit and any letters glued after it. A unit followed by more
# letters ("39.7mo", "2.1pp", "1.1x") is not a unit this check knows, so the whole run is reported, never a part.
TOKEN = re.compile(r"(?<![\d.,])([+\-−]?)(\d[\d.,]*\d|\d)(\s?%|bn|mln|mn|[kKMm])?([^\W\d_]*)")
ENGLISH = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# The unit a chart's rows are in, from the start of its subtitle ("k, 2026 AC", "EUR millions, Q1 2026").
UNITS = ((re.compile(r"\b(k|thousands?|TEUR|TUSD|kEUR|kUSD|tsd)\b", re.I), 1e3),
         (re.compile(r"\b(M|mio|mn|mln|millions?|MEUR|MUSD)\b"), 1e6),
         (re.compile(r"\b(bn|billions?)\b", re.I), 1e9))


def tokens(text):
    """(raw, Num or None) for every number-like run in text; None when it is not an English number with a known
    unit. A whole number right after a letter, with no unit (Q4, H1, FY26, W45), is a label and is left out."""
    text = str(text or "")
    out = []
    for m in TOKEN.finditer(text):
        sign, digits, unit, tail = m.group(1), m.group(2), (m.group(3) or "").strip(), m.group(4)
        raw = m.group(0)
        after_letter = m.start() > 0 and text[m.start() - 1].isalpha()
        if after_letter and not sign and not unit and not tail and "." not in digits and "," not in digits:
            continue
        if tail or not ENGLISH.fullmatch(digits):
            out.append((raw, None))
            continue
        value = float(digits.replace(",", ""))
        value = -value if sign in ("-", "−") else value
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        percent = unit == "%"
        out.append((raw, Num(raw, value, decimals, "" if percent else unit, percent)))
    return out


def parse(text):
    """The numbers in text, as written in English: [Num]."""
    return [n for _, n in tokens(text) if n is not None]


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _scenarios(args):
    """(actual column index or None, [reference column indexes]) from measure, else from the column names."""
    cols = list(args.get("columns") or [])
    measure = args.get("measure") if isinstance(args.get("measure"), dict) else {}
    actual = measure.get("Val") or "AC"
    if measure:
        refs = [measure[k] for k in measure if k in REFS]
    else:
        refs = [c for c in cols if c in REFS]
    index = {c: i for i, c in enumerate(cols)}
    return index.get(actual), [index[r] for r in refs if r in index]


def unit(args):
    """The multiplier the chart's rows are in, from the first part of its subtitle; None when it does not say."""
    head = str(args.get("subtitle") or "").split(",")[0]
    for pattern, mult in UNITS:
        if pattern.search(head):
            return mult
    return None


def _column(rows, i):
    return [r[i] if i < len(r) and _number(r[i]) else None for r in rows]


def candidates(args):
    """(values, percents) the chart's rows support."""
    rows = [r for r in (args.get("rows") or []) if isinstance(r, (list, tuple))]
    width = max((len(r) for r in rows), default=0)
    values, percents = [], []
    for i in range(width):
        col = [v for v in _column(rows, i) if v is not None]
        if col:
            values += col + [sum(col), min(col), max(col)]
    ai, refs = _scenarios(args)
    if ai is None:
        return values, percents
    actual = _column(rows, ai)
    act_total = sum(v for v in actual if v is not None)
    for v in actual:
        if v is not None and act_total:
            percents.append(v / act_total * 100)
    for ri in refs:
        ref = _column(rows, ri)
        pairs = [(a, r) for a, r in zip(actual, ref) if a is not None and r is not None]
        diffs = [a - r for a, r in pairs]
        total_a, total_r = sum(a for a, _ in pairs), sum(r for _, r in pairs)
        total_d = total_a - total_r
        values += diffs + [total_d]
        percents += [(a - r) / abs(r) * 100 for a, r in pairs + [(total_a, total_r)] if r]
        if total_d:
            percents += [d / total_d * 100 for d in diffs]
    if args.get("time_axis"):
        points = [v for v in actual if v is not None]
        for i, early in enumerate(points):
            for late in points[i + 1:]:
                values.append(late - early)
                if early:
                    percents.append((late - early) / abs(early) * 100)
    return values, percents


def _scales(args, values):
    """What one row unit is worth: the subtitle's unit; else 1 when the rows are plainly in units (a value of
    10,000 or more); else unknown, so units, thousands and millions are all tried."""
    u = unit(args)
    if u:
        return (u,)
    return (1.0,) if any(abs(v) >= 10000 for v in values) else (1.0, 1e3, 1e6)


def _matches(n, values, percents, scales):
    tol = 0.5 * 10 ** -n.decimals + 1e-9
    d = abs(n.value)
    if n.percent:
        return any(abs(abs(p) - d) <= tol for p in percents)
    m = MULT[n.suffix]
    return any(abs(abs(c) * worth / m - d) <= tol for worth in scales for c in values)


def untraced(text, charts, kept=()):
    """The raw form of every number in text that none of the charts' rows supports, minus the kept ones."""
    sets = []
    for args in charts:
        args = args if isinstance(args, dict) else {}
        v, p = candidates(args)
        sets.append((v, p, _scales(args, v)))
    kept = set(kept or ())
    out = []
    for raw, n in tokens(text):
        if raw in kept or raw in out:
            continue
        if n is not None:
            if re.fullmatch(r"\d{4}", raw) and 1900 <= n.value <= 2100:
                continue  # a year: four bare digits, no sign, separator or unit
            if any(_matches(n, v, p, scales) for v, p, scales in sets):
                continue
        out.append(raw)
    return out


def contribution(args):
    """How much the chart moves: the sum of absolute variances to its first reference scenario, 0.0 without one."""
    ai, refs = _scenarios(args)
    if ai is None or not refs:
        return 0.0
    rows = [r for r in (args.get("rows") or []) if isinstance(r, (list, tuple))]
    total = sum(abs(a - r) for a, r in zip(_column(rows, ai), _column(rows, refs[0])) if a is not None and r is not None)
    return float(total * (unit(args) or 1.0))  # in units, so charts in thousands and in units compare
