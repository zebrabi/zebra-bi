#!/usr/bin/env python3
"""numbercheck.py: trace every number in a report's text to the rows of its charts.

A number passes when one of the charts' rows supports it: a value, a total, the smallest or largest value, a
variance to PL, PY, PM or FC, a variance in percent, a share of a total, or a move between two periods. Signs live
in the words ("39.2k under plan"), so absolute values are compared, within the rounding the text shows. Rows may
already be in thousands or millions, so "60.5k" passes against a row of 60.5. Numbers are read with English
separators only; a European "359,7k" or "1.026.887,77" is reported as untraced, never read as another number.
Standard library only.
"""
import re
from collections import namedtuple

Num = namedtuple("Num", "raw value decimals suffix percent")

REFS = ("PY", "PM", "PL", "FC")
MULT = {"": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "bn": 1e9}
# A run of digits with any separators, its sign and its unit; a digit right after a letter (Q4, FY26) is a label.
TOKEN = re.compile(r"(?<![\w.,])([+\-−]?)(\d[\d.,]*\d|\d)(\s?%|bn|[kKM])?(?![\w])")
ENGLISH = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def tokens(text):
    """(raw, Num or None) for every number-like run in text; None when it is not an English number."""
    out = []
    for m in TOKEN.finditer(str(text or "")):
        sign, digits, unit = m.group(1), m.group(2), (m.group(3) or "").strip()
        raw = m.group(0)
        if not ENGLISH.fullmatch(digits):
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


def _matches(n, values, percents):
    tol = 0.5 * 10 ** -n.decimals + 1e-9
    d = abs(n.value)
    if n.percent:
        return any(abs(abs(p) - d) <= tol for p in percents)
    m = MULT[n.suffix]
    return any(abs(abs(c) / scale - d) <= tol for scale in (m, m / 1e3, m / 1e6) for c in values)


def untraced(text, charts, kept=()):
    """The raw form of every number in text that none of the charts' rows supports, minus the kept ones."""
    values, percents = [], []
    for args in charts:
        v, p = candidates(args if isinstance(args, dict) else {})
        values += v
        percents += p
    kept = set(kept or ())
    out = []
    for raw, n in tokens(text):
        if raw in kept or raw in out:
            continue
        if n is not None:
            if not n.percent and not n.suffix and n.decimals == 0 and 1900 <= abs(n.value) <= 2100:
                continue  # a year
            if _matches(n, values, percents):
                continue
        out.append(raw)
    return out


def contribution(args):
    """How much the chart moves: the sum of absolute variances to its first reference scenario, 0.0 without one."""
    ai, refs = _scenarios(args)
    if ai is None or not refs:
        return 0.0
    rows = [r for r in (args.get("rows") or []) if isinstance(r, (list, tuple))]
    return float(sum(abs(a - r) for a, r in zip(_column(rows, ai), _column(rows, refs[0])) if a is not None and r is not None))
