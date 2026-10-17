"""Synthetic regressions for 2.0 collection, deadline and grading accuracy."""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "学习控制台"))
import canvas_sync
import grades
import server


def course(cid, code, **extra):
    return {"id": cid, "code": code, "name": code, "term": "Synthetic term",
            "start": None, "end": None, **extra}


class CollectionReliabilityChecks(unittest.TestCase):
    def collect(self, observed, *, failure=False, previous_owner=9, assignments=()):
        state = {
            "config.json": {"user": {"id": previous_owner}, "courses": [course(1, "A"), course(2, "B")]},
            "snapshot.json": {"assignments": {"20": {"course": "B", "name": "Synthetic deadline", "sub_state": "unsubmitted"}},
                              "complete": True, "collected_at": "2026-10-17T00:00:00+00:00"},
            "assignment-meta.json": {"20": {"assignment_group_id": 5}},
            "announcements.json": []}
        class API:
            def get(self, path):
                if failure and "/courses/2/assignments" in path:
                    raise RuntimeError("synthetic endpoint failure")
                return list(assignments) if "/assignments?" in path else []
        writes = []
        def save(path, value):
            state[path.name] = value
            writes.append(path.name)
        with patch.object(canvas_sync, "read_json", side_effect=lambda p, default: state.get(p.name, default)), \
             patch.object(canvas_sync, "save_private", side_effect=save), \
             patch.object(canvas_sync, "current_courses", return_value=observed):
            result = canvas_sync.collect(API(), Path("/synthetic-no-files"), {"id": 9})
        return state, result, writes

    def test_partial_roster_retains_verified_course_and_cached_assignment(self):
        state, (config, warnings), _ = self.collect([course(1, "A", rosterIncomplete=True)], failure=True)
        self.assertEqual([c["code"] for c in config["courses"]], ["A", "B"])
        self.assertTrue(config["courses"][1]["rosterCached"])
        self.assertTrue(state["snapshot.json"]["assignments"]["20"]["cached"])
        self.assertEqual(state["assignment-meta.json"]["20"]["assignment_group_id"], 5)
        self.assertFalse(state["snapshot.json"]["complete"])
        self.assertTrue(any("缓存名单" in warning for warning in warnings))

    def test_complete_roster_can_remove_departed_course(self):
        state, (config, warnings), _ = self.collect([course(1, "A")])
        self.assertEqual([c["code"] for c in config["courses"]], ["A"])
        self.assertNotIn("20", state["snapshot.json"]["assignments"])
        self.assertTrue(state["snapshot.json"]["complete"])

    def test_renamed_course_retains_legacy_assignment_by_course_id(self):
        state, _, _ = self.collect([course(1, "A"), course(2, "B-new")], failure=True)
        assignment = state["snapshot.json"]["assignments"]["20"]
        self.assertEqual((assignment["course"], assignment["courseId"]), ("B-new", "2"))
        self.assertTrue(assignment["cached"])

    def test_partial_roster_without_retained_courses_does_not_claim_cache(self):
        _, (_, warnings), _ = self.collect([course(1, "A", rosterIncomplete=True), course(2, "B", rosterIncomplete=True)])
        self.assertTrue(any("仅含已发现" in warning for warning in warnings))
        self.assertFalse(any("已保留" in warning for warning in warnings))

    def test_missing_or_unrecognized_submission_state_remains_unknown(self):
        for submission in (None, {}, {"workflow_state": "unexpected"}):
            with self.subTest(submission=submission):
                state, _, _ = self.collect([course(1, "A")], assignments=[{"id": 1, "submission": submission}])
                assignment = state["snapshot.json"]["assignments"]["1"]
                self.assertEqual(assignment["sub_state"], "unknown")
                self.assertFalse(assignment["submission_state_observed"])

    def test_explicit_unsubmitted_and_submitted_at_remain_observed(self):
        for submission, expected in (({"workflow_state": "unsubmitted"}, "unsubmitted"),
                                     ({"submitted_at": "2026-10-17T00:00:00Z"}, "submitted")):
            with self.subTest(submission=submission):
                state, _, _ = self.collect([course(1, "A")], assignments=[{"id": 1, "submission": submission}])
                assignment = state["snapshot.json"]["assignments"]["1"]
                self.assertEqual(assignment["sub_state"], expected)
                self.assertTrue(assignment["submission_state_observed"])

    def test_other_account_cannot_reuse_previous_roster(self):
        with self.assertRaisesRegex(RuntimeError, "账号.*不一致"):
            self.collect([course(1, "A", rosterIncomplete=True)], previous_owner=8)


