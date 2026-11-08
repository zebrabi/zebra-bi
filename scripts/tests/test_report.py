"""Tests for report.py and report_server.py. Run: python3 -I -m unittest discover <plugin>/scripts/tests

Nothing goes over the network: charts come from visuals/<id>.json written by the test, and the server runs with
ZEBRA_BI_MCP_URL on a closed loopback port, so an uncached chart fails fast instead of drawing.
"""
import datetime as dt, hashlib, json, os, shutil, subprocess, sys, tempfile, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import report as R  # noqa: E402
import story as S  # noqa: E402

SERVER = os.path.join(os.path.dirname(HERE), "report_server.py")
ARGS = {"title": "Sales by region", "chart_type": "variance", "category_columns": ["Region"],
        "measure": {"name": "Sales", "Val": "AC", "PL": "PL"},
        "columns": ["Region", "AC", "PL"], "rows": [["West", 226.3, 226.8], ["East", 185.4, 194.4]]}
RESULT = {"content": [{"type": "text", "text": "Rendered a variance table."}], "isError": False,
          "structuredContent": {"visual": {"title": "Sales by region", "ycol_ac": [{"value": 226.3}]}}}
FAKE_MCP = "http://127.0.0.1:9/mcp"  # nothing listens there


def cache(report_dir, vid, args, result=RESULT, endpoint=None):
    os.makedirs(os.path.join(report_dir, "visuals"), exist_ok=True)
    with open(os.path.join(report_dir, "visuals", vid + ".json"), "w") as f:
        json.dump({"args_hash": S.args_hash(args), "endpoint": endpoint or S.ENDPOINT, "result": result}, f)


def ids(rep):
    return [[c["id"] for c in r["cells"]] for r in rep["rows"]]


class Format(unittest.TestCase):
    def test_new_report_is_valid_and_empty(self):
        rep = R.validate(R.new("  Q3   close "))
        self.assertEqual(rep["title"], "Q3 close")
        self.assertEqual(rep["rows"], [])

    def test_rows_hold_one_to_three_cells(self):
        rep = R.new("r")
        for _ in range(3):
            R.add_text(rep, "x", row=1)
        self.assertEqual([len(r["cells"]) for r in rep["rows"]], [3])
        R.add_text(rep, "fourth", row=1)  # row 1 is full: a new row
        self.assertEqual([len(r["cells"]) for r in rep["rows"]], [3, 1])
        rep["rows"][0]["cells"].append({"id": "c99", "type": "text", "text": "x"})
        with self.assertRaises(ValueError):
            R.validate(rep)

    def test_chart_cell_must_name_a_held_chart(self):
        rep = R.new("r")
        rep["rows"].append({"cells": [{"id": "c1", "type": "chart", "visual": "nope"}]})
        with self.assertRaises(ValueError):
            R.validate(rep)

    def test_add_chart_gives_unique_ids(self):
        rep = R.new("r")
        a, _ = R.add_chart(rep, ARGS)
        b, row = R.add_chart(rep, ARGS, row=1)
        self.assertEqual((a, b, row), ("sales-by-region", "sales-by-region-2", 1))
        self.assertEqual(rep["rows"][0]["cells"][1], {"id": "c2", "type": "chart", "visual": "sales-by-region-2"})

    def test_summary_names_cells_never_data(self):
        rep = R.new("Close")
        R.add_chart(rep, ARGS)
        R.add_text(rep, "West is on plan. " * 20)
        s = R.summary(rep)
        self.assertIn("[c1] chart 'Sales by region'", s)
        self.assertIn("[c2] text 'West is on plan.", s)
        self.assertNotIn("226.3", s)

    def test_layout_moves_cells_and_drops_unshown_charts(self):
        rep = R.new("r")
        R.add_chart(rep, ARGS)
        R.add_text(rep, "note")
        rows = [{"cells": [rep["rows"][1]["cells"][0]]}, {"cells": []}]  # chart removed, empty row dropped
        out = R.apply_layout(rep, "New title", rows, rep["rev"])
        self.assertEqual(out["title"], "New title")
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(out["visuals"], {})

    def test_layout_cannot_invent_a_chart(self):
        rep = R.new("r")
        with self.assertRaises(ValueError):
            R.apply_layout(rep, None, [{"cells": [{"id": "c1", "type": "chart", "visual": "x"}]}], rep["rev"])

    def test_stale_rev_conflicts(self):
        rep = R.new("r")
        rep["rev"] = 4
        with self.assertRaises(R.Conflict):
            R.apply_layout(rep, None, [], 3)


class Cells(unittest.TestCase):
    def setUp(self):
        self.rep = R.new("r")
        R.add_chart(self.rep, ARGS)                     # c1, row 1
        R.add_text(self.rep, "a")                       # c2, row 2
        R.add_text(self.rep, "b", row=2)                # c3, row 2

    def test_move_into_a_row_at_a_position(self):
        R.move_cell(self.rep, "c3", 1, position=1)
        self.assertEqual(ids(self.rep), [["c3", "c1"], ["c2"]])

    def test_move_to_a_new_row_and_drop_the_emptied_one(self):
        R.move_cell(self.rep, "c1", 3)                  # one past the last row: a new row; row 1 empties
        self.assertEqual(ids(self.rep), [["c2", "c3"], ["c1"]])

    def test_move_refuses_a_full_row_and_bad_rows(self):
        R.add_text(self.rep, "c", row=2)                # row 2 now full
        with self.assertRaises(ValueError):
            R.move_cell(self.rep, "c1", 2)
        with self.assertRaises(ValueError):
            R.move_cell(self.rep, "c1", 9)
        self.assertEqual(ids(self.rep), [["c1"], ["c2", "c3", "c4"]])  # untouched after a refusal

    def test_a_chart_carries_its_note_through_edits_and_exports(self):
        vid, _ = R.add_chart(self.rep, dict(ARGS, title="Sales by month"), note="  Sales grew **+4%** on PY.  ")
        cell = self.rep["rows"][-1]["cells"][0]
        self.assertEqual(cell["note"], "Sales grew **+4%** on PY.")
        R.validate(self.rep)
        self.assertIn("note 'Sales grew **+4%** on PY.'", R.summary(self.rep))
        self.assertIn("note MISSING", R.summary(self.rep))  # c1 has none yet
        cell["kept"] = ["+4%"]  # the chart holds no +4%: the user keeps it, so the export takes it
        moved = R.apply_layout(self.rep, None, self.rep["rows"], self.rep["rev"])
        self.assertEqual(moved["rows"][-1]["cells"][0]["note"], "Sales grew **+4%** on PY.")
        self.assertEqual(R.to_story(moved)["rows"][-1]["cells"][0], {"visual": vid, "note": "Sales grew **+4%** on PY."})
        bare = [{"cells": [{k: v for k, v in c.items() if k != "note"} for c in r["cells"]]} for r in self.rep["rows"]]
        kept = R.apply_layout(self.rep, None, bare, self.rep["rev"])  # a save without notes keeps them
        self.assertEqual(kept["rows"][-1]["cells"][0]["note"], "Sales grew **+4%** on PY.")
        R.update_text(self.rep, cell["id"], "")
        self.assertNotIn("note", cell)  # an empty note is no note
        with self.assertRaises(ValueError):
            R.validate(dict(self.rep, rows=[{"cells": [dict(cell, note="x" * (R.MAX_TEXT + 1))]}]))

    def test_remove_drops_the_chart_and_an_empty_row(self):
        R.remove_cell(self.rep, "c1")
        self.assertEqual(ids(self.rep), [["c2", "c3"]])
        self.assertEqual(self.rep["visuals"], {})

    def test_update_text_and_replace_chart_check_the_cell_type(self):
        R.update_text(self.rep, "c2", "new")
        self.assertEqual(self.rep["rows"][1]["cells"][0]["text"], "new")
        R.update_text(self.rep, "c1", "West led **+37.9k**.")  # a chart's text is the note under it
        self.assertEqual(self.rep["rows"][0]["cells"][0]["note"], "West led **+37.9k**.")
        vid = R.replace_chart(self.rep, "c1", dict(ARGS, title="Sales by product"))
        self.assertEqual(vid, "sales-by-product")
        self.assertEqual(list(self.rep["visuals"]), ["sales-by-product"])
        with self.assertRaises(ValueError):
            R.replace_chart(self.rep, "c2", ARGS)
        with self.assertRaises(ValueError):
            R.remove_cell(self.rep, "c42")


