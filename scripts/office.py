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
Standard library only.
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
    story = json.load(open(os.path.join(story_dir, "story.json"), encoding="utf-8"))
    renderer_path, meta = S.renderer()
    results = S.render_visuals(story_dir, story)
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
    snaps, chrome = SN.snapshot_all({v: results[v] for v in used}, renderer_path, os.path.join(story_dir, "snapshots"))
    if not chrome:
        print("No Chrome, Edge or Chromium found (set ZEBRA_BI_CHROME): the files carry a placeholder image, "
              "so readers without the Zebra BI add-in see no chart.", file=sys.stderr)
    src = story.get("source", {})
    footer = "Source: %s. Built %s." % (src.get("label") or src.get("name") or "unknown", dt.date.today().isoformat())
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
