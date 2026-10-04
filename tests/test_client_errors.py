"""Client errors must not meter tokens, mark uncertainty, or advance history."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_history import BASE, BUCKET_MS, event, transcript
from cache_history import History
from cache_sources import Excluded, Record, parse_feed, parse_transcript


def api_error(eid="client-error", ms=BASE + 100 * BUCKET_MS):
    row = transcript(eid, ms, stop="stop_sequence", usage={
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        "input_tokens": 0, "output_tokens": 0}, isApiErrorMessage=True,
        error="server_error", apiErrorStatus=502)
    row["message"]["model"] = "<synthetic>"
    return row


class ClientErrorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cockpit-client-error-")
        self.path = Path(self.tmp.name)
        self.h = History(self.path / "history.sqlite3")

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def accepted_old_record(self, row):
        # Reproduce the legacy bug without using the fixed exclusion branch.
        old = copy.deepcopy(row)
        old.pop("isApiErrorMessage", None)
        old["message"].pop("model", None)
        return parse_transcript(old, "p", "main", 99, BASE + 365 * 86_400_000)

    def test_explicit_error_and_synthetic_not_zero_usage_heuristic(self):
        for stop in (None, "stop_sequence", "end_turn"):
            row = api_error()
            row["message"]["stop_reason"] = stop
            self.assertIsInstance(parse_transcript(row, "p", "main", 1, BASE), Excluded)
        row = api_error()
        row.pop("isApiErrorMessage")
        row["message"]["usage"]["cache_read_input_tokens"] = 123
        self.assertEqual(parse_transcript(row, "p", "main", 1, BASE).reason,
                         "client_synthetic_message")
        for usage in ({"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                       "input_tokens": 0}, {"cache_read_input_tokens": 90,
                       "cache_creation_input_tokens": 0, "input_tokens": 10}):
            real = transcript("real", stop="stop_sequence", usage=usage,
                              isApiErrorMessage=False)
            real["message"]["model"] = "real-model-fixture"
            rec = parse_transcript(real, "p", "main", 1, BASE + 1)
            self.assertIsInstance(rec, Record)
            self.assertEqual(rec.kind, "request")
            self.assertEqual(rec.usage, usage)

    def test_error_only_no_requests_no_slots_no_revision(self):
        with self.h.transaction():
            before = self.h.view()
            self.assertFalse(self.h.put(parse_transcript(api_error(), "p", "main", 1, BASE)))
            self.assertEqual(self.h.view(), before)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM issues").fetchone()[0], 0)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM quarantined").fetchone()[0], 1)
        self.assertEqual(before["active_bucket_count"], 0)

    def test_later_api_error_leaves_all_twelve_cells_and_ratio_frozen(self):
        with self.h.transaction():
            for i in range(12):
                self.h.put(parse_feed(event(str(i), BASE+i*BUCKET_MS,
                                           read=100+i, uncached=i), i+1, BASE+100*BUCKET_MS))
            before = self.h.view()
            for i, ms in enumerate((BASE+11*BUCKET_MS+1, BASE+100*BUCKET_MS,
                                    BASE+86_400_000)):
                self.h.put(parse_transcript(api_error(str(i), ms), "p", "main", i+1,
                                            BASE+365*86_400_000))
                self.assertEqual(self.h.view(), before)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0], 12)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM issues").fetchone()[0], 0)

    def test_legacy_isolation_keeps_ledger_and_invalidates_every_scope(self):
        row = api_error("same-id")
        legacy = self.accepted_old_record(row)
        with self.h.transaction():
            good = transcript("first", usage={"cache_read_input_tokens": 100,
                              "cache_creation_input_tokens": 0, "input_tokens": 0})
            self.h.put(parse_transcript(good, "p", "main", 1, BASE+1))
            self.h.put(legacy)
            other = transcript("same-id", BASE,
                               session="other")
            self.h.put(parse_transcript(other, "p", "main", 2, BASE+1))
            before = self.h.view()
            before_session = self.h.view({"session_id": "s"})
            original = [tuple(x) for x in self.h.db.execute("SELECT * FROM events ORDER BY event_id,session_id")]
            self.assertFalse(self.h.put(parse_transcript(row, "p", "main", 99, BASE+100*BUCKET_MS)))
            after = self.h.view()
            after_session = self.h.view({"session_id": "s"})
            self.assertEqual(after["revision"], before["revision"]+1)
            self.assertEqual(after_session["revision"], before_session["revision"]+1)
            self.assertEqual(after["active_bucket_count"], 1)
            self.assertEqual(after_session["percentage"], "100%")
            self.assertEqual(after["quality"], "exact")
            self.assertEqual(after["read"], 190)  # other session's same ID is NOT excluded
            self.assertEqual(original, [tuple(x) for x in self.h.db.execute("SELECT * FROM events ORDER BY event_id,session_id")])
            self.h.put(parse_transcript(row, "p", "main", 99, BASE+100*BUCKET_MS))
            self.assertEqual(self.h.view(), after)
        self.h.close()
        self.h = History(self.path / "history.sqlite3")
        with self.h.transaction():
            self.assertEqual(self.h.view(), after)
            self.h.put(legacy)  # source rotation/replay cannot re-release it
            self.assertEqual(self.h.view(), after)

    def test_one_time_repair_uses_source_markers_not_all_zero_or_checkpoints(self):
        source = self.path / "transcript.jsonl"
        bad = api_error()
        real_zero = transcript("real-zero", BASE+BUCKET_MS, stop="stop_sequence", usage={
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "input_tokens": 0})
        source.write_text(json.dumps(real_zero)+"\n"+json.dumps(bad)+"\n")
        with self.h.transaction():
            self.h.put(self.accepted_old_record(bad))
            self.h.put(parse_transcript(real_zero, "p", "main", 1, BASE+2*BUCKET_MS))
            checkpoint = (str(source), 1, 2, source.stat().st_size, 2, 0, "head", "tail")
            self.h.db.execute("INSERT INTO checkpoints VALUES (?,?,?,?,?,?,?,?)", checkpoint)
            ledger = [tuple(x) for x in self.h.db.execute("SELECT * FROM events ORDER BY event_id")]
            before = self.h.view()
            sources = [(source, ("transcript", "p", "main"))]
            self.assertEqual(self.h.reconcile_transcript_exclusions(sources, BASE+365*86_400_000), 1)
            after = self.h.view()
            self.assertNotEqual(before["anchor_bucket"], after["anchor_bucket"])
            self.assertEqual(after["active_bucket_count"], 1)  # ordinary zero record retained
            self.assertEqual(tuple(self.h.db.execute("SELECT * FROM checkpoints").fetchone()), checkpoint)
            self.assertEqual(ledger, [tuple(x) for x in self.h.db.execute("SELECT * FROM events ORDER BY event_id")])
            self.assertEqual(self.h.reconcile_transcript_exclusions(sources, BASE+365*86_400_000), 0)
            self.assertEqual(self.h.view(), after)
        self.assertEqual(source.read_text(), json.dumps(real_zero)+"\n"+json.dumps(bad)+"\n")

    def test_partial_source_repair_is_transactional_and_does_not_mark_success(self):
        source = self.path / "transcript.jsonl"
        bad = api_error()
        source.write_text(json.dumps(bad)+"\n"+"not json\n")
        with self.h.transaction():
            self.h.put(self.accepted_old_record(bad))
            before = self.h.view()
        with self.assertRaises(ValueError):
            with self.h.transaction():
                self.h.reconcile_transcript_exclusions([(source,("transcript","p","main"))],
                                                       BASE+365*86_400_000)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM quarantined").fetchone()[0], 0)
        self.assertIsNone(self.h.db.execute("SELECT value FROM meta WHERE key='transcript_filter'").fetchone())
        with self.h.transaction():
            self.assertEqual(self.h.view(), before)

    def test_malformed_explicit_error_is_not_missing_request_evidence(self):
        row = api_error()
        row["sessionId"] = ""
        self.assertIsNone(parse_transcript(row, "p", "main", 1, BASE))
        row = api_error()
        row["version"] = "unsupported-client-version"
        row.pop("timestamp")
        self.assertIsInstance(parse_transcript(row, "p", "main", 1, BASE), Excluded)


if __name__ == "__main__":
    unittest.main()
