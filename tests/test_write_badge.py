"""Exact latest-bucket W badge; all subprocesses use disposable data/config.

No Claude messages, production ledgers, installed status line, or network access.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_history import BASE, BUCKET_MS, ROOT, event
from test_original_ui import PALETTE, SGR, visible_styles
from cache_history import History, bridge
from cache_sources import parse_feed
from statusline_input import VIEW_AXIS, history_fields, input_fields, latest_bucket_write, saved_history_fields

BAR = re.compile(r"[▁▂▃▄▅▆▇█]")


def old_view(write=555000, quality="exact", percentage="95%", codes=None):
    """A same-revision legacy view with no newly persisted badge field."""
    return {"axis": VIEW_AXIS, "percentage": percentage,
            "codes": codes or ["-"] * 11 + ["6"], "revision": 1,
            "buckets": [{"bucket": BASE // BUCKET_MS, "quality": quality, "write": write}]}


class WriteBadgeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cockpit-write-badge-")
        self.path = Path(self.tmp.name)
        for name in ("home", "config", "projects", "state"):
            (self.path / name).mkdir()
        self.feed = self.path / "feed.jsonl"
        self.feed.write_text("")
        self.db = self.path / "state/history.sqlite3"
        self.env = {**os.environ, "HOME": str(self.path / "home"),
                    "XDG_STATE_HOME": str(self.path / "state"),
                    "CLAUDE_CONFIG_DIR": str(self.path / "config"),
                    "CACHE_TRANSCRIPT_ROOT": str(self.path / "projects"),
                    "CACHE_HISTORY_DB": str(self.db), "CACHE_EVENT_FILE": str(self.feed),
                    "CACHE_HISTORY_NOW": str((BASE + 100000) / 1000),
                    "CACHE_HISTORY_PYTHON": sys.executable,
                    "CACHE_HOST_MARGIN": "4", "CACHE_HISTORY_DEBUG": "", "LC_ALL": "C",
                    "COLORTERM": "truecolor", "TERM_PROGRAM": "", "TERM": "xterm-256color",
                    "COLUMNS": "120", "PYTHONDONTWRITEBYTECODE": "1"}
        for name in ("CACHE_SCOPE_PROJECT", "CACHE_SCOPE_SESSION", "CACHE_SCOPE_CONVERSATION"):
            self.env.pop(name, None)
        self.payload = {"model": {"display_name": "Badge Test (1M context)"},
                        "effort": {"level": "high"},
                        "context_window": {"total_input_tokens": 85000,
                                           "context_window_size": 200000,
                                           "used_percentage": 42.5},
                        "workspace": {"current_dir": str(self.path)},
                        "cost": {"total_cost_usd": 1.25}}

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, *rows):
        with self.feed.open("a") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    def saved_view(self, view, scope=None):
        key = json.dumps(scope or {}, sort_keys=True, separators=(",", ":"))
        Path(str(self.db) + ".view.json").write_text(json.dumps({key: view}))

    def fallback_script(self, helper=None):
        """A copied shell with a fallback parser; optional legacy helper fixture."""
        bundle = self.path / ("legacy-bundle" if helper is not None else "fallback-bundle")
        (bundle / "lib").mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "statusline-command.sh", bundle / "statusline-command.sh")
        shutil.copy2(ROOT / "lib/statusline_input.py", bundle / "lib/statusline_input.py")
        if helper is not None:
            (bundle / "lib/cache_history.py").write_text(helper)
        return bundle / "statusline-command.sh"

    def render(self, script=None, **env):
        result = subprocess.run(["/bin/bash", str(script or ROOT / "statusline-command.sh")],
                                input=json.dumps(self.payload), capture_output=True, text=True,
                                env={**self.env, **env}, check=True)
        self.assertEqual(result.stderr, "")
        plain = SGR.sub("", result.stdout)
        if int(env.get("COLUMNS", self.env["COLUMNS"])) >= 80:
            self.assertEqual(plain.splitlines()[0].count("tok 85k/200k (42.5%)"), 1)
        self.assertEqual(plain.splitlines()[-1], str(self.path))
        return result.stdout

    def cache(self, output):
        return "\n".join(output.splitlines()[1:-1])

    def test_pure_extraction_rejects_unknown_and_invalid_without_poisoning_existing_fields(self):
        self.assertEqual(latest_bucket_write(None), -1)
        self.assertEqual(latest_bucket_write({}), -1)
        for buckets in (None, [], {}, [None], ["wrong"],
                        [{"quality": "exact", "write": 17}],
                        [{"quality": "exact", "bucket": True, "write": 17}],
                        [{"quality": "exact", "bucket": 1.0, "write": 17}],
                        [{"quality": "exact", "bucket": None, "write": 17}],
                        [{"quality": "exact", "bucket": "123", "write": 17}],
                        [{"quality": "exact", "bucket": -1, "write": 17}]):
            with self.subTest(buckets=buckets):
                view = old_view()
                view["buckets"] = buckets
                self.assertEqual(latest_bucket_write(view), -1)
                self.assertEqual(history_fields(view), ["95%", "-"*11+"6", "-1"])
        for quality in ("padding", "unknown", "coarse", "partial", None):
            self.assertEqual(latest_bucket_write(old_view(0, quality)), -1)
        for write in (None, True, False, -1, "0", 1.5, [], {}):
            self.assertEqual(latest_bucket_write(old_view(write)), -1)
        self.assertEqual(latest_bucket_write(old_view(0)), 0)
        self.assertEqual(latest_bucket_write(old_view(1234)), 1234)
        view = old_view(1234)
        view["buckets"][-1]["bucket"] = 0
        self.assertEqual(latest_bucket_write(view), 1234)
        view = old_view(0)
        view["buckets"].insert(0, {"bucket": None, "quality": "unknown", "write": 99999})
        self.assertEqual(history_fields(view), ["95%", "-"*11+"6", "0"])
        view["codes"] = ["!"] * 12
        self.assertEqual(history_fields(view), ["?%", "?"*12, "-1"])

    def test_same_bucket_sum_deduplicates_and_idle_freezes_every_byte(self):
        first = event("first", read=80, write=20000, uncached=20)
        self.append(first)
        one = self.render()
        self.assertEqual(bridge(self.payload, self.env)[8:], ["0.4%", "-"*11+"0", "20000"])
        self.assertTrue(SGR.sub("", self.cache(one)).endswith("   💭 20k"))
        self.append(event("second", BASE + 1, read=80, write=35000, uncached=20, seq=2),
                    event("third", BASE + 2, read=80, write=5000, uncached=20, seq=3))
        total = self.render()
        raw = bridge(self.payload, self.env)
        self.assertEqual(len(raw), 11)
        self.assertEqual(raw[-1], "60000")
        self.assertEqual(SGR.sub("", self.cache(total)).split("   💭 ")[-1], "60k")
        self.assertEqual("".join(BAR.findall(self.cache(one)))[:-1],
                         "".join(BAR.findall(self.cache(total)))[:-1])
        self.append(event("first", read=80, write=20000, uncached=20, seq=4))
        self.assertEqual(self.render(), total)
        for seconds in (300, 3600, 86400):
            self.assertEqual(self.render(CACHE_HISTORY_NOW=str(BASE/1000 + seconds)), total)

    def test_new_active_bucket_moves_one_slot_and_scope_matches_bar(self):
        self.append(event("older", read=100, write=60000, uncached=0,
                          project_id="p1", session_id="s1"))
        self.render()
        self.append(event("new", BASE + 5*BUCKET_MS, read=100, write=0, uncached=0,
                          project_id="p2", session_id="s2", seq=2))
        new_time = str((BASE + 5*BUCKET_MS + 100000) / 1000)
        shared = bridge(self.payload, {**self.env, "CACHE_HISTORY_NOW": new_time})
        self.assertEqual(shared[8:], ["0.3%", "-"*10+"07", "0"])
        row = SGR.sub("", self.cache(self.render(CACHE_HISTORY_NOW=new_time)))
        self.assertEqual("".join(BAR.findall(row)), "▁"*11 + "█")
        self.assertTrue(row.endswith("   💭 0"))
        one = bridge(self.payload, {**self.env, "CACHE_HISTORY_NOW": new_time,
                                    "CACHE_SCOPE_PROJECT": "p1", "CACHE_SCOPE_SESSION": "s1"})
        two = bridge(self.payload, {**self.env, "CACHE_HISTORY_NOW": new_time,
                                    "CACHE_SCOPE_PROJECT": "p2", "CACHE_SCOPE_SESSION": "s2"})
        self.assertEqual(one[8:], ["0.2%", "-"*11+"0", "60000"])
        self.assertEqual(two[8:], ["100%", "-"*11+"7", "0"])
        self.assertTrue(SGR.sub("", self.cache(self.render(CACHE_HISTORY_NOW=new_time,
                         CACHE_SCOPE_PROJECT="p1", CACHE_SCOPE_SESSION="s1"))).endswith("   💭 60k"))
        self.assertTrue(SGR.sub("", self.cache(self.render(CACHE_HISTORY_NOW=new_time,
                         CACHE_SCOPE_PROJECT="p2", CACHE_SCOPE_SESSION="s2"))).endswith("   💭 0"))

    def test_coarse_bucket_never_fakes_zero_but_confirmed_zero_shows(self):
        self.append(event("baseline", kind="counter", counter_epoch="e", request_count=1,
                          read=0, write=100, uncached=0),
                    event("coarse", BASE + BUCKET_MS, kind="counter", counter_epoch="e",
                          request_count=2, read=0, write=500, uncached=0, seq=2))
        now = str((BASE + BUCKET_MS + 100000) / 1000)
        raw = bridge(self.payload, {**self.env, "CACHE_HISTORY_NOW": now})
        self.assertEqual(raw[-1], "-1")
        sidecar = json.loads(Path(str(self.db) + ".view.json").read_text())["{}"]
        self.assertEqual(sidecar["write"], 400)
        self.assertEqual(sidecar["buckets"][-1]["write"], 0)
        self.assertEqual(sidecar["buckets"][-1]["quality"], "unknown")
        self.assertNotIn("💭", self.cache(self.render(CACHE_HISTORY_NOW=now)))
        self.append(event("exact-zero", BASE + 2*BUCKET_MS, read=100, write=0,
                          uncached=0, seq=3))
        now = str((BASE + 2*BUCKET_MS + 100000) / 1000)
        self.assertEqual(bridge(self.payload, {**self.env, "CACHE_HISTORY_NOW": now})[-1], "0")
        self.assertTrue(SGR.sub("", self.cache(self.render(CACHE_HISTORY_NOW=now))).endswith("   💭 0"))

    def test_same_revision_old_sqlite_view_first_read_without_new_request(self):
        h = History(self.db)
        try:
            with h.transaction():
                h.put(parse_feed(event("legacy", read=1045, write=555000, uncached=0),
                                 1, BASE + 100000))
                view = h.view()
            revision = view["revision"]
            self.assertNotIn("last_write", view)
            self.assertEqual(view["buckets"][-1]["write"], 555000)
        finally:
            h.close()
        raw = bridge(self.payload, self.env)
        self.assertEqual(raw[-1], "555000")
        self.assertEqual(saved_history_fields(self.env)[-1], "555000")
        h = History(self.db)
        try:
            self.assertEqual(h.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0], revision)
            cached = json.loads(h.db.execute("SELECT data FROM views WHERE scope_key='{}'").fetchone()[0])
            self.assertNotIn("last_write", cached)
        finally:
            h.close()

    def test_sidecar_fallback_old_view_and_invalid_bucket_preserve_original_codes(self):
        view = old_view(555000)
        self.saved_view(view)
        fallback = self.fallback_script()
        self.assertEqual(saved_history_fields(self.env), ["95%", "-"*11+"6", "555000"])
        normal = self.render(script=fallback)
        self.assertEqual(SGR.sub("", self.cache(normal)),
                         "▲ hit 95.0%  " + "▁"*11+"▇   💭 555k")
        # An invalid feed forces the live helper to use the same committed sidecar.
        self.feed.write_text("{invalid-json}\n")
        failed = self.render()
        self.assertEqual(self.cache(failed), self.cache(normal))
        view["buckets"][-1]["quality"] = "unknown"
        self.saved_view(view)
        unknown = self.render(script=fallback)
        self.assertEqual(SGR.sub("", self.cache(unknown)), "▲ hit 95.0%  " + "▁"*11+"▇")
        self.assertEqual(saved_history_fields(self.env), ["95%", "-"*11+"6", "-1"])

    def test_helper_contract_payload_and_three_history_fields_on_every_path(self):
        self.append(event("known", write=555000))
        fields = bridge(self.payload, self.env)
        self.assertEqual(len(fields), 11)
        self.assertEqual(fields[:8], input_fields(self.payload))
        self.assertEqual(fields[10], "555000")
        sidecar = Path(str(self.db) + ".view.json")
        # SQLite cannot open a directory as its database. The same saved view
        # must still give exactly the normal 8+3 fields, without writes to it.
        unavailable = self.path / "unavailable-db"
        unavailable.mkdir()
        saved = Path(str(unavailable) + ".view.json")
        saved.write_bytes(sidecar.read_bytes())
        env = {**self.env, "CACHE_HISTORY_DB": str(unavailable)}
        self.assertEqual(bridge(self.payload, env), fields)
        for saved_content, expected in ((saved.read_bytes(), fields[8:]),
                                        (b"{}", ["?%", "?"*12, "-1"]),
                                        (b"not-json", ["?%", "?"*12, "-1"])):
            saved.write_bytes(saved_content)
            for helper in ("cache_history.py", "statusline_input.py"):
                result = subprocess.run([sys.executable, str(ROOT / "lib" / helper)],
                                        input=json.dumps(self.payload), capture_output=True,
                                        text=True, env=env, check=True)
                self.assertEqual(result.stderr, "")
                rows = result.stdout.splitlines()
                self.assertEqual(len(rows), 11)
                self.assertEqual(rows[:8], input_fields(self.payload))
                self.assertEqual(rows[8:], expected)

    def test_shared_and_filtered_latest_bucket_sum_includes_each_unique_scope(self):
        self.append(event("same-id", write=20000, project_id="p1", session_id="s1"),
                    event("same-id", write=35000, project_id="p2", session_id="s2"),
                    event("same-id", write=5000, project_id="p1", session_id="s1",
                          conversation_scope="subagent:child"))
        scopes = (({}, "60000"), ({"CACHE_SCOPE_PROJECT": "p1"}, "25000"),
                  ({"CACHE_SCOPE_SESSION": "s2"}, "35000"),
                  ({"CACHE_SCOPE_CONVERSATION": "main"}, "55000"),
                  ({"CACHE_SCOPE_PROJECT": "p1", "CACHE_SCOPE_SESSION": "s1",
                    "CACHE_SCOPE_CONVERSATION": "subagent:child"}, "5000"))
        for scope, expected in scopes:
            env = {**self.env, **scope}
            actual = bridge(self.payload, env)
            self.assertEqual(actual[10], expected)
            # Normal DB and standalone fallback both select the identical scope.
            self.assertEqual(saved_history_fields(env), actual[8:])
            self.assertEqual(len(actual[9]), 12)

    def test_badge_is_only_an_append_and_all_twelve_glyph_colors_are_preserved(self):
        fallback = self.fallback_script()
        codes = list("012345677?--")
        rgb = ((245, 201, 181), (225, 205, 190), (202, 210, 200), (178, 213, 209),
               (172, 218, 216), (187, 225, 223), (201, 231, 230), (216, 238, 237))
        indexed = (223, 223, 187, 151, 115, 115, 152, 152)
        for tc in (True, False):
            color = {"COLORTERM": "truecolor" if tc else "", "TERM_PROGRAM": "", "TERM": "xterm-256color"}
            separator = "   💭 "
            for width in (5, 10, 16, 24, 25, 26, 29, 37, 38, 80, 140):
                with self.subTest(tc=tc, width=width):
                    self.saved_view(old_view(555000, quality="unknown", codes=codes))
                    without = self.render(script=fallback, COLUMNS=str(width), **color).splitlines()
                    self.saved_view(old_view(555000, codes=codes))
                    with_badge = self.render(script=fallback, COLUMNS=str(width), **color).splitlines()
                    self.assertEqual(with_badge[0], without[0])
                    self.assertEqual(with_badge[-1], without[-1])
                    self.assertEqual(len(with_badge), len(without))
                    for before, after in zip(without[1:-1], with_badge[1:-1]):
                        self.assertEqual(after.split(separator, 1)[0], before)
                    bars = [(ch, style) for ch, style in visible_styles("\n".join(with_badge[1:-1]))
                            if BAR.fullmatch(ch)]
                    expected = []
                    for code in codes:
                        i = int(code) if code.isdigit() else 0
                        style = ("\x1b[38;2;%d;%d;%dm" % rgb[i] if tc else "\x1b[38;5;%dm" % indexed[i])
                        expected.append((chr(0x2581 + i), style))
                    self.assertEqual(bars, expected)

    def test_missing_helper_or_old_ten_field_helper_hides_only_badge(self):
        fallback = self.fallback_script()
        self.assertNotIn("💭", self.cache(self.render(script=fallback)))
        # Simulate a deployed old helper: exact original 8+2 field contract.
        fields = ["Badge Test (1M context)", "high", "85000", "200000", "42.5", "43",
                  "1.25", str(self.path), "95%", "-"*11+"6"]
        legacy = self.fallback_script(helper="print('\\n'.join(%r))\n" % fields)
        plain = SGR.sub("", self.cache(self.render(script=legacy)))
        self.assertEqual(plain, "▲ hit 95.0%  " + "▁"*11+"▇")

    def test_format_boundaries_exact_spaces_and_per_character_colors(self):
        fallback = self.fallback_script()
        cases = ((0, "0"), (999, "999"), (1000, "1k"), (1234, "1.2k"),
                 (555000, "555k"), (999999, "1000k"), (1000000, "1M"),
                 (1200000, "1.2M"), (123456789, "123.5M"))
        for indexed, palette in ((False, PALETTE["truecolor"]), (True, PALETTE["indexed"])):
            color = {"COLORTERM": "", "TERM_PROGRAM": "", "TERM": "xterm-256color"} if indexed else {
                "COLORTERM": "truecolor", "TERM_PROGRAM": "", "TERM": "xterm-256color"}
            for write, text in cases:
                with self.subTest(indexed=indexed, write=write):
                    self.saved_view(old_view(write))
                    raw = self.cache(self.render(script=fallback, **color))
                    plain = SGR.sub("", raw)
                    original = "▲ hit 95.0%  " + "▁"*11+"▇"
                    self.assertEqual(plain, original + "   💭 " + text)
                    self.assertEqual(plain[len(original):len(original)+3], "   ")
                    self.assertEqual([ord(c) for c in plain[len(original):len(original)+3]], [32, 32, 32])
                    self.assertEqual(plain[len(original)+3:len(original)+5], "💭 ")
                    tokens = visible_styles(raw)
                    emoji = next(i for i, (ch, _) in enumerate(tokens) if ch == "💭")
                    self.assertEqual(tokens[emoji-3:emoji+2],
                                     [(" ", "\x1b[0m")]*3 + [("💭", "\x1b[0m"), (" ", "\x1b[0m")])
                    self.assertNotIn("|", plain)
                    number = text[:-1] if text.endswith(("k", "M")) else text
                    self.assertEqual(tokens[emoji+2:emoji+2+len(number)],
                                     [(ch, palette["WRITE_NUM_C"]) for ch in number])
                    if text.endswith(("k", "M")):
                        unit_role = "WRITE_K_C" if text.endswith("k") else "WRITE_UNIT_C"
                        self.assertEqual(tokens[emoji+2+len(number)], (text[-1], palette[unit_role]))
                    else:
                        self.assertEqual(len(tokens), emoji+2+len(number))
                    if indexed:
                        self.assertNotIn("38;2", raw)

    def test_three_plain_spaces_no_pipe_or_style_leak(self):
        fallback = self.fallback_script()
        self.saved_view(old_view(555000))
        for mode in ("truecolor", "indexed"):
            with self.subTest(mode=mode):
                color = {"COLORTERM": "truecolor" if mode == "truecolor" else "",
                         "TERM_PROGRAM": "", "TERM": "xterm-256color"}
                output = self.render(script=fallback, **color)
                row = self.cache(output)
                tokens = visible_styles(row)
                emoji = next(i for i, (ch, _) in enumerate(tokens) if ch == "💭")
                self.assertEqual(tokens[emoji-3:emoji+2],
                                 [(" ", "\x1b[0m")]*3 + [("💭", "\x1b[0m"), (" ", "\x1b[0m")])
                self.assertNotIn("|", SGR.sub("", row))
                # Narrow/unknown output must not leave a partial tail or spaces.
                original = "▲ hit 95.0%  " + "▁"*11 + "▇"
                narrow = self.cache(self.render(script=fallback, COLUMNS="38", **color))
                self.assertEqual(SGR.sub("", narrow), original)
                self.assertNotIn("💭", narrow)
                self.saved_view(old_view(555000, quality="unknown"))
                unknown = self.cache(self.render(script=fallback, **color))
                self.assertEqual(SGR.sub("", unknown), original)
                self.assertNotIn("💭", unknown)
                self.saved_view(old_view(555000))

    def test_requested_number_and_k_colors_keep_M_and_other_UI_unchanged(self):
        fallback = self.fallback_script()
        colors = (("truecolor", "\x1b[1;38;2;35;136;168m", "\x1b[1;38;2;247;214;79m",
                   "\x1b[1;38;2;230;173;53m"),
                  ("indexed", "\x1b[1;38;5;31m", "\x1b[1;38;5;221m", "\x1b[1;38;5;178m"))
        for mode, number_color, k_color, m_color in colors:
            color = {"COLORTERM": "truecolor" if mode == "truecolor" else "",
                     "TERM_PROGRAM": "", "TERM": "xterm-256color"}
            self.assertEqual(PALETTE[mode]["WRITE_NUM_C"], number_color)
            self.assertEqual(PALETTE[mode]["WRITE_K_C"], k_color)
            self.assertEqual(PALETTE[mode]["WRITE_UNIT_C"], m_color)
            for write, number, unit in ((1234, "1.2", "k"), (1200000, "1.2", "M"),
                                        (999, "999", "")):
                with self.subTest(mode=mode, write=write):
                    self.saved_view(old_view(write, quality="unknown"))
                    without = self.render(script=fallback, **color).splitlines()
                    self.saved_view(old_view(write))
                    with_badge = self.render(script=fallback, **color).splitlines()
                    self.assertEqual(with_badge[0], without[0])
                    self.assertEqual(with_badge[-1], without[-1])
                    expected = "   💭 " + number_color + number + "\x1b[0m"
                    if unit:
                        expected += (k_color if unit == "k" else m_color) + unit + "\x1b[0m"
                    self.assertEqual(with_badge[1], without[1] + expected)

    def test_exact_fit_and_narrow_wrapping_never_shed_original_ui(self):
        fallback = self.fallback_script()
        original = "▲ hit 95.0%  " + "▁"*11+"▇"  # exactly 25 columns
        for write, text, boundary in ((555000, "555k", 35), (999999, "1000k", 36)):
            self.saved_view(old_view(write))
            for content_cols in (25, boundary-1, boundary, boundary+1, 20, 21, 22, 1, 6, 10):
                for locale in ("C", "en_US.UTF-8"):
                    with self.subTest(write=write, content_cols=content_cols, locale=locale):
                        raw = self.cache(self.render(script=fallback, COLUMNS=str(content_cols+4), LC_ALL=locale))
                        plain = SGR.sub("", raw)
                        has_badge = "💭" in plain
                        if content_cols >= 25:
                            expected = content_cols >= boundary
                            self.assertEqual(plain, original + ("   💭 " + text if expected else ""))
                        else:
                            last_col = 12 % content_cols or content_cols
                            expected = last_col + 6 + len(text) <= content_cols
                            self.assertEqual(has_badge, expected)
                            prefix = plain.split("   💭 ", 1)[0]
                            self.assertEqual("".join(BAR.findall(prefix)), "▁"*11+"▇")
                            self.assertEqual("".join(prefix.split()).replace(" ", ""),
                                             "".join(original.split()).replace(" ", ""))
                            if expected:
                                self.assertTrue(plain.endswith("   💭 " + text))
                        self.assertEqual(len(BAR.findall(plain)), 12)
                        # Emoji occupies two columns; ANSI does not. No host-side clipping assumed.
                        for line in plain.splitlines():
                            self.assertLessEqual(len(line) + line.count("💭"), content_cols)
        # The one-column-short boundary has no tail spaces or partial numeric value.
        self.saved_view(old_view(555000))
        for width in (37, 38):
            self.assertEqual(SGR.sub("", self.cache(self.render(script=fallback, COLUMNS=str(width)))), original)
        for width in (39, 40, 47):
            self.assertEqual(SGR.sub("", self.cache(self.render(script=fallback, COLUMNS=str(width)))), original+"   💭 555k")


if __name__ == "__main__":
    unittest.main()
