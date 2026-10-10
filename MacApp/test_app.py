"""Account isolation, read-only collection, migration, and deadline checks."""
import io
import json
import sys
import subprocess
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "学习控制台"))
import server
from canvas_api import Canvas, CanvasPagingError
from canvas_sync import collect
from canvas_web import all_courses, course_code_from_home, dashboard_courses, identity, linked_files_and_pages, module_files
from canvas_archive import archive_courses, safe_component
from canvas_documents import scan_courses, public_summary
from document_deadlines import reconcile
from native_canvas import HOST, material_host, normalized_cookies, save_private, session_class


class ApplicationChecks(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "Mac native isolation validation requires Swift and macOS")
    def test_native_test_override_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory(prefix="polyu-isolation-compiler-") as temporary:
            binary = Path(temporary) / "isolation-checks"
            subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-module-cache-path", str(Path(temporary) / "cache"),
                            str(ROOT / "MacApp/TestIsolation.swift"), str(ROOT / "验证/IsolationTests.swift"), "-o", str(binary)],
                           check=True, capture_output=True, text=True, timeout=90)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=20)
            self.assertIn("ISOLATION_CHECKS=12", result.stdout)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="polyu-app-check-")
        self.project = Path(self.temp.name)
        self.profile = self.project / "account"
        self.patch = patch.multiple(server, PROJECT=self.project, PROFILE=self.profile,
                                    NATIVE=True, NATIVE_SECRET="test-native-secret")
        self.patch.start()
        self.job = server.JOB.copy()
        server.JOB.update(busy=False, stage="", lastError="", needsLogin=False)

    def tearDown(self):
        server.JOB.clear()
        server.JOB.update(self.job)
        self.patch.stop()
        self.temp.cleanup()

    def test_fresh_install_is_empty_and_has_no_weekly_report(self):
        data = server.dashboard_data()
        self.assertEqual(data["courseCount"], 0)
        self.assertEqual(data["grades"]["courses"], [])
        self.assertFalse(data["connection"]["authorized"])
        self.assertNotIn("days", data)
        self.assertNotIn("weekItems", data)
        self.assertNotIn("hasWeekReport", data)

    def test_cookies_stay_on_canvas(self):
        cookies = normalized_cookies([
            {"name": "s", "value": "ok", "domain": ".polyu.edu.hk", "expires": -1},
            {"name": "SSO", "value": "secret", "domain": "login.microsoftonline.com"},
            {"name": "expired", "value": "old", "domain": "canvas.polyu.edu.hk", "expires": 1},
            {"name": "bad", "value": "value\r\nheader", "domain": "canvas.polyu.edu.hk"}])
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["domain"], "canvas.polyu.edu.hk")

    def test_transport_reads_canvas_guarded_json(self):
        client = session_class()(HOST, self.profile, cookies=[], persist=False)
        response = Mock()
        response.headers = Message()
        response.read.return_value = b'while(1);{"id":123}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(client.opener, "open", return_value=response):
            self.assertEqual(client.get("/api/v1/users/self")["id"], 123)
        self.assertFalse(self.profile.exists())

    def test_pagination_is_complete_and_rejects_external_next_link(self):
        client = Canvas(HOST)
        first = Message()
        first["Link"] = '<https://canvas.polyu.edu.hk/api/v1/courses?page=2>; rel="next"'
        with patch.object(client, "fetch", side_effect=[(first, b'[{"id":1}]'), (Message(), b'[{"id":2}]')]) as fetch:
            self.assertEqual([x["id"] for x in client.get("/api/v1/courses")], [1, 2])
            self.assertEqual(fetch.call_count, 2)
        outside = Message()
        outside["Link"] = '<https://example.com/api/v1/courses>; rel="next"'
        with patch.object(client, "fetch", return_value=(outside, b'[]')):
            with self.assertRaises(ValueError):
                client.get("/api/v1/courses")

    def test_canvas_page_read_retries_after_timeout(self):
        client = Canvas(HOST, retries=1)
        stalled = Mock()
        stalled.read.side_effect = TimeoutError("The read operation timed out")
        stalled.__enter__ = Mock(return_value=stalled)
        stalled.__exit__ = Mock(return_value=False)
        ready = Mock()
        ready.headers = Message()
        ready.read.return_value = b"<html>Canvas</html>"
        ready.__enter__ = Mock(return_value=ready)
        ready.__exit__ = Mock(return_value=False)
        with patch.object(client.opener, "open", side_effect=[stalled, ready]) as fetch, patch("canvas_api.time.sleep"):
            self.assertEqual(client.fetch(HOST + "/courses/5128", "text/html")[1], b"<html>Canvas</html>")
        self.assertEqual(fetch.call_count, 2)

    def test_canvas_web_finds_visible_course_and_module_folder(self):
        dashboard = '''<script>window.ENV={"current_user_id":"55247"}</script>
          <a class="ic-DashboardCard__link" href="/courses/5125"><h2 class="ic-DashboardCard__header-title" title="Ecological Approaches"></h2>
          <div class="ic-DashboardCard__header-subtitle" title="ABCT5031_26271_A"></div></a>'''
        self.assertEqual(identity(dashboard)["id"], "55247")
        self.assertEqual(dashboard_courses(dashboard)[0]["code"], "ABCT5031_26271_A")
        modules = '''<script>window.ENV={"current_user_id":"55247"}</script>
          <div class="context_module" aria-label="Class Materials" data-module-id="14948"><ul>
          <li class="context_module_item Attachment_146703"><a class="item_link" title="ABCT5031_Course Outline.pdf" href="/courses/5125/modules/items/76244">outline</a></li>
          <li class="context_module_item Attachment_146704"><a class="item_link" title="Teaching schedule for ABCT5031_2026-27.pdf" href="/courses/5125/modules/items/77776">schedule</a></li>
          <li class="context_module_item Attachment_146705"><a class="item_link" title="Group Assignment for ABCT5031_2026-27_v2.pdf" href="/courses/5125/modules/items/98883">assignment</a></li>
          </ul></div>'''
        rows = module_files(modules, "5125")
        self.assertEqual([r["id"] for r in rows], ["146703", "146704", "146705"])
        self.assertEqual({r["folder"] for r in rows}, {"Class Materials"})
        self.assertEqual(rows[0]["downloadUrl"], HOST + "/courses/5125/files/146703/download?download_frd=1")
        self.assertEqual(safe_component("../evil.pdf", "x"), "_evil.pdf")
        self.assertTrue(material_host("https://a29159-134527.cluster42.canvas-user-content.com/courses/file"))
        self.assertTrue(material_host("https://cdn.inst-fs-sin-prod.inscloudgate.net/courses/file"))
        self.assertFalse(material_host("https://canvas-user-content.com.evil.example/file"))

    def test_all_courses_includes_non_favorite_and_page_attachments(self):
        dashboard = '''<a class="ic-DashboardCard__link" href="/courses/5125"><h2 class="ic-DashboardCard__header-title" title="Course One"></h2><div class="ic-DashboardCard__header-subtitle" title="ABCT5031"></div></a>'''
        listing = '''<table id="my_courses_table"><tr class="course-list-table-row"><th class="course-list-course-title-column"><a href="/courses/5125" title="Course One">Course One</a></th><td class="course-list-term-column">2026/27</td></tr><tr class="course-list-table-row"><th class="course-list-course-title-column"><a href="/courses/6000" title="Other Course">Other Course</a></th></tr></table>'''
        rows = all_courses(listing, dashboard)
        self.assertEqual([(r["id"], r["code"]) for r in rows], [("5125", "ABCT5031"), ("6000", "6000")])
        files, pages = linked_files_and_pages('''<main><a href="/courses/5125/files/99/download?download_frd=1" title="Download Notice.pdf">Download Notice.pdf</a><img src="/courses/5125/files/100/preview" alt="Figure.png"><a href="/courses/5125/announcements/32">Announcement</a></main>''', "5125")
        self.assertEqual({row["id"] for row in files}, {"99", "100"})
        self.assertEqual(pages, ["/courses/5125/announcements/32"])
        self.assertEqual(course_code_from_home('<li id="crumb_course_5125"><a href="/courses/5125"><span>ABCT5031_26271_A</span></a></li>', "5125"), "ABCT5031_26271_A")

    def test_archive_failure_is_visible_and_never_reports_complete(self):
        html = '''<script>{"current_user_id":"1"}</script><div class="context_module" aria-label="Class Materials"><li class="context_module_item Attachment_99"><a class="item_link" title="Exam.pdf" href="/courses/5/modules/items/1">Exam.pdf</a></li></div>'''
        headers = Message(); headers["Content-Type"] = "text/html"
        api = Mock()
        api.fetch.return_value = (headers, html.encode())
        api.download_file.side_effect = RuntimeError("server unavailable")
        cfg = {"user": {"id": 1}, "courses": [{"id": 5, "code": "A5", "name": "Course"}]}
        archive = archive_courses(api, self.profile, cfg, lambda: None, lambda _: None)
        self.assertFalse(archive["complete"])
        self.assertEqual(archive["entries"][0]["status"], "failed")
        self.assertFalse(public_summary(self.profile)["downloadComplete"])

    def test_download_finishes_before_document_interpretation(self):
        html = '''<script>window.ENV={"current_user_id":"1"}</script>
          <div class="context_module" aria-label="Class Materials"><ul>
          <li class="context_module_item Attachment_703"><a class="item_link" title="Outline.pdf" href="/courses/5125/modules/items/44">Outline.pdf</a></li>
          </ul></div>'''
        headers = Message(); headers["Content-Type"] = "text/html; charset=utf-8"
        api = Mock()
        api.fetch.return_value = (headers, html.encode())
        def download(url, destination, maximum=None, cancelled=lambda: None):
            self.assertIn("/files/703/download", url)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"%PDF-test")
            self.assertFalse((self.profile / "documents/index.json").exists())
            return 9
        api.download_file.side_effect = download
        api.get.side_effect = RuntimeError("API unavailable")
        cfg = {"user": {"id": 1}, "courses": [{"id": 5125, "code": "ABCT5031", "name": "Course"}]}
        archive = archive_courses(api, self.profile, cfg, lambda: None, lambda _: None)
        self.assertTrue(archive["complete"])
        self.assertEqual(archive["courses"][0]["downloaded"], 1)
        path = self.profile / "documents/files" / archive["entries"][0]["paths"][0]
        self.assertTrue(path.is_file())
        page_fetches = api.fetch.call_count
        with patch("canvas_documents.extract", return_value=[{"page": 1, "text": "Midterm 13 Oct 2026", "rows": ["Midterm 13 Oct 2026"], "readable": True, "processed": True}]) as reader:
            scan_courses(api, self.profile, cfg, None, lambda: None, lambda _: None, archive=archive)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(api.fetch.call_count, page_fetches)
        self.assertEqual(api.download_file.call_count, 1)
        summary = public_summary(self.profile)
        self.assertTrue(summary["downloadComplete"])
        self.assertEqual(summary["downloadedFiles"], 1)
        self.assertEqual(summary["downloadEntries"][0]["folders"], ["Class Materials"])

    def test_rejects_session_from_a_different_account(self):
        save_private(self.profile / "config.json", {"user": {"id": 1}})
        save_private(self.profile / "browser/canvas-cookies.json", [{"original": True}])
        fake = Mock()
        fake.get.return_value = {"id": 2, "name": "Other"}
        with patch.object(server, "native_api", return_value=fake):
            with self.assertRaisesRegex(ValueError, "另一账号"):
                server.connect_native({"cookies": [{"name": "s", "value": "new", "domain": "canvas.polyu.edu.hk"}]})
        self.assertEqual(json.loads((self.profile / "browser/canvas-cookies.json").read_text()), [{"original": True}])
        self.assertFalse((self.profile / "login.json").exists())

    def test_first_connection_keeps_auto_sync_preference(self):
        fake = Mock()
        fake.get.return_value = {"id": 1, "name": "Student"}
        save_private(self.profile / "app-settings.json", {"autoSync": False})
        with patch.object(server, "native_api", return_value=fake):
            server.connect_native({"cookies": [{"name": "s", "value": "new", "domain": "canvas.polyu.edu.hk"}]})
        self.assertFalse(server.connection_data()["autoSync"])
        self.assertTrue(server.connection_data()["authorized"])
        self.assertEqual(json.loads((self.profile / "state.json").read_text()), {"manual_deadlines": []})

    def test_collect_preserves_other_courses_after_partial_failure(self):
        def get(path):
            if path.startswith("/api/v1/courses?"):
                return [{"id": 11, "course_code": "A11", "name": "Course A"},
                        {"id": 22, "course_code": "B22", "name": "Course B"}]
            if "/11/assignments" in path:
                return [{"id": 7, "name": "Quiz 1", "quiz_id": 5, "points_possible": 20,
                         "submission": {"workflow_state": "graded", "score": 18, "grade_hidden": False}}]
            if "/22/assignments" in path:
                raise RuntimeError("endpoint unavailable")
            if "announcements" in path:
                return []
            raise AssertionError(path)
        old = {"assignments": {"8": {"course": "B22", "name": "Previous", "due_at": None}}}
        save_private(self.profile / "snapshot.json", old)
        cfg, warnings = collect(Mock(get=Mock(side_effect=get)), self.profile, {"id": 1, "name": "Student"})
        snapshot = json.loads((self.profile / "snapshot.json").read_text())
        self.assertEqual(len(cfg["courses"]), 2)
        self.assertEqual(snapshot["assignments"]["7"]["score"], 18)
        self.assertIn("8", snapshot["assignments"])
        self.assertFalse(snapshot["complete"])
        self.assertEqual(len(warnings), 1)

    def test_failed_first_course_listing_does_not_claim_full_file_scan(self):
        save_private(self.profile / "config.json", {"user": {"id": 1}, "courses": []})
        save_private(self.profile / "login.json", {"user_id": 1})
        api = Mock()
        api.get.return_value = {"id": 1}
        with patch.object(server, "native_api", return_value=api), \
             patch.object(server, "collect_canvas", side_effect=RuntimeError("course list unavailable")), \
             patch.object(server, "scan_courses") as scan:
            server.rebuild()
        scan.assert_not_called()
        phases = json.loads((self.profile / "sync-status.json").read_text())["phases"]
        self.assertEqual(phases[-1]["status"], "partial")
        self.assertFalse((self.profile / "documents/index.json").exists())

    def test_migrates_legacy_account_and_removes_weekly_output(self):
        legacy = self.project / ".jiujiastudy"
        save_private(legacy / "login.json", {"user_id": 1, "transport": "webkit"})
        save_private(legacy / "config.json", {"user": {"id": 1}, "courses": [], "root": "/old"})
        save_private(legacy / "state.json", {"manual_deadlines": [
            {"item": "Keep", "source": "manual"}, {"item": "Drop", "source": "Canvas 文件核对 · old"}]})
        save_private(legacy / "raw/daily/snapshot.json", {"assignments": {}, "collected_at": "2026-10-01T00:00:00Z",
                                                              "raw_dir": "raw/daily/2026-10-01"})
        save_private(legacy / "documents/index.json", {"evidence": {}})
        (legacy / "plans").mkdir()
        (legacy / "plans/week.json").write_text("{}")
        (self.project / "救驾").mkdir()
        (self.project / "救驾/本周清单.html").write_text("old")
        server.migrate_legacy_profile()
        self.assertFalse(legacy.exists())
        self.assertFalse((self.project / "救驾").exists())
        self.assertFalse((self.profile / "plans").exists())
        self.assertTrue((self.profile / "documents/index.json").exists())
        self.assertEqual(json.loads((self.profile / "state.json").read_text())["manual_deadlines"],
                         [{"item": "Keep", "source": "manual"}])

    def test_manual_schedule_saves_without_network_or_coach(self):
        save_private(self.profile / "config.json", {"courses": [{"code": "ABCT5001"}]})
        handler = object.__new__(server.Handler)
        payload = {"course": "ABCT5001", "title": "Midterm", "date": "2026-10-13", "time": "14:00",
                   "url": HOST + "/courses/1", "weight": "20%"}
        self.assertEqual(handler.save_event(payload), "安排已保存。")
        rows = json.loads((self.profile / "state.json").read_text())["manual_deadlines"]
        self.assertEqual(rows[0]["date"], "2026-10-13")
        self.assertEqual(handler.delete_event({"course": "ABCT5001", "title": "Midterm"}), "手动安排已删除。")

    def test_document_conflict_keeps_personal_date(self):
        save_private(self.profile / "state.json", {"manual_deadlines": [{"course": "A", "item": "Exam",
            "date": "2026-10-12", "source": "manual"}]})
        index = {"evidence": {"deadlines": [{"id": "exam", "course": "A", "title": "Exam",
            "pattern": "Exam", "date": "2026-10-13", "sources": []}], "pending": []}}
        evidence = reconcile(self.profile, index)
        self.assertEqual(evidence["deadlines"], [])
        self.assertEqual(evidence["pending"][0]["reason"], "现有安排与课程文件日期不一致，需核对")
        self.assertEqual(json.loads((self.profile / "state.json").read_text())["manual_deadlines"][0]["date"], "2026-10-12")

    def test_forged_native_request_is_rejected(self):
        handler = object.__new__(server.Handler)
        handler.server = Mock(server_port=8766)
        handler.path = "/api/native/session"
        handler.headers = Message()
        handler.headers["Host"] = "127.0.0.1:8766"
        handler.headers["X-PolyU-Native"] = "forged"
        handler.send_json = Mock()
        handler.rfile = io.BytesIO(b'{}')
        handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[1], 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