class Draft(unittest.TestCase):
    """The first draft's fields: a message above each chart, evidence under it, its reason and recipe; the key
    message and KPI tiles; and the number checks over all of them."""

    def setUp(self):
        sys.path.insert(0, HERE)
        from test_numbercheck import PNL
        self.pnl = PNL
        self.rep = R.new("r")
        R.add_chart(self.rep, ARGS)  # an older chart cell: no message, no recipe

    def checked(self):
        rep = R.new("Checks")
        R.add_chart(rep, self.pnl, message="Revenue missed plan by **39.2k**", note="That is **−3.7%**; COGS 99.0k under.")
        R.set_key_message(rep, "Revenue missed plan by 39.2k.")
        R.set_kpis(rep, [{"value": "1.0M", "label": "Revenue, ΔPL −39.2k"}])
        return rep, rep["rows"][-1]["cells"][0]

    def test_a_chart_carries_message_evidence_why_and_recipe(self):
        R.add_chart(self.rep, ARGS, message="West led **+37.9k**", note="West AC 226.3 vs PL 226.8.",
                    why="Regions compare best side by side.", recipe={"source": "sales.xlsx", "code": "df.groupby('Region')"})
        cell = self.rep["rows"][-1]["cells"][0]
        self.assertEqual((cell["message"], cell["why"], cell["recipe"]["source"]),
                         ("West led **+37.9k**", "Regions compare best side by side.", "sales.xlsx"))
        R.validate(self.rep)

    def test_old_reports_stay_valid_and_say_message_missing(self):
        R.validate(self.rep)
        self.assertIn("message MISSING", R.summary(self.rep))
        self.assertEqual(R.checks(self.rep), {})

    def test_checks_flag_only_untraced_numbers(self):
        rep, cell = self.checked()
        self.assertEqual(R.checks(rep), {"cells": {cell["id"]: {"note": ["99.0k"]}}})
        self.assertEqual(R.flags(rep), ["%s note: 99.0k" % cell["id"]])
        self.assertIn("unchecked: 99.0k", R.summary(rep))

    def test_kept_numbers_are_the_controllers(self):
        rep, cell = self.checked()
        cell["kept"] = ["99.0k"]
        self.assertEqual(R.flags(rep), [])

    def test_key_message_and_kpis_are_checked_against_every_chart(self):
        rep, _ = self.checked()
        R.set_key_message(rep, "Revenue missed plan by 41.0k.")
        R.set_kpis(rep, [{"value": "1.0M", "label": "Revenue"}, {"value": "7.7M", "label": "Bogus"}])
        self.assertEqual(R.checks(rep)["key_message"], ["41.0k"])
        self.assertEqual(R.checks(rep)["kpis"], [[], ["7.7M"]])

    def test_update_text_reaches_message_and_why(self):
        R.update_text(self.rep, "c1", "Sales missed plan", field="message")
        R.update_text(self.rep, "c1", "One bar per region.", field="why")
        cell = self.rep["rows"][0]["cells"][0]
        self.assertEqual((cell["message"], cell["why"]), ("Sales missed plan", "One bar per region."))
        R.update_text(self.rep, "c1", "", field="message")
        self.assertNotIn("message", cell)

    def test_layout_saves_keep_and_change_the_new_fields(self):
        R.update_text(self.rep, "c1", "Sales missed plan", field="message")
        bare = [{"cells": [{"id": "c1", "type": "chart", "visual": self.rep["rows"][0]["cells"][0]["visual"]}]}]
        out = R.apply_layout(self.rep, None, bare, self.rep["rev"], key_message="Key", kpis=[{"value": "1", "label": "x"}],
                             kept=["1"])
        self.assertEqual(out["rows"][0]["cells"][0]["message"], "Sales missed plan")  # absent in the save: kept
        self.assertEqual((out["key_message"], out["kpis"], out["kept"]), ("Key", [{"value": "1", "label": "x"}], ["1"]))

    def test_the_story_carries_key_message_tiles_and_messages(self):
        rep, cell = self.checked()
        cell["kept"] = ["99.0k"]
        story = R.to_story(rep)
        self.assertEqual((story["kicker"], story["title"]), ("Checks", "Revenue missed plan by 39.2k."))
        self.assertEqual(story["kpis"], [{"value": "1.0M", "label": "Revenue, ΔPL −39.2k"}])
        self.assertEqual(story["rows"][0]["cells"][0]["message"], "Revenue missed plan by **39.2k**")
        self.assertNotIn("kicker", R.to_story(self.rep))  # no key message: the title is the heading, no label above it

    def test_the_key_message_heading_has_no_bold_marks(self):
        rep, cell = self.checked()
        cell["kept"] = ["99.0k"]
        R.set_key_message(rep, "Revenue missed plan by **39.2k**.")
        self.assertEqual(R.to_story(rep)["title"], "Revenue missed plan by 39.2k.")  # a story title is plain text

    def test_the_story_refuses_untraced_numbers(self):
        rep, _ = self.checked()
        with self.assertRaisesRegex(ValueError, "not in their charts: c1 note: 99.0k"):
            R.to_story(rep)

    def test_hidden_notes_do_not_block_the_export(self):
        rep, cell = self.checked()  # its note holds 99.0k, which no chart has
        rep["show_notes"] = False
        self.assertEqual(R.flags(rep), [])  # the exports leave hidden notes out, so they do not block them
        self.assertNotIn("note", R.to_story(rep)["rows"][0]["cells"][0])
        self.assertEqual(R.checks(rep)["cells"][cell["id"]]["note"], ["99.0k"])  # the builder still shows it

    def test_an_emptied_kpi_tile_is_dropped(self):
        R.set_kpis(self.rep, [{"value": "", "label": ""}, {"value": "411.7", "label": "Sales AC"}, {"value": " ", "label": ""}])
        self.assertEqual(self.rep["kpis"], [{"value": "411.7", "label": "Sales AC"}])

    def test_an_old_report_arranges_and_exports(self):
        R.add_text(self.rep, "Sales by region.")
        R.arrange(self.rep, "board-pack")
        R.validate(self.rep)
        story = R.to_story(self.rep)
        self.assertEqual(story["title"], "r")
        self.assertEqual(len(story["rows"]), 2)

    def test_limits(self):
        with self.assertRaises(ValueError):
            R.set_kpis(self.rep, [{"value": "1", "label": "x"}] * 5)
        with self.assertRaises(ValueError):
            R.set_key_message(self.rep, "x" * 601)
        with self.assertRaises(ValueError):
            R.validate(dict(self.rep, layout="grid"))


