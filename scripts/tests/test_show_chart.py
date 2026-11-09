"""Tests for show_chart: one Zebra BI chart in the chat, outside any report, kept under the reports folder's
.chat-charts, drawn after sign-in, and its card page (chart_card, app only). Run: python3 -I -m unittest discover
<plugin>/scripts/tests

The fake Zebra BI MCP and authorization server of test_sign_in.py answers render_visual; nothing reads or writes the
real token store or report folder.
"""
import json, os, sys, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import report as R  # noqa: E402

R._legacy_checked = True  # a test never moves the real ~/Documents reports
import report_server as RS  # noqa: E402
import test_sign_in as T  # noqa: E402
from test_sign_in import ARGS, MONTHS, FAKE, SEEN, Base, until  # noqa: E402


def setUpModule():
    T.setUpModule()


def tearDownModule():
    T.tearDownModule()


class ShowChart(Base):
    def show(self, chart=ARGS):
        return RS.call("show_chart", {"chart": chart}, None)

    def card(self, cid):
        return RS.call("chart_card", {"chart": cid}, None)["structuredContent"]

    def test_before_sign_in_the_chart_is_kept_and_waits(self):
        out = self.show()
        self.assertFalse(out.get("isError"), out)
        said, got = out["content"][0]["text"], out["structuredContent"]
        self.assertIn("placeholder", said)
        self.assertIn('"Sign in to Zebra BI"', said)
        self.assertEqual(set(got), {"chart", "title", "drawn"})  # the model gets no rows back
        self.assertEqual((got["title"], got["drawn"]), ("Sales by region", False))
        self.assertNotIn("calls", SEEN)  # no call to Zebra BI without a sign-in
        kept = os.path.join(os.environ["ZEBRA_BI_REPORTS"], ".chat-charts", got["chart"], "chart.json")
        with open(kept, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["args"], ARGS)
        card = self.card(got["chart"])
        self.assertEqual((card["drawn"], card["result"], card["error"]), (False, None, None))
        self.assertEqual(card["ghost"], {"kind": "bars", "count": 2})
        self.assertEqual(card["auth"]["state"], "idle")
        self.assertEqual(R.reports(), [])  # not a report

    def test_after_sign_in_the_card_draws_it(self):
        cid = self.show(MONTHS)["structuredContent"]["chart"]
        self.signed_in()
        card = self.card(cid)
        self.assertTrue(card["drawn"])
        self.assertEqual(card["result"]["structuredContent"]["visual"]["title"], "Sales by month")
        self.assertEqual(card["ghost"]["kind"], "columns")
        again = self.show(MONTHS)  # the same chart shown again is the same one, drawn
        self.assertEqual(again["structuredContent"], {"chart": cid, "title": "Sales by month", "drawn": True})
        self.assertIn("Zebra BI: Drew Sales by month.", again["content"][0]["text"])
        self.assertEqual(SEEN["calls"].count("tools/call"), 1)  # drawn once, then from the cache

    def test_signed_in_it_is_drawn_at_once(self):
        self.signed_in()
        out = self.show()
        self.assertTrue(out["structuredContent"]["drawn"])
        self.assertIn("Showed 'Sales by region' in the chat", out["content"][0]["text"])
        self.assertNotEqual(self.show(MONTHS)["structuredContent"]["chart"], out["structuredContent"]["chart"])

    def test_a_licence_refusal_is_told_once_and_shown_on_the_card(self):
        self.signed_in()
        FAKE["render"] = "trial"
        out = self.show()
        self.assertFalse(out.get("isError"), out)
        self.assertIn("Zebra BI did not draw it", out["content"][0]["text"])
        self.assertNotIn("trial", out["content"][0]["text"].lower())
        card = self.card(out["structuredContent"]["chart"])
        self.assertEqual((card["drawn"], card["auth"]["refusal"]["kind"]), (False, "trial_ended"))

    def test_a_chart_zebra_bi_cannot_draw_is_an_error(self):
        self.signed_in()
        FAKE["render"] = "badargs"
        out = self.show()
        self.assertTrue(out.get("isError"))
        self.assertIn("measure.Val names a column", out["content"][0]["text"])

    def test_no_chart_no_id(self):
        self.assertTrue(RS.handle("tools/call", {"name": "show_chart", "arguments": {"chart": None}}).get("isError"))
        for bad in ("../x", "0" * 15, None):
            with self.assertRaises(ValueError):
                RS.call("chart_card", {"chart": bad}, None)
        with self.assertRaises(ValueError):
            RS.call("chart_card", {"chart": "0" * 16}, None)  # a well-formed id nobody showed

    def test_the_card_is_its_own_page_and_chart_card_is_app_only(self):
        tools = {t["name"]: t for t in RS.tools()}
        card_uri = RS.app_uri("card")
        self.assertTrue(card_uri.startswith("ui://zebra-bi/chart-card-"))
        self.assertEqual(tools["show_chart"]["_meta"]["ui"]["resourceUri"], card_uri)
        self.assertEqual(tools["show_chart"]["_meta"]["openai/outputTemplate"], card_uri)
        self.assertNotIn("openai/ui", tools["show_chart"]["_meta"])  # no sidebar entry for a chart
        self.assertIn("chart_card", RS.app_only_tools())
        self.assertNotIn("show_chart", RS.app_only_tools())
        uris = [r["uri"] for r in RS.handle("resources/list", {})["resources"]]
        self.assertEqual(uris, [RS.app_uri(), card_uri])
        page = RS.handle("resources/read", {"uri": card_uri})["contents"][0]
        self.assertIn(RS.app_key(), page["text"])
        self.assertNotIn(RS.KEY_SLOT, page["text"])
        self.assertTrue(page["_meta"]["ui"]["prefersBorder"])
        cid = self.show()["structuredContent"]["chart"]
        refused = RS.handle("tools/call", {"name": "chart_card", "arguments": {"chart": cid}})
        self.assertTrue(refused.get("isError"))  # without the key
        ok = RS.handle("tools/call", {"name": "chart_card", "arguments": {"chart": cid, "app_key": RS.app_key()}})
        self.assertFalse(ok.get("isError"), ok)

    def test_charts_nobody_showed_for_a_month_are_removed(self):
        old = self.show()["structuredContent"]["chart"]
        path = os.path.join(os.environ["ZEBRA_BI_REPORTS"], ".chat-charts", old, "chart.json")
        past = time.time() - (R.CHAT_DAYS + 1) * 86400
        os.utime(path, (past, past))
        self.show(MONTHS)
        self.assertFalse(os.path.exists(os.path.dirname(path)))

    def test_no_text_names_a_command_or_offers_a_trial(self):
        texts = [t["description"] for t in RS.tools() if t["name"] in ("show_chart", "chart_card")]
        texts.append(self.show()["content"][0]["text"])
        for t in texts:
            for word in ("story.py", "python", "terminal", "trial"):
                self.assertNotIn(word, t.lower(), t)


if __name__ == "__main__":
    unittest.main()
