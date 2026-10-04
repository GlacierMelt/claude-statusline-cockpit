"""Compatible app updates must work without release allowlists or lost checkpoints."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_history import BASE, BUCKET_MS, transcript
from cache_sources import Excluded, Issue, Record, TOKEN_KEYS, parse_feed, parse_transcript
from cache_history import History, TRANSCRIPT_CONTRACT_POLICY, bridge


class ContractCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cockpit-contract-")
        self.path = Path(self.tmp.name)
        self.h = History(self.path / "history.sqlite3")
        self.source = self.path / "source.jsonl"

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def write(self, *rows):
        self.source.write_text("".join(json.dumps(row)+"\n" for row in rows))

    def ingest(self, **kwargs):
        with self.h.transaction():
            self.h.ingest_file(self.source, "transcript", BASE+365*86_400_000,
                               "p", "main", **kwargs)
            return self.h.view()

    def legacy_adapter(self, row, project, scope, seq, now):
        # Reproduce the actual former gate. This is not the new validator.
        rec = parse_transcript(row, project, scope, seq, now)
        if isinstance(rec, Record) and row.get("version") in ("2.1.289", "3.0.0"):
            return Issue("unsupported_transcript_version", rec.event_ms,
                         rec.project_id, rec.session_id, rec.conversation_scope)
        return rec

    def legacy_ingest(self):
        with patch("cache_history.parse_transcript", side_effect=self.legacy_adapter):
            before = self.ingest()
        self.h.db.execute("DELETE FROM transcript_replays")  # old DB had no policy rows
        return before

    def test_future_versions_same_contract_are_accepted(self):
        for version in ("2.1.289", "2.1.290", "2.2.0", "3.0.0", "99.0.1-beta", None):
            with self.subTest(version=version):
                row = transcript("msg", version=version)
                row["future_field"] = {"safe_extra": True}
                row["message"]["future_metadata"] = [1,2,3]
                row["message"]["usage"]["cache_creation"] = {"ephemeral_1h_input_tokens":0}
                rec = parse_transcript(row,"p","main",1,BASE+1)
                self.assertIsInstance(rec,Record)
                self.assertEqual(rec.usage,dict(zip(TOKEN_KEYS,(90,0,10))))
                self.assertEqual(rec.source_version,version or "unreported")
        row = transcript("without-version")
        row.pop("version")
        self.assertEqual(parse_transcript(row,"p","main",1,BASE+1).source_version,"unreported")

    def test_changed_required_fields_do_not_get_guessed_from_aliases(self):
        for field in ("id", "role", "stop_reason", "usage"):
            row=transcript("msg",version="99.0.0")
            row["message"]["future_"+field]=row["message"].pop(field)
            self.assertIsInstance(parse_transcript(row,"p","main",1,BASE+1),Issue)
        row=transcript("msg",version="99.0.0")
        row["message"]["stop_reason"]="new_unknown_finish"
        self.assertEqual(parse_transcript(row,"p","main",1,BASE+1).code,"unsupported_stop_reason")

    def test_malformed_usage_is_rejected_in_any_version(self):
        for version in ("2.1.288", "2.1.289", "3.0.0"):
            for n in (-1, True, 1.0, "10", None, 2**53):
                row=transcript("msg",version=version)
                row["message"]["usage"]["input_tokens"]=n
                self.assertEqual(parse_transcript(row,"p","main",1,BASE+1).code,"invalid_usage")
            row=transcript("msg",version=version)
            row["message"]["usage"].pop("cache_creation_input_tokens")
            self.assertIsInstance(parse_transcript(row,"p","main",1,BASE+1),Issue)

    def test_identity_time_role_and_completion_still_fail_closed(self):
        changes=(lambda r:r.update(sessionId=""),lambda r:r.update(timestamp="no timezone"),
                 lambda r:r["message"].update(id="bad\x1bidentity"),
                 lambda r:r["message"].update(role="user"),
                 lambda r:r.update(session_id="conflicts"))
        for change in changes:
            row=transcript("msg",version="3.0.0");change(row)
            self.assertIsInstance(parse_transcript(row,"p","main",1,BASE+1),Issue)

    def test_client_errors_in_future_versions_never_become_requests(self):
        for version in ("2.1.289", "99.0.0"):
            row=transcript("err",version=version,isApiErrorMessage=True)
            self.assertIsInstance(parse_transcript(row,"p","main",1,BASE+1),Excluded)
            row.pop("isApiErrorMessage");row["message"]["model"]="<synthetic>"
            self.assertIsInstance(parse_transcript(row,"p","main",1,BASE+1),Excluded)

    def test_null_stop_remains_pending_until_complete_not_stream_chunk_addition(self):
        start=transcript("stream",version="3.0.0",stop=None)
        final=transcript("stream",BASE+1,version="3.0.0")
        self.write(start)
        view=self.ingest()
        self.assertEqual(view["active_bucket_count"],0)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM pending").fetchone()[0],1)
        with self.source.open("a") as stream:stream.write(json.dumps(final)+"\n")
        view=self.ingest()
        self.assertEqual(view["read"],90)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0],1)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM pending").fetchone()[0],0)

    def test_checkpoint_eof_recovery_retains_raw_issues_and_deduplicates(self):
        first=transcript("old",usage=dict(zip(TOKEN_KEYS,(100,0,0))))
        new=transcript("new",BASE+100*BUCKET_MS,version="2.1.289")
        duplicate=copy.deepcopy(new);duplicate["timestamp"]=transcript("",BASE+100*BUCKET_MS+1)["timestamp"]
        self.write(first,new,duplicate)
        before=self.legacy_ingest()
        cp=tuple(self.h.db.execute("SELECT * FROM checkpoints").fetchone())
        old_rows=[tuple(x) for x in self.h.db.execute("SELECT * FROM events")]
        raw_issues=[tuple(x) for x in self.h.db.execute("SELECT * FROM issues ORDER BY sequence")]
        self.assertEqual(cp[3],self.source.stat().st_size)
        after=self.ingest()
        self.assertEqual(after["active_bucket_count"],2)
        self.assertEqual(after["codes"],before["codes"][1:]+["4"])
        self.assertEqual(after["percentage"],"95%")
        self.assertEqual(after["quality"],"exact")
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0],2)
        self.assertTrue(set(old_rows).issubset({tuple(x) for x in self.h.db.execute("SELECT * FROM events")}))
        self.assertEqual(raw_issues,[tuple(x) for x in self.h.db.execute("SELECT * FROM issues ORDER BY sequence")])
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM resolved_issues").fetchone()[0],2)
        self.assertEqual(tuple(self.h.db.execute("SELECT * FROM checkpoints").fetchone()),cp)
        with patch("cache_history.parse_transcript",wraps=parse_transcript) as parser:
            self.assertEqual(self.ingest(),after)
            self.assertEqual(parser.call_count,0)  # replay once, then incremental EOF
        self.h.close();self.h=History(self.path/"history.sqlite3")
        self.assertEqual(self.ingest(),after)

    def test_future_release_after_replay_is_automatically_incremental(self):
        self.write(transcript("old",usage=dict(zip(TOKEN_KEYS,(80,0,20)))))
        before=self.ingest()
        with self.source.open("a") as f:
            f.write(json.dumps(transcript("future",BASE+1,version="3.0.0",usage=dict(zip(TOKEN_KEYS,(920,0,0)))))+"\n")
        after=self.ingest()
        self.assertEqual(after["codes"][:-1],before["codes"][:-1])
        self.assertEqual((before["codes"][-1],after["codes"][-1]),("0","7"))
        self.assertEqual(after["percentage"],"98%")
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM issues").fetchone()[0],0)

    def test_policy_bump_recovers_previously_crossed_source_without_clearing_state(self):
        self.write(transcript("old"),transcript("new",BASE+BUCKET_MS,version="3.0.0"))
        before=self.legacy_ingest()
        self.h.db.execute("INSERT INTO transcript_replays VALUES (?,?)",(str(self.source.resolve()),TRANSCRIPT_CONTRACT_POLICY-1))
        after=self.ingest()
        self.assertEqual(after["active_bucket_count"],before["active_bucket_count"]+1)
        self.assertEqual(self.h.db.execute("SELECT policy FROM transcript_replays").fetchone()[0],TRANSCRIPT_CONTRACT_POLICY)

    def test_recovery_failure_rolls_back_event_issue_resolution_and_checkpoint(self):
        self.write(transcript("old"),transcript("new",BASE+BUCKET_MS,version="2.1.289"))
        before=self.legacy_ingest()
        cp=tuple(self.h.db.execute("SELECT * FROM checkpoints").fetchone())
        def fail(phase):
            if phase=="before_checkpoint":raise RuntimeError("fixture failure")
        with self.assertRaises(RuntimeError):self.ingest(failpoint=fail)
        self.assertEqual(tuple(self.h.db.execute("SELECT * FROM checkpoints").fetchone()),cp)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0],1)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM resolved_issues").fetchone()[0],0)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM transcript_replays").fetchone()[0],0)
        with self.h.transaction():self.assertEqual(self.h.view(),before)
        self.assertEqual(self.ingest()["active_bucket_count"],2)

    def test_complete_malformed_line_rolls_back_recovery(self):
        self.write(transcript("old"),transcript("new",BASE+BUCKET_MS,version="2.1.289"))
        self.legacy_ingest()
        with self.source.open("a") as f:f.write("not-json\n")
        with self.assertRaises(ValueError):self.ingest()
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0],1)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM resolved_issues").fetchone()[0],0)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM transcript_replays").fetchone()[0],0)

    def test_unrecoverable_changed_contract_keeps_actionable_diagnostic(self):
        row=transcript("bad",BASE+BUCKET_MS,version="99.0.0")
        row["message"]["usage"]={"new_counter_format":123}
        self.write(transcript("old"),row)
        self.ingest()
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0],1)
        self.assertEqual(self.h.db.execute("SELECT code FROM issues").fetchone()[0],"invalid_usage")
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM resolved_issues").fetchone()[0],0)

    def test_resolution_requires_matching_scope_time_line_and_final_weights(self):
        row=transcript("valid",version="3.0.0")
        rec=parse_transcript(row,"p","main",1,BASE+1)
        with self.h.transaction():
            self.h.put(rec)
            for line,code,time,session in ((1,"unsupported_transcript_version",BASE+1,"s"),
                (1,"invalid_usage",BASE,"another"),(1,"conflicting_final_usage",BASE,"s")):
                self.h.issue(self.source,line,Issue(code,time,"p",session,"main"))
            self.assertEqual(self.h.resolve_transcript_issues(self.source,1,rec),0)
            self.assertEqual(self.h.db.execute("SELECT count(*) FROM resolved_issues").fetchone()[0],0)

    def test_already_booked_recovery_invalidates_saved_quality_without_new_tokens(self):
        row=transcript("valid",version="3.0.0")
        rec=parse_transcript(row,"p","main",1,BASE+1)
        with self.h.transaction():
            self.h.put(rec)
            self.h.issue(self.source,1,Issue("unsupported_transcript_version",BASE,"p","s","main"))
            before=self.h.view()
            self.assertEqual(before["quality"],"partial")
            self.assertEqual(self.h.resolve_transcript_issues(self.source,1,rec),1)
            after=self.h.view()
            self.assertEqual(after["quality"],"exact")
            self.assertGreater(after["revision"],before["revision"])
            self.assertEqual(after["read"],before["read"])
            self.assertEqual(self.h.resolve_transcript_issues(self.source,1,rec),0)
            self.assertEqual(self.h.view(),after)

    def test_future_quarantine_survives_replay_without_releasing_it(self):
        row=transcript("future",BASE+BUCKET_MS,version="3.0.0")
        self.write(row)
        with self.h.transaction():
            self.h.ingest_file(self.source,"transcript",BASE,"p","main")
            before=self.h.view()
        self.h.db.execute("DELETE FROM transcript_replays")
        self.assertEqual(self.ingest(),before)
        self.assertEqual(self.h.db.execute("SELECT count(*) FROM events").fetchone()[0],0)
        self.assertEqual(self.h.db.execute("SELECT reason FROM quarantined").fetchone()[0],"future_event")

    def test_explicit_feed_contract_remains_versioned(self):
        self.assertIsInstance(parse_feed({"schema":"cockpit-events/v999"},1,BASE),Issue)
        self.assertIsNone(parse_transcript({"type":"system","version":"3.0.0"},"p","main",1,BASE))


if __name__=="__main__":unittest.main()
