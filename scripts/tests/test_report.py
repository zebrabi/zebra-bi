"""Tests for report.py and report_server.py. Run: python3 -I -m unittest discover <plugin>/scripts/tests

Nothing goes over the network: charts come from visuals/<id>.json written by the test, and the server runs with
ZEBRA_BI_MCP_URL on a closed loopback port, so an uncached chart fails fast instead of drawing.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

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

    def test_summary_names_charts_never_data(self):
        rep = R.new("Close")
        R.add_chart(rep, ARGS)
        R.add_text(rep, "West is on plan. " * 20)
        s = R.summary(rep)
        self.assertIn("chart 'Sales by region'", s)
        self.assertNotIn("226.3", s)
        self.assertIn("...", s)

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


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["ZEBRA_BI_REPORTS"] = self.tmp

    def tearDown(self):
        os.environ.pop("ZEBRA_BI_REPORTS", None)
        shutil.rmtree(self.tmp)

    def test_save_bumps_rev_and_lists_newest_first(self):
        a, b = R.path_for("a"), R.path_for("b")
        self.assertEqual(R.save(a, R.new("A")), 1)
        rep = R.load(a)
        self.assertEqual(R.save(a, rep), 2)
        R.save(b, R.new("B"))
        os.utime(a, (1, 1))
        self.assertEqual([r[0] for r in R.reports()], ["b", "a"])

    def test_slug_must_be_plain(self):
        with self.assertRaises(ValueError):
            R.path_for("../etc")

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

    def tool(self, name, args=None):
        return self.rpc("tools/call", {"name": name, "arguments": args or {}})["result"]


class Server(unittest.TestCase):
    """report_server.py over stdio, as a host would drive it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.server = Proc(self.tmp)
        self.rpc, self.tool = self.server.rpc, self.server.tool

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.tmp)

    def test_tools_entrypoints_and_visibility(self):
        tools = {t["name"]: t for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertEqual(tools["open_report"]["_meta"]["openai/ui"]["entrypoints"], [{"type": "global"}, {"type": "thread"}])
        self.assertRegex(tools["open_report"]["_meta"]["ui"]["resourceUri"], r"^ui://zebra-bi/report-builder-[0-9a-f]{12}\.html$")
        self.uri = tools["open_report"]["_meta"]["ui"]["resourceUri"]
        for app_only in ("get_report", "get_renderer", "save_layout"):
            self.assertEqual(tools[app_only]["_meta"]["ui"]["visibility"], ["app"])
        self.assertNotIn("_meta", tools["add_chart"])

    def test_app_page_nests_through_srcdoc(self):
        uri = self.rpc("resources/list")["result"]["resources"][0]["uri"]
        res = self.rpc("resources/read", {"uri": uri})["result"]["contents"][0]
        self.assertEqual(res["mimeType"], "text/html;profile=mcp-app")
        self.assertIn("ui/initialize", res["text"])
        self.assertIn("f.srcdoc = rendererText", res["text"])
        self.assertNotIn("createObjectURL", res["text"])

    def test_build_a_report_and_save_a_layout(self):
        out = self.tool("open_report", {"title": "Q3 close"})
        self.assertEqual(out["structuredContent"]["report"], "q3-close")
        self.tool("add_text", {"text": "First"})
        self.tool("add_text", {"text": "Second", "row": 1})
        rep = self.tool("get_report")["structuredContent"]["report"]
        self.assertEqual([[c["text"] for c in r["cells"]] for r in rep["rows"]], [["First", "Second"]])
        self.assertTrue(self.tool("get_report", {"since": rep["rev"]})["structuredContent"]["unchanged"])
        a, b = rep["rows"][0]["cells"]
        saved = self.tool("save_layout", {"rev": rep["rev"], "title": "Q3 close, final", "rows": [{"cells": [b]}, {"cells": [a]}]})
        self.assertEqual(saved["structuredContent"]["rev"], rep["rev"] + 1)
        stale = self.tool("save_layout", {"rev": rep["rev"], "rows": []})
        self.assertTrue(stale["isError"])
        self.assertTrue(stale["structuredContent"]["conflict"])
        with open(os.path.join(self.tmp, "reports", "q3-close", "report.zbreport")) as f:
            disk = json.load(f)
        self.assertEqual(disk["title"], "Q3 close, final")
        self.assertEqual([r["cells"][0]["text"] for r in disk["rows"]], ["Second", "First"])

    def test_chart_from_cache_and_failed_chart_leaves_report_alone(self):
        self.tool("open_report", {"title": "Charts"})
        cache(os.path.join(self.tmp, "reports", "charts"), "sales-by-region", ARGS, endpoint=FAKE_MCP)
        added = self.tool("add_chart", {"chart": ARGS})
        self.assertFalse(added.get("isError"), added)
        self.assertIn("Rendered a variance table.", added["content"][0]["text"])
        got = self.tool("get_report")["structuredContent"]
        self.assertEqual(got["results"]["sales-by-region"], RESULT)
        other = dict(ARGS, title="Not cached")
        failed = self.tool("add_chart", {"chart": other})  # no cache, closed port: not drawn, not added
        self.assertTrue(failed["isError"])
        self.assertEqual(list(self.tool("get_report")["structuredContent"]["report"]["visuals"]), ["sales-by-region"])

    def test_every_process_follows_the_report_opened_last(self):
        self.tool("open_report", {"title": "First"})
        sidebar = Proc(self.tmp)  # the builder in the sidebar runs in its own process
        try:
            self.assertEqual(sidebar.tool("get_report")["structuredContent"]["report"]["title"], "First")
            sidebar.tool("open_report", {"title": "Second"})
            self.assertEqual(self.tool("get_report")["structuredContent"]["report"]["title"], "Second")
        finally:
            sidebar.close()

    def test_unknown_method_and_tool(self):
        self.assertEqual(self.rpc("nope")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "nope"})["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
