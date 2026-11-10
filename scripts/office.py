#!/usr/bin/env python3
"""office.py: write a Zebra BI data story as PowerPoint and/or Excel with live Zebra BI for Office visuals.

  python3 -I office.py build <story-dir> [--pptx] [--xlsx] [--brand <dir>]   both formats when neither flag is given
  python3 -I office.py check <file.pptx|file.xlsx>            structural checks, exit 1 on any error

`build` reuses story.py: every visual in story.json is rendered (or reused from visuals/<id>.json),
then drawn once more by the same renderer in headless Chrome for a snapshot PNG (snapshots/<id>.png).
Each visual goes into the file as a Zebra BI Charts or Tables for Office add-in part carrying its
settings and data, with the snapshot as the image Office shows where the add-in is not installed.
The page around each visual follows the brand (brand.py: .zebra/brand/, else the editorial default);
the charts themselves never change. Every written file passes `check` before its path is printed.
<story-dir> may also be a report builder folder (report.zbreport): it builds as a PowerPoint deck, a cover
with the key message and KPI tiles, then a slide per chart in the report's reading order with its message as
the title and its note beside it, each chart drawn at the width the slide gives it. A report whose text holds
a number no chart supports is not built. Standard library only.
"""
import datetime as dt, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brand as B  # noqa: E402
import story as S  # noqa: E402
from zoffice import check as C, pptx as P, snapshot as SN, xlsx as XL  # noqa: E402


def run_check(path):
    errors, warnings, summary = C.check(path)
    for w in warnings:
        print("warning: " + w, file=sys.stderr)
    for e in errors:
        print("error: " + e, file=sys.stderr)
    return errors, summary


def build(story_dir, formats, brand_dir=None):
    brand = B.load(story_dir, brand_dir)
    errors, warnings = B.check(brand)
    for w in warnings:
        print("brand warning: " + w, file=sys.stderr)
    if errors:
        sys.exit("brand %r is not valid: %s" % (brand.get("name"), "; ".join(errors)))
    report_file = os.path.join(story_dir, "report.zbreport")
    report = not os.path.exists(os.path.join(story_dir, "story.json")) and os.path.isfile(report_file)
    if report:
        import report as R  # report.py imports story.py, so it is imported only for a report folder
        try:
            story = R.to_deck(R.load(report_file))
        except ValueError as e:
            sys.exit("Not built: %s" % e)
        if "xlsx" in formats:
            print("A report builds as PowerPoint only; no workbook was written.", file=sys.stderr)
        formats = ["pptx"]
    else:
        story = json.load(open(os.path.join(story_dir, "story.json"), encoding="utf-8"))
        in_rows = {c.get("visual") for r in story.get("rows") or [] if isinstance(r, dict)
                   for c in r.get("cells") or [] if isinstance(c, dict) and c.get("visual")}
        left_out = sorted(in_rows - {s.get("visual") for s in story.get("sections", [])})
        if left_out:
            print("story.json rows name charts a deck or workbook cannot show (only the sections are slides and "
                  "sheets): %s" % ", ".join(left_out), file=sys.stderr)
    results = S.render_visuals(story_dir, story)  # first, so `Sending <n> chart(s)` comes before any call to the MCP
    renderer_path, meta = S.renderer()
    used = [s["visual"] for s in story.get("sections", []) if s.get("visual")]
    missing = [v for v in used if v not in results]
    if missing:
        sys.exit("sections name visuals that are not in story.json: %s" % ", ".join(missing))
    visuals = {}
    for vid in used:
        v = (results[vid].get("structuredContent") or {}).get("visual")
        if not v:
            sys.exit("visual %r has no structuredContent.visual in its result; delete visuals/%s.json and build again" % (vid, vid))
        visuals[vid] = v
    width = P.chart_width(brand) if report else SN.WIDTH
    snaps, chrome = SN.snapshot_all({v: results[v] for v in used}, renderer_path, os.path.join(story_dir, "snapshots"), width)
    if not chrome:
        print("No Chrome, Edge or Chromium found (set ZEBRA_BI_CHROME): the files carry a placeholder image, "
              "so readers without the Zebra BI add-in see no chart.", file=sys.stderr)
    src = story.get("source") or {}
    label = src.get("label") or src.get("name")
    footer = ("Source: %s. " % label if label else "") + "Built %s." % dt.date.today().isoformat()
    slug = os.path.basename(os.path.normpath(story_dir))
    written, failed = [], False
    for fmt in formats:
        out = os.path.join(story_dir, "%s.%s" % (slug, fmt))
        try:
            (P if fmt == "pptx" else XL).build(story, visuals, snaps, out, footer, brand)
        except ValueError as e:
            failed = True
            print("%s was not written: %s" % (out, e), file=sys.stderr)
            continue
        errors, summary = run_check(out)
        if errors:
            failed = True
            os.replace(out, out + ".failed")
            print("%s failed its checks and was renamed to %s.failed" % (out, os.path.basename(out)), file=sys.stderr)
            continue
        written.append({"file": out, "bytes": os.path.getsize(out), **summary})
    print(json.dumps({"files": written, "brand": brand.get("name"), "snapshots": "chrome" if chrome else "placeholder",
                      "renderer_sha256": meta["sha256"][:12]}, indent=2))
    if failed:
        sys.exit(1)


def main(argv):
    if len(argv) < 2 or argv[0] not in ("build", "check"):
        sys.exit(__doc__)
    if argv[0] == "check":
        errors, summary = run_check(argv[1])
        print(json.dumps({"file": argv[1], "ok": not errors, **summary}, indent=2))
        sys.exit(1 if errors else 0)
    formats = [f for f in ("pptx", "xlsx") if "--" + f in argv] or ["pptx", "xlsx"]
    bdir = os.path.abspath(argv[argv.index("--brand") + 1]) if "--brand" in argv else None
    build(os.path.abspath(argv[1]), formats, bdir)


if __name__ == "__main__":
    main(sys.argv[1:])