class DeadlineReliabilityChecks(unittest.TestCase):
    now = dt.datetime(2026, 10, 18, 12, 0, tzinfo=server.HK)

    def event(self, **extra):
        return {"id": "synthetic", "date": "2026-10-17", "daysLeft": -1,
                "kind": "assignment", "source": "canvas", "submissionState": "unsubmitted",
                "submissionStateObserved": True, "status": "未交", "pending": False, "time": "", **extra}

    def test_canvas_overdue_has_explicit_last_observed_status(self):
        upcoming, overdue = server.split_deadline_events([self.event()], self.now)
        self.assertFalse(upcoming)
        self.assertEqual(overdue[0]["overdueState"], "canvas_unsubmitted")
        self.assertIn("上次同步", overdue[0]["overdueLabel"])

    def test_date_only_items_wait_until_next_day_and_same_day_deadline_is_exact(self):
        rows = [
            self.event(id="date-only", date="2026-10-18", daysLeft=0),
            self.event(id="future", date="2026-10-18", daysLeft=0, time="13:00"),
            self.event(id="passed", date="2026-10-18", daysLeft=0, time="11:00"),
            self.event(id="seconds", date="2026-10-18", daysLeft=0, time="11:59", dueAt="2026-10-18T12:00:30+08:00")]
        upcoming, overdue = server.split_deadline_events(rows, self.now)
        self.assertEqual([r["id"] for r in upcoming], ["date-only", "future", "seconds"])
        self.assertEqual([r["id"] for r in overdue], ["passed"])

    def test_cache_invalidates_once_at_known_deadline_without_periodic_rebuild(self):
        before = {"data": {"synthetic": True}, "validUntil": self.now + dt.timedelta(seconds=1)}
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return DeadlineReliabilityChecks.now
        with patch.dict(server.DASHBOARD_CACHE, before, clear=True), \
             patch.object(server, "datetime", Clock), \
             patch.object(server, "DEADLINE_REVISION", 0), \
             patch.object(server, "connection_data", return_value={}), \
             patch.object(server.runtime_cache, "revision", side_effect=lambda profile, epoch, day: epoch):
            initial = server.status_data()["revision"]
            self.assertEqual(server.DASHBOARD_CACHE["data"], {"synthetic": True})
            server.DASHBOARD_CACHE["validUntil"] = self.now
            expired = server.status_data()["revision"]
            self.assertNotEqual(initial, expired)
            self.assertEqual(server.DASHBOARD_CACHE, {})
            self.assertEqual(server.status_data()["revision"], expired)

    def test_cache_deadline_crossed_during_build_invalidates_on_next_poll(self):
        due = self.now + dt.timedelta(seconds=1)
        current = [self.now]
        row = self.event(date="2026-10-18", daysLeft=0, dueAt=due.isoformat())
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return current[0]
        def build():
            result = {"events": [row] if current[0] < due else []}
            current[0] = due + dt.timedelta(seconds=1)
            return result
        with patch.dict(server.DASHBOARD_CACHE, {}, clear=True), \
             patch.object(server, "datetime", Clock), \
             patch.object(server, "DEADLINE_REVISION", 0), \
             patch.object(server, "connection_data", return_value={}), \
             patch.object(server, "dashboard_data", side_effect=build) as builder, \
             patch.object(server.runtime_cache, "signature", return_value=None), \
             patch.object(server.runtime_cache, "revision", side_effect=lambda profile, epoch, day: epoch):
            initial = server.cached_dashboard()
            self.assertEqual(server.DASHBOARD_CACHE["validUntil"], due)
            expired = server.status_data()["revision"]
            self.assertNotEqual(initial["revision"], expired)
            self.assertEqual(server.cached_dashboard()["events"], [])
            self.assertIsNone(server.DASHBOARD_CACHE["validUntil"])
            self.assertEqual(server.status_data()["revision"], expired)
            self.assertEqual(builder.call_count, 2)

    def test_unknown_and_legacy_submission_states_do_not_claim_explicit_unsubmitted(self):
        rows = [self.event(id="unknown", submissionState="unknown", submissionStateObserved=False),
                self.event(id="legacy", submissionStateObserved=None)]
        _, overdue = server.split_deadline_events(rows, self.now)
        self.assertEqual([row["overdueState"] for row in overdue], ["date_passed", "date_passed"])

    def test_documents_never_claim_canvas_submission_status(self):
        _, overdue = server.split_deadline_events([self.event(source="documents", submissionState=None)], self.now)
        self.assertEqual(overdue[0]["overdueState"], "date_passed")
        self.assertIn("待核对", overdue[0]["overdueLabel"])

    def test_exams_completed_submitted_and_uncertain_events_are_not_overdue(self):
        rows = [self.event(kind="final"), self.event(status="已完成"),
                self.event(submissionState="submitted"), self.event(pending=True)]
        self.assertEqual(server.split_deadline_events(rows, self.now), ([], []))

    def test_future_completed_or_manually_linked_submitted_items_are_not_pending(self):
        rows = [self.event(date="2026-10-19", daysLeft=1, source="manual", status="已完成"),
                self.event(date="2026-10-19", daysLeft=1, source="manual", submissionState="submitted")]
        self.assertEqual(server.split_deadline_events(rows, self.now), ([], []))

    def test_submitted_assignment_does_not_reappear_from_document_evidence(self):
        storage = {
            "config.json": {"user": {"id": 9}, "courses": [course(1, "SYNTH")]},
            "state.json": {},
            "snapshot.json": {"assignments": {"4": {"course": "SYNTH", "name": "Essay", "sub_state": "submitted",
                           "due_at": "2026-10-17T08:00:00+00:00"}}}}
        evidence = {"deadlines": [{"id": "doc-essay", "canvasAssignmentId": "4", "course": "SYNTH",
                    "title": "Essay", "date": "2026-10-17", "kind": "assignment", "pattern": "Essay"}]}
        with patch.object(server, "NATIVE", True), \
             patch.object(server, "load_json", side_effect=lambda p, default: storage.get(p.name, default)), \
             patch.object(server, "connection_data", return_value={"authorized": True}), \
             patch.object(server, "public_summary", return_value={"evidence": evidence}), \
             patch.object(server, "build_grades", return_value={}), \
             patch.object(server.academic_calendar, "month_summary", return_value={}), \
             patch.object(server.timetable, "query", return_value={}):
            data = server.dashboard_data()
        self.assertFalse(data["events"])
        self.assertFalse(data["overdue"])