class Layouts(unittest.TestCase):
    """Named layouts rearrange the same cells: charts by how much they move, largest first, text after its chart."""

    def setUp(self):
        self.rep = R.new("Layouts")
        chart = lambda name, size: {"title": name, "chart_type": "variance", "columns": ["Item", "AC", "PL"],
                                    "measure": {"name": name, "Val": "AC", "PL": "PL"}, "rows": [["a", size, 0]]}
        R.add_chart(self.rep, chart("small", 1), message="m", note="n")
        R.add_chart(self.rep, chart("middle", 10), row=1, message="m", note="n")
        R.add_text(self.rep, "About the middle chart.", row=1)  # row 1 is now full: small, middle, text
        R.add_chart(self.rep, chart("big", 100), message="m", note="n")
        cells = {c["id"]: c for r in self.rep["rows"] for c in r["cells"]}
        self.middle_id = next(i for i, c in cells.items() if c.get("visual") == "middle")
        self.text_id = next(i for i, c in cells.items() if c["type"] == "text")

    def test_layouts_rearrange_the_same_cells(self):
        before = sorted(c["id"] for r in self.rep["rows"] for c in r["cells"])
        for layout in R.LAYOUTS:
            R.arrange(self.rep, layout)
            self.assertEqual(sorted(c["id"] for r in self.rep["rows"] for c in r["cells"]), before)
            self.assertTrue(all(1 <= len(r["cells"]) <= 3 for r in self.rep["rows"]))
            self.assertEqual(self.rep["layout"], layout)
            R.validate(self.rep)

    def charts(self):
        return [[c["visual"] for c in r["cells"] if c["type"] == "chart"] for r in self.rep["rows"]
                if any(c["type"] == "chart" for c in r["cells"])]

    def test_one_pager_fills_rows_evenly_largest_first(self):
        R.arrange(self.rep, "one-pager")
        self.assertEqual(self.charts(), [["big", "middle", "small"]])
        for n, sizes in ((1, [1]), (2, [2]), (3, [3]), (4, [2, 2]), (5, [3, 2]), (7, [3, 2, 2]), (9, [3, 3, 3])):
            self.assertEqual(R.even_rows(n), sizes)

    def test_variance_walk_keeps_the_order_the_charts_were_built_in(self):
        R.arrange(self.rep, "one-pager")  # sorted by size first, so the walk cannot just keep the current order
        R.arrange(self.rep, "variance-walk")
        self.assertEqual(self.charts(), [["small"], ["middle"], ["big"]])

    def test_the_build_order_reads_cell_numbers(self):
        rep = R.new("Eleven")
        for i in range(11):  # c10 and c11 come after c9, so a string sort of the ids would put them early
            R.add_chart(rep, {"title": "k%d" % i, "chart_type": "bar", "columns": ["Item", "AC", "PL"],
                              "measure": {"Val": "AC", "PL": "PL"}, "rows": [["a", i + 1, 0]]}, message="m")
        R.arrange(rep, "variance-walk")
        self.assertEqual([r["cells"][0]["visual"] for r in rep["rows"]], ["k%d" % i for i in range(11)])

    def test_board_pack_puts_each_chart_alone_largest_first(self):
        R.arrange(self.rep, "board-pack")
        self.assertEqual(self.charts(), [["big"], ["middle"], ["small"]])

    def test_text_follows_its_chart(self):
        for layout in R.LAYOUTS:
            R.arrange(self.rep, layout)
            ids = [[c["id"] for c in r["cells"]] for r in self.rep["rows"]]
            row_of = {cid: i for i, r in enumerate(ids) for cid in r}
            self.assertEqual(row_of[self.text_id], row_of[self.middle_id] + 1, layout)

    def test_a_moved_text_follows_the_chart_it_was_moved_under(self):
        R.arrange(self.rep, "board-pack")  # big, middle, text, small
        text = next(c for r in self.rep["rows"] for c in r["cells"] if c["id"] == self.text_id)
        self.assertEqual(text["after"], self.middle_id)
        rows = [r for r in self.rep["rows"] if r["cells"][0]["id"] != self.text_id]
        rows.insert(1, {"cells": [text]})  # the user drags it under the big chart
        self.rep = R.apply_layout(self.rep, None, rows, self.rep["rev"])
        R.validate(self.rep)
        R.arrange(self.rep, "variance-walk")
        order = [r["cells"][0].get("visual") or r["cells"][0]["id"] for r in self.rep["rows"]]
        self.assertEqual(order, ["small", "middle", "big", self.text_id])
        with self.assertRaises(ValueError):
            R.validate(dict(self.rep, rows=[{"cells": [dict(text, after="nope")]}]))

    def test_notes_follow_the_layout(self):
        R.arrange(self.rep, "one-pager")
        self.assertIs(self.rep["show_notes"], False)  # a one-pager shows the messages; the notes stay in the report
        for layout in ("variance-walk", "board-pack"):
            R.arrange(self.rep, layout)
            self.assertIs(self.rep["show_notes"], True)

    def test_the_exports_get_the_layout(self):
        for layout in R.LAYOUTS:
            R.arrange(self.rep, layout)
            self.assertEqual(R.to_story(self.rep)["layout"], layout)
        self.assertNotIn("layout", R.to_story(R.new("As built")))

    def test_a_single_chart_and_an_unknown_layout(self):
        sys.path.insert(0, HERE)
        from test_numbercheck import PNL
        one = R.new("One")
        R.add_chart(one, PNL, message="m", note="n")
        R.arrange(one, "board-pack")
        self.assertEqual(len(one["rows"]), 1)
        with self.assertRaises(ValueError):
            R.arrange(one, "grid")


class Deck(unittest.TestCase):
    """A report as the deck's story: a cover, then one section per chart in reading order."""

    def setUp(self):
        self.rep = R.new("P&L close")
        chart = lambda name: {"title": name, "chart_type": "bar", "columns": ["Item", "AC"], "rows": [["a", 1]]}
        R.add_text(self.rep, "Read this first.")
        R.add_chart(self.rep, chart("Left chart"), message="Left **message**", note="Left note")
        R.add_chart(self.rep, chart("Right chart"), row=2, message="Right message", note="Right note")
        R.add_text(self.rep, "About the right one.")
        R.add_chart(self.rep, chart("Untold chart"))  # no message: its title heads the slide

    def test_one_section_per_chart_in_reading_order(self):
        deck = R.to_deck(self.rep)
        self.assertEqual([s.get("visual") for s in deck["sections"]], [None, "left-chart", "right-chart", "untold-chart"])
        self.assertEqual(deck["sections"][0], {"heading": "P&L close", "body": "Read this first."})
        self.assertEqual(deck["sections"][1], {"heading": "Left message", "visual": "left-chart", "note": "Left note", "texts": []})
        self.assertEqual(deck["sections"][2]["texts"], ["About the right one."])
        self.assertEqual(deck["sections"][3]["heading"], "Untold chart")
        self.assertEqual((deck["title"], deck.get("kpis")), ("P&L close", None))
        self.assertNotIn("rows", deck)

    def test_the_cover_carries_the_key_message_and_tiles(self):
        R.set_key_message(self.rep, "Sales are **up**.")
        R.set_kpis(self.rep, [{"value": "1", "label": "Sales"}])
        deck = R.to_deck(self.rep)
        self.assertEqual((deck["kicker"], deck["title"], deck["kpis"]), ("P&L close", "Sales are up.", [{"value": "1", "label": "Sales"}]))

    def test_hidden_notes_never_leave_the_report(self):
        self.rep["show_notes"] = False
        self.assertEqual([s.get("note") for s in R.to_deck(self.rep)["sections"][1:]], ["", "", ""])

    def test_a_flagged_number_stops_the_deck(self):
        R.update_text(self.rep, "c2", "Left was 99.0.")
        with self.assertRaises(ValueError):
            R.to_deck(self.rep)


class BrokenFiles(unittest.TestCase):
    """One broken report file in the folder never takes the list, the search by title or Recently deleted with it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["ZEBRA_BI_REPORTS"] = self.tmp
        self.good = R.create("October close")
        for slug, body in (("nested", "[" * 100000), ("number-title", json.dumps(dict(R.new("x"), title=7))),
                           ("a-list", "[1, 2]"), ("huge", " " * (R.MAX_FILE + 1) + "{}")):
            os.makedirs(os.path.join(self.tmp, slug))
            with open(os.path.join(self.tmp, slug, R.FILE), "w") as f:
                f.write(body)

    def tearDown(self):
        os.environ.pop("ZEBRA_BI_REPORTS", None)
        shutil.rmtree(self.tmp)

    def test_the_good_report_still_lists_and_opens(self):
        self.assertEqual([r["report"] for r in R.reports()], [self.good])
        self.assertEqual(R.find("October close"), self.good)
        for slug in ("nested", "number-title", "a-list", "huge"):
            with self.assertRaises(ValueError):
                R.load(R.path_for(slug))

    def test_recently_deleted_skips_a_broken_entry(self):
        entry = R.trash(self.good)
        R.trash("nested")
        self.assertEqual([d["entry"] for d in R.deleted()], [entry])


class Concurrency(unittest.TestCase):
    """The chat's server and the builder's are separate processes: neither may write over the other's change."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["ZEBRA_BI_REPORTS"] = self.tmp

    def tearDown(self):
        os.environ.pop("ZEBRA_BI_REPORTS", None)
        shutil.rmtree(self.tmp)

    def test_a_chart_drawn_while_the_builder_saves_keeps_both_changes(self):
        import report_server as RS
        slug = R.create("Race")
        path = R.path_for(slug)

        def meanwhile(s, vid, args):  # the builder saves the user's text while Zebra BI draws the chart
            R.change(path, lambda rep: R.add_text(rep, "Typed meanwhile."))
            return {"content": [], "structuredContent": {"visual": {"title": "x"}}}, None
        real, RS.draw = RS.draw, meanwhile
        try:
            out = RS.call("add_chart", {"report": slug, "chart": ARGS, "message": "m"}, None)
        finally:
            RS.draw = real
        self.assertFalse(out.get("isError"), out)
        rep = R.load(path)
        self.assertEqual([c["type"] for r in rep["rows"] for c in r["cells"]], ["text", "chart"])
        self.assertEqual(rep["rev"], 3)  # created, the builder's save, the chart

    def test_a_save_waits_for_another_writers_lock(self):
        import threading
        slug = R.create("Lock")
        path = R.path_for(slug)
        held = threading.Event()

        def other():
            with R.locked(path):
                held.set()
                time.sleep(0.4)
                rep = R.load(path)
                R.add_text(rep, "first")
                R.save(path, rep)
        t = threading.Thread(target=other)
        t.start()
        held.wait(2)
        R.change(path, lambda rep: R.add_text(rep, "second"))
        t.join()
        rep = R.load(path)
        self.assertEqual([c["text"] for r in rep["rows"] for c in r["cells"]], ["first", "second"])