class GradeReliabilityChecks(unittest.TestCase):
    def result(self, mode, **extra):
        spec = {"name": "Tests", "pattern": ".*", "weight": 40, "mode": mode,
                "equalItems": True, "expectedCount": 2, "canvasGroupId": 7, **extra}
        rows = [("1", {"name": "Test 1", "sub_state": "graded", "score": 80, "points": 100,
                       "course": "SYNTH", "html_url": ""})]
        return grades.component_data(spec, rows, lambda url: url, True, {"1": {"assignment_group_id": 7}})

    def test_show_only_equal_items_cannot_bypass_complex_policy(self):
        result = self.result("show_only")
        self.assertIsNone(result["weighted"])
        self.assertIsNone(result["assessedWeight"])

    def test_complex_group_cannot_use_equal_item_exception(self):
        self.assertIsNone(self.result("group_all", complex=True)["weighted"])

    def test_complete_complex_group_cannot_fold_either(self):
        spec = {"name": "Tests", "pattern": ".*", "weight": 40, "mode": "group_all",
                "complex": True, "equalItems": True, "expectedCount": 1}
        rows = [("1", {"name": "Test 1", "sub_state": "graded", "score": 80, "points": 100})]
        result = grades.component_data(spec, rows, lambda url: url, True, {})
        self.assertIsNone(result["weighted"])
        self.assertIsNone(result["assessedWeight"])

    def test_verified_equal_items_still_contribute_independently(self):
        result = self.result("group_all")
        self.assertEqual(result["weighted"], 16)
        self.assertEqual(result["assessedWeight"], 20)


if __name__ == "__main__":
    unittest.main()