class SharedFolders(unittest.TestCase):
    """A report folder someone shared may carry symbolic links: deleting and duplicating never follow them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["ZEBRA_BI_REPORTS"] = os.path.join(self.tmp, "reports")
        self.outside = os.path.join(self.tmp, "outside.txt")
        with open(self.outside, "w") as f:
            f.write("keep me")

    def tearDown(self):
        os.environ.pop("ZEBRA_BI_REPORTS", None)
        shutil.rmtree(self.tmp)

    def test_deleting_never_writes_through_a_link(self):
        slug = R.create("Q3 review")
        os.symlink(self.outside, os.path.join(R.root(), slug, "deleted.json"))
        entry = R.trash(slug)
        with open(self.outside) as f:
            self.assertEqual(f.read(), "keep me")
        self.assertFalse(os.path.islink(os.path.join(R.root(), R.DELETED, entry, "deleted.json")))
        self.assertEqual([d["entry"] for d in R.deleted()], [entry])

    def test_duplicating_copies_only_the_reports_own_charts(self):
        slug = R.create("Q3 review")
        path = R.path_for(slug)
        rep = R.load(path)
        vid, _ = R.add_chart(rep, ARGS)
        R.save(path, rep)
        vdir = os.path.join(R.root(), slug, "visuals")
        os.makedirs(vdir, exist_ok=True)
        with open(os.path.join(vdir, vid + ".json"), "w") as f:
            f.write("{}")
        os.symlink(self.outside, os.path.join(vdir, "stolen.json"))
        os.symlink(self.tmp, os.path.join(vdir, "home"))
        os.symlink("..", os.path.join(vdir, "up"))  # a loop: copying through it would never end
        with open(os.path.join(vdir, "extra.txt"), "w") as f:
            f.write("not a chart")
        copy = R.duplicate(slug, "Q4 review")
        self.assertEqual(sorted(os.listdir(os.path.join(R.root(), copy, "visuals"))), [vid + ".json"])

    def test_a_pipe_where_a_chart_should_be_does_not_hang_duplicating(self):
        slug = R.create("Q3 review")
        path = R.path_for(slug)
        rep = R.load(path)
        vid, _ = R.add_chart(rep, ARGS)
        R.save(path, rep)
        os.makedirs(os.path.join(R.root(), slug, "visuals"))
        os.mkfifo(os.path.join(R.root(), slug, "visuals", vid + ".json"))  # opening it to read would wait for a writer
        copy = R.duplicate(slug, "Q4 review")
        self.assertFalse(os.path.exists(os.path.join(R.root(), copy, "visuals", vid + ".json")))


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["ZEBRA_BI_REPORTS"] = self.tmp

    def tearDown(self):
        os.environ.pop("ZEBRA_BI_REPORTS", None)
        shutil.rmtree(self.tmp)

    def test_save_bumps_rev_and_lists_last_saved_first(self):
        a, b = R.path_for("a"), R.path_for("b")
        self.assertEqual(R.save(a, R.new("A")), 1)
        self.assertEqual(R.save(a, R.load(a)), 2)
        R.save(b, R.new("B"))
        os.utime(a, (1, 1))
        self.assertEqual([r["report"] for r in R.reports()], ["b", "a"])
        self.assertEqual(R.info("a")["rev"], 2)

    def test_a_report_folder_reads_as_a_story_with_its_rows(self):
        slug = R.create("Q4 close")
        path = R.path_for(slug)
        rep = R.load(path)
        vid, _ = R.add_chart(rep, ARGS)
        R.add_text(rep, "West led **+37.9k**.", row=1)
        rep["rows"][0]["cells"][-1]["kept"] = ["+37.9k"]  # no chart holds it: kept by the user, so the export takes it
        R.add_chart(rep, dict(ARGS, title="Sales by month"))
        rep["visuals"]["unshown"] = {"args": ARGS}  # a chart no cell shows stays out of the exports
        R.save(path, rep)
        story = S.load(os.path.dirname(path))
        self.assertEqual(story["title"], "Q4 close")
        self.assertEqual(story["sections"], [])
        self.assertEqual(story["rows"][0], {"cells": [{"visual": vid}, {"text": "West led **+37.9k**."}]})
        self.assertEqual(len(story["rows"]), 2)
        self.assertEqual(sorted(story["visuals"]), sorted(S.story_visuals(story)))
        self.assertNotIn("source", story)  # a report names no data source, so the footer names none
        with open(os.path.join(os.path.dirname(path), "story.json"), "w") as f:
            json.dump({"title": "A story", "sections": [], "visuals": {}}, f)
        self.assertEqual(S.load(os.path.dirname(path))["title"], "A story")  # a story.json there wins

    def test_slug_must_be_plain(self):
        with self.assertRaises(ValueError):
            R.path_for("../etc")

    def test_create_never_reuses_a_taken_title(self):
        self.assertEqual(R.create("Q3 close"), "q3-close")
        self.assertEqual(R.create("Q3 close"), "q3-close-2")
        self.assertEqual(len(R.reports()), 2)

    def test_rename_keeps_the_folder(self):
        R.create("Old")
        self.assertEqual(R.rename("old", "  New   name "), "New name")
        self.assertEqual(R.info("old")["title"], "New name")
        self.assertEqual(R.find("new name"), "old")

    def test_find_by_slug_title_or_unique_prefix(self):
        R.create("P&L close October 2026")
        R.create("P&L close September 2026")
        self.assertEqual(R.find("p-l-close-october-2026"), "p-l-close-october-2026")
        self.assertEqual(R.find("P&L close September"), "p-l-close-september-2026")
        with self.assertRaises(ValueError):
            R.find("P&L close")  # two match
        with self.assertRaises(ValueError):
            R.find("nothing")

    def test_duplicate_copies_charts_and_resets_rev(self):
        slug = R.create("October")
        rep = R.load(R.path_for(slug))
        R.add_chart(rep, ARGS)
        R.save(R.path_for(slug), rep)
        cache(os.path.join(self.tmp, slug), "sales-by-region", ARGS)
        copy = R.duplicate(slug, "November")
        dup = R.load(R.path_for(copy))
        self.assertEqual((copy, dup["title"], dup["rev"]), ("november", "November", 1))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, copy, "visuals", "sales-by-region.json")))
        self.assertEqual(R.load(R.path_for(R.duplicate(slug)))["title"], "October (copy)")

    def test_trash_restore_and_purge(self):
        slug = R.create("Old")
        R.remember(slug, "t1")
        entry = R.trash(slug)
        self.assertFalse(R.exists(slug))
        self.assertIsNone(R.resolve("t1"))  # forgotten wherever it was open
        self.assertEqual([d["title"] for d in R.deleted()], ["Old"])
        self.assertEqual(R.restore(entry), "old")
        R.trash("old")
        R.create("Old")                      # a new "old" takes the slug
        self.assertEqual(R.restore(R.deleted_entry("Old")), "old-2")  # restored beside it, not over it
        R.purge(R.trash("old-2"))
        self.assertEqual(R.deleted(), [])
        with self.assertRaises(ValueError):
            R.purge("../x--20260101T000000000000")

    def test_deleted_reports_expire_after_keep_days(self):
        entry = R.trash(R.create("Ancient"))
        with open(os.path.join(self.tmp, R.DELETED, entry, "deleted.json"), "w") as f:
            json.dump({"deleted_at": "2020-01-01T00:00:00Z"}, f)
        self.assertEqual(R.deleted(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, R.DELETED, entry)))

    def test_each_thread_keeps_its_report_and_last_wins_elsewhere(self):
        a, b = R.create("A"), R.create("B")
        R.remember(a, "chat-1")
        R.remember(b, "chat-2")
        self.assertEqual((R.resolve("chat-1"), R.resolve("chat-2")), (a, b))
        self.assertEqual(R.resolve("new-chat"), b)  # a chat with no report works on the one opened last
        self.assertEqual(R.resolve(None), b)
        self.assertEqual(R.last(), b)

    def test_render_uses_the_cache_without_the_network(self):
        d = os.path.join(self.tmp, "r")
        cache(d, "sales-by-region", ARGS)
        self.assertEqual(R.render(d, "sales-by-region", ARGS), RESULT)
        self.assertEqual(R.server_note(RESULT), "Rendered a variance table.")


class Proc:
    """One report_server.py process over stdio, as a host starts one per surface."""

    def __init__(self, tmp):
        env = dict(os.environ, ZEBRA_BI_REPORTS=os.path.join(tmp, "reports"),
                   CLAUDE_PLUGIN_DATA=os.path.join(tmp, "data"), ZEBRA_BI_MCP_URL=FAKE_MCP)
        self.p = subprocess.Popen([sys.executable, "-I", SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=env)
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18"})

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)
        self.p.stdout.close()
        self.p.stderr.close()

    def rpc(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        msg = json.loads(self.p.stdout.readline())  # every stdout line is one JSON-RPC message
        assert msg["id"] == self.n, msg
        return msg

    def tool(self, name, args=None, thread=None):
        params = {"name": name, "arguments": args or {}}
        if thread:
            params["_meta"] = {"threadId": thread}
        return self.rpc("tools/call", params)["result"]


class Server(unittest.TestCase):
    """report_server.py over stdio, as a host would drive it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.server = Proc(self.tmp)

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.tmp)

    def rpc(self, method, params=None):
        return self.server.rpc(method, params)

    def tool(self, name, args=None, thread=None):
        return self.server.tool(name, args, thread)

    def report(self, slug, follow=None, thread=None):
        return self.tool("get_report", {"report": slug, "follow": follow or "none"}, thread)["structuredContent"]

    def test_tools_entrypoints_and_visibility(self):
        tools = {t["name"]: t for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(tools["open_report"]["_meta"]["openai/ui"]["entrypoints"], [{"type": "global"}, {"type": "thread"}])
        self.assertRegex(tools["open_report"]["_meta"]["ui"]["resourceUri"], r"^ui://zebra-bi/report-builder-[0-9a-f]{12}\.html$")
        for app_only in ("library", "get_report", "get_renderer", "save_layout", "purge_report"):
            self.assertEqual(tools[app_only]["_meta"]["ui"]["visibility"], ["app"])
        self.assertFalse(tools["delete_report"]["annotations"]["destructiveHint"])  # restorable
        self.assertTrue(tools["purge_report"]["annotations"]["destructiveHint"])
        for model in ("create_report", "list_reports", "rename_report", "duplicate_report", "delete_report", "restore_report",
                      "add_chart", "add_text", "update_text", "remove_cell", "move_cell", "replace_chart", "save_pdf", "save_pptx", "read_chart", "show_notes",
                      "set_key_message", "set_kpis", "arrange_report"):
            self.assertNotEqual(tools[model].get("_meta", {}).get("ui", {}).get("visibility"), ["app"], model)

    def drafted(self):
        """A report with one chart whose note holds a number the chart does not: 99.0."""
        slug = self.tool("create_report", {"title": "Draft"})["structuredContent"]["report"]
        cache(os.path.join(self.tmp, "reports", slug), "sales-by-region", ARGS, endpoint=FAKE_MCP)
        out = self.tool("add_chart", {"chart": ARGS, "message": "West missed plan by **0.5**",
                                      "note": "East came in 9.0 under plan; total 99.0.", "why": "Two regions side by side.",
                                      "recipe": {"source": "sales.xlsx", "code": "df.groupby('Region').sum()"}})
        return slug, out

    def test_add_chart_needs_a_message_and_reports_unchecked_numbers(self):
        tools = {t["name"]: t for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertIn("message", tools["add_chart"]["inputSchema"]["required"])
        _, out = self.drafted()
        self.assertFalse(out.get("isError"), out)
        self.assertTrue(out["content"][0]["text"].endswith("Unchecked numbers: 99.0"), out["content"][0]["text"])

    def test_key_message_kpis_layout_and_fields(self):
        slug, _ = self.drafted()
        self.assertFalse(self.tool("set_key_message", {"text": "Sales missed plan by 9.5."}).get("isError"))
        self.assertTrue(self.tool("set_kpis", {"kpis": [{"value": "1", "label": "x"}] * 5}).get("isError"))
        self.assertFalse(self.tool("set_kpis", {"kpis": [{"value": "411.7", "label": "Sales AC"}]}).get("isError"))
        self.assertFalse(self.tool("arrange_report", {"layout": "variance-walk"}).get("isError"))
        self.assertFalse(self.tool("update_text", {"cell": "c1", "text": "East missed plan by **9.0**", "field": "message"}).get("isError"))
        rep = self.report(slug)["report"]
        self.assertEqual((rep["key_message"], rep["kpis"][0]["value"], rep["layout"]), ("Sales missed plan by 9.5.", "411.7", "variance-walk"))
        read = self.tool("read_chart", {"cell": "c1"})["structuredContent"]
        self.assertEqual((read["message"], read["why"], read["recipe"]["source"]),
                         ("East missed plan by **9.0**", "Two regions side by side.", "sales.xlsx"))

    def test_get_report_carries_checks(self):
        slug, _ = self.drafted()
        self.assertEqual(self.report(slug)["checks"], {"cells": {"c1": {"note": ["99.0"]}}})

    def test_save_layout_takes_the_new_fields(self):
        slug, _ = self.drafted()
        rep = self.report(slug)["report"]
        rows = [{"cells": [dict(rep["rows"][0]["cells"][0], kept=["99.0"], message="Edited")]}]
        out = self.tool("save_layout", {"report": slug, "rev": rep["rev"], "rows": rows, "key_message": "Key",
                                        "kpis": [{"value": "411.7", "label": "AC"}]})
        self.assertFalse(out.get("isError"), out)
        self.assertEqual(out["structuredContent"]["checks"], {})  # the builder refreshes its flags from the save
        got = self.report(slug)
        self.assertEqual((got["report"]["key_message"], got["report"]["rows"][0]["cells"][0]["message"]), ("Key", "Edited"))
        self.assertEqual(got["checks"], {})  # 99.0 is kept now

    def test_app_only_tools_refuse_a_model_turn(self):
        slug, _ = self.drafted()
        turn = {"x-codex-turn-metadata": {"thread_id": "t1"}, "threadId": "t1"}  # a model's call in ChatGPT carries this
        for name, args in (("library", {}), ("get_report", {"report": slug}), ("purge_report", {"entry": "x"}),
                           ("save_layout", {"report": slug, "rev": 1, "rows": [], "kept": ["99.0"]})):
            out = self.rpc("tools/call", {"name": name, "arguments": args, "_meta": turn})["result"]
            self.assertTrue(out.get("isError"), name)
            self.assertIn("report builder app", out["content"][0]["text"])
        self.assertFalse(self.tool("library").get("isError"))  # the app's own call carries no model turn
        self.assertEqual(self.report(slug)["checks"], {"cells": {"c1": {"note": ["99.0"]}}})  # nothing was kept

    def test_save_pptx_refuses_while_numbers_are_flagged(self):
        slug, _ = self.drafted()
        out = self.tool("save_pptx")
        self.assertTrue(out.get("isError"))
        self.assertEqual(out["structuredContent"]["flags"], ["c1 note: 99.0"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "reports", slug, slug + ".pptx")))

    def test_save_pdf_refuses_while_numbers_are_flagged(self):
        slug, _ = self.drafted()
        out = self.tool("save_pdf")
        self.assertTrue(out.get("isError"))
        self.assertEqual(out["structuredContent"]["flags"], ["c1 note: 99.0"])
        self.assertIn("not in their charts", out["content"][0]["text"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "reports", slug, slug + ".pdf")))

    def test_a_one_pager_that_needs_more_pages_says_so(self):
        import report_server as RS
        self.assertEqual(RS.pdf_saved("/r/a.pdf", 1, "one-pager"),
                         "Saved /r/a.pdf (1 page). It holds the report's aggregated numbers, so share it only where the data may go.")
        two = RS.pdf_saved("/r/a.pdf", 2, "one-pager")
        self.assertIn("(2 pages)", two)
        self.assertIn("did not fit one page", two)
        self.assertNotIn("did not fit", RS.pdf_saved("/r/a.pdf", 4, "board-pack"))

    def test_app_page_nests_through_srcdoc(self):
        uri = self.rpc("resources/list")["result"]["resources"][0]["uri"]
        res = self.rpc("resources/read", {"uri": uri})["result"]["contents"][0]
        self.assertEqual(res["mimeType"], "text/html;profile=mcp-app")
        self.assertIn("ui/initialize", res["text"])
        self.assertIn("f.srcdoc = rendererText", res["text"])
        self.assertNotIn("createObjectURL", res["text"])

    def test_the_builder_wears_the_zebra_bi_brand(self):
        res = self.rpc("resources/list")["result"]["resources"][0]
        fonts = ["https://fonts.googleapis.com", "https://fonts.gstatic.com"]
        self.assertEqual(res["_meta"]["ui"]["csp"]["resourceDomains"], fonts)
        read = self.rpc("resources/read", {"uri": res["uri"]})["result"]["contents"][0]
        self.assertEqual(read["_meta"]["openai/widgetCSP"]["resource_domains"], fonts)
        self.assertIn("--accent:#FFD800", read["text"])
        self.assertIn("'Open Sans'", read["text"])
        self.assertIn("fonts.googleapis.com/css2?family=Open+Sans:wght@300;700", read["text"])
        self.assertNotIn("#0F6CBD", read["text"])  # the old blue is gone

    def test_open_with_no_reports_opens_the_empty_home(self):
        out = self.tool("open_report")
        self.assertIsNone(out["structuredContent"]["report"])
        self.assertTrue(self.tool("add_text", {"text": "x"})["isError"])

    def test_build_a_report_and_save_a_layout(self):
        slug = self.tool("create_report", {"title": "Q3 close"})["structuredContent"]["report"]
        self.assertEqual(slug, "q3-close")
        self.tool("add_text", {"text": "First"})
        self.tool("add_text", {"text": "Second", "row": 1})
        rep = self.report(slug)["report"]
        self.assertEqual([[c["text"] for c in r["cells"]] for r in rep["rows"]], [["First", "Second"]])
        self.assertTrue(self.tool("get_report", {"report": slug, "since": rep["rev"]})["structuredContent"]["unchanged"])
        a, b = rep["rows"][0]["cells"]
        saved = self.tool("save_layout", {"report": slug, "rev": rep["rev"], "title": "Q3 close, final",
                                          "rows": [{"cells": [b]}, {"cells": [a]}]})
        self.assertEqual(saved["structuredContent"]["rev"], rep["rev"] + 1)
        stale = self.tool("save_layout", {"report": slug, "rev": rep["rev"], "rows": []})
        self.assertTrue(stale["structuredContent"]["conflict"])
        with open(os.path.join(self.tmp, "reports", "q3-close", "report.zbreport")) as f:
            disk = json.load(f)
        self.assertEqual(disk["title"], "Q3 close, final")
        self.assertEqual([r["cells"][0]["text"] for r in disk["rows"]], ["Second", "First"])

    def test_chart_from_cache_and_failed_chart_leaves_report_alone(self):
        slug = self.tool("create_report", {"title": "Charts"})["structuredContent"]["report"]
        cache(os.path.join(self.tmp, "reports", slug), "sales-by-region", ARGS, endpoint=FAKE_MCP)
        added = self.tool("add_chart", {"chart": ARGS})
        self.assertFalse(added.get("isError"), added)
        self.assertIn("Rendered a variance table.", added["content"][0]["text"])
        self.assertEqual(self.report(slug)["results"]["sales-by-region"], RESULT)
        failed = self.tool("add_chart", {"chart": dict(ARGS, title="Not cached")})  # closed port: not drawn, not added
        self.assertTrue(failed["isError"])
        self.assertEqual(list(self.report(slug)["report"]["visuals"]), ["sales-by-region"])
        replaced = self.tool("replace_chart", {"cell": "c1", "chart": dict(ARGS, title="Not cached")})
        self.assertTrue(replaced["isError"])  # nor replaced
        self.assertEqual(list(self.report(slug)["report"]["visuals"]), ["sales-by-region"])

    def test_cells_by_id_from_the_chat(self):
        self.tool("create_report", {"title": "Cells"})
        for t in ("one", "two", "three"):
            self.tool("add_text", {"text": t})
        outline = self.tool("open_report", {"report": "Cells"})["content"][0]["text"]
        self.assertIn("[c3] text 'three'", outline)
        moved = self.tool("move_cell", {"cell": "c3", "row": 1, "position": 1})
        self.assertIn("Row 1: [c3] text 'three'; [c1] text 'one'", moved["content"][0]["text"])
        self.tool("update_text", {"cell": "c2", "text": "TWO"})
        self.tool("remove_cell", {"cell": "c1"})
        rep = self.report("cells")["report"]
        self.assertEqual([[c["text"] for c in r["cells"]] for r in rep["rows"]], [["three"], ["TWO"]])
        self.assertTrue(self.tool("remove_cell", {"cell": "c9"})["isError"])

    def test_lifecycle_from_the_chat(self):
        self.tool("create_report", {"title": "October"})
        self.tool("rename_report", {"title": "October close"})
        self.assertIn("* october: October close", self.tool("list_reports")["content"][0]["text"])
        dup = self.tool("duplicate_report", {"title": "November close"})["structuredContent"]["report"]
        self.assertEqual(dup, "november-close")
        self.assertEqual(self.report(dup)["report"]["title"], "November close")
        self.tool("delete_report", {"report": "October close"})
        self.assertTrue(self.report("october")["gone"])
        self.assertIn("october: October close", self.tool("list_reports", {"deleted": True})["content"][0]["text"])
        self.assertEqual(self.tool("restore_report", {"report": "October close"})["structuredContent"]["report"], "october")
        lib = self.tool("library")["structuredContent"]
        self.assertEqual(sorted(r["report"] for r in lib["reports"]), ["november-close", "october"])
        self.tool("delete_report", {"report": "october"})
        entry = self.tool("library")["structuredContent"]["deleted"][0]["entry"]
        self.assertEqual(self.tool("purge_report", {"entry": entry})["structuredContent"]["purged"], entry)
        self.assertEqual(self.tool("library")["structuredContent"]["deleted"], [])

    def test_each_chat_works_on_its_own_report(self):
        a = self.tool("create_report", {"title": "A"}, thread="chat-1")["structuredContent"]["report"]
        b = self.tool("create_report", {"title": "B"}, thread="chat-2")["structuredContent"]["report"]
        self.tool("add_text", {"text": "for A"}, thread="chat-1")
        self.tool("add_text", {"text": "for B"}, thread="chat-2")
        self.assertEqual(self.report(a)["report"]["rows"][0]["cells"][0]["text"], "for A")
        self.assertEqual(self.report(b)["report"]["rows"][0]["cells"][0]["text"], "for B")
        # the sidebar builder follows the report opened last; a chat's builder follows its chat
        self.assertEqual(self.report(a, follow="last"), {"switched": b})
        self.assertNotIn("switched", self.report(a, follow="thread", thread="chat-1"))

    def test_every_process_follows_the_report_opened_last(self):
        first = self.tool("create_report", {"title": "First"})["structuredContent"]["report"]
        sidebar = Proc(self.tmp)  # the builder in the sidebar runs in its own process
        try:
            self.assertEqual(sidebar.tool("library")["structuredContent"]["last"], first)
            self.tool("create_report", {"title": "Second"})
            self.assertEqual(sidebar.tool("get_report", {"report": first, "follow": "last"})["structuredContent"],
                             {"switched": "second"})
            self.tool("delete_report", {"report": "second"})
            self.assertTrue(sidebar.tool("get_report", {"report": "second", "follow": "none"})["structuredContent"]["gone"])
            self.assertTrue(sidebar.tool("save_layout", {"report": "second", "rev": 1, "rows": []})["isError"])
        finally:
            sidebar.close()

    def test_open_without_a_report_does_not_pin_the_thread(self):
        # ChatGPT gives the plugin's own surface a thread id; opening it is a look, not a choice
        self.tool("create_report", {"title": "October"})
        self.assertEqual(self.tool("open_report", {}, thread="plugin")["structuredContent"]["report"], "october")
        self.tool("create_report", {"title": "November"}, thread="chat-1")
        self.assertEqual(self.tool("open_report", {}, thread="plugin")["structuredContent"]["report"], "november")
        self.assertEqual(self.tool("library", {}, thread="plugin")["structuredContent"]["last"], "november")

    def test_notes_can_be_hidden_and_shown_for_the_whole_report(self):
        slug = self.tool("create_report", {"title": "Q4"})["structuredContent"]["report"]
        rdir = os.path.join(self.tmp, "reports", slug)
        cache(rdir, "sales-by-region", ARGS, endpoint=FAKE_MCP)
        self.tool("add_chart", {"chart": ARGS, "note": "West led."})
        self.tool("show_notes", {"show": False})
        self.assertNotIn("note", S.load(rdir)["rows"][0]["cells"][0])  # exports leave it out
        self.assertIn("chart notes hidden", self.tool("open_report", {"report": slug})["content"][0]["text"])
        rep = self.report(slug)["report"]
        self.assertEqual(rep["rows"][0]["cells"][0]["note"], "West led.")  # kept
        self.tool("save_layout", {"report": slug, "rev": rep["rev"], "rows": rep["rows"], "show_notes": True})
        self.assertEqual(S.load(rdir)["rows"][0]["cells"][0]["note"], "West led.")

    def test_read_chart_gives_the_rows_behind_a_note(self):
        slug = self.tool("create_report", {"title": "Q4"})["structuredContent"]["report"]
        cache(os.path.join(self.tmp, "reports", slug), "sales-by-region", ARGS, endpoint=FAKE_MCP)
        self.tool("add_chart", {"chart": ARGS, "note": "West led."})
        self.tool("add_text", {"text": "x"})
        out = self.tool("read_chart", {"cell": "c1"})
        self.assertEqual(out["structuredContent"]["chart"]["rows"], ARGS["rows"])
        self.assertIn("note: West led.", out["content"][0]["text"])
        self.assertTrue(self.tool("read_chart", {"cell": "c2"})["isError"])

    def test_save_pdf_without_a_browser_says_so(self):
        self.tool("create_report", {"title": "Q4 close"})
        self.server.close()
        os.environ["ZEBRA_BI_CHROME"] = os.path.join(self.tmp, "no-such-browser")
        try:
            self.server = Proc(self.tmp)
            out = self.tool("save_pdf")
        finally:
            os.environ.pop("ZEBRA_BI_CHROME")
        self.assertTrue(out["isError"])
        self.assertIn("No PDF was saved: The PDF needs Google Chrome", out["content"][0]["text"])

    @unittest.skipUnless(shutil.which("pdftotext"), "needs pdftotext")
    def test_save_pdf_prints_the_rows_side_by_side(self):
        sys.path.insert(0, HERE)
        from test_pdf import FAKE_RENDERER
        from zoffice import snapshot as SN
        if not SN.find_chrome():
            self.skipTest("no Chrome, Edge or Chromium installed")
        cache_dir = os.path.join(self.tmp, "data", "renderer")  # the server's CLAUDE_PLUGIN_DATA
        os.makedirs(cache_dir)
        page = (FAKE_RENDERER % 300).encode()
        with open(os.path.join(cache_dir, "visual.html"), "wb") as f:
            f.write(page)
        with open(os.path.join(cache_dir, "visual.json"), "w") as f:
            json.dump({"sha256": hashlib.sha256(page).hexdigest(), "endpoint": FAKE_MCP,
                       "fetched_at": dt.datetime.now().isoformat()}, f)
        slug = self.tool("create_report", {"title": "Q4 close"})["structuredContent"]["report"]
        rdir = os.path.join(self.tmp, "reports", slug)
        month = dict(ARGS, title="Sales by month")
        cache(rdir, "sales-by-region", ARGS, endpoint=FAKE_MCP)
        cache(rdir, "sales-by-month", month, endpoint=FAKE_MCP,
              result=dict(RESULT, structuredContent={"visual": {"title": "Sales by month"}}))
        self.tool("add_chart", {"chart": ARGS})
        self.tool("add_chart", {"chart": month, "row": 1})
        self.tool("add_text", {"text": "West led."})
        out = self.tool("save_pdf")
        self.assertFalse(out.get("isError"), out)
        pdf = out["structuredContent"]["file"]
        self.assertEqual(pdf, os.path.join(rdir, slug + ".pdf"))
        text = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True).stdout
        line = lambda label: next(i for i, l in enumerate(text.splitlines()) if label in l)
        self.assertEqual(line("Sales by region"), line("Sales by month"))  # one row, side by side
        self.assertIn("Q4 close", text)
        self.assertNotIn("Source:", text)

    def test_debug_log_records_meta_not_arguments(self):
        log = os.path.join(self.tmp, "calls.jsonl")
        self.server.close()
        os.environ["ZEBRA_BI_REPORT_LOG"] = log
        try:
            self.server = Proc(self.tmp)
            self.server.tool("create_report", {"title": "Secret title"}, thread="chat-9")
        finally:
            os.environ.pop("ZEBRA_BI_REPORT_LOG")
        with open(log) as f:
            lines = [json.loads(line) for line in f]
        call = [line for line in lines if line["method"] == "tools/call"][0]
        self.assertEqual((call["tool"], call["_meta"]), ("create_report", {"threadId": "chat-9"}))
        self.assertNotIn("Secret", json.dumps(lines))

    def test_unknown_method_and_tool(self):
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "nope"})["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
