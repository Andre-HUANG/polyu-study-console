"""2.2 completion marks, local calendar export and grade target planning (synthetic data only)."""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '学习控制台'))
sys.path.insert(0, str(ROOT / 'WindowsApp'))
import server
import calendar_export
import runtime_cache
from grades import build_grades, target_planning
from diagnostic_log import validate_event
from native_canvas import HOST, save_private
from release_build import CONSOLE_FILES, FORBIDDEN, VERSION, BUILD, BASELINE_VERSION, BASELINE_BUILD

HK = ZoneInfo('Asia/Hong_Kong')


class V22Actions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='polyu-v22-')
        self.root = Path(self.temp.name)
        self.profile = self.root / 'account'
        self.patch = patch.multiple(server, PROJECT=self.root, PROFILE=self.profile, DIAGNOSTICS=None,
                                    ACCESS=server.LocalAccess(), NATIVE=True, NATIVE_SECRET='synthetic-native-key')
        self.patch.start()
        server.DASHBOARD_CACHE.clear()
        self.today = datetime.now(HK).date()

    def tearDown(self):
        server.DASHBOARD_CACHE.clear()
        self.patch.stop(); self.temp.cleanup()

    def day(self, offset):
        return (self.today + timedelta(days=offset)).isoformat()

    def authorize(self):
        save_private(self.profile / 'config.json', {'user': {'id': 'synthetic'}, 'courses': [
            {'id': 1, 'code': 'TEST', 'name': 'Synthetic course'}, {'id': 2, 'code': 'MORE', 'name': 'Second course'}]})
        save_private(self.profile / 'login.json', {'user_id': 'synthetic', 'transport': 'webkit'})

    def seed(self, unsubmitted=False):
        self.authorize()
        manual = [
            {'course': 'TEST', 'item': 'Reading log', 'date': self.day(3), 'time': '23:59', 'kind': 'assignment', 'url': HOST + '/courses/1', 'source': 'manual'},
            {'course': 'TEST', 'item': 'Final exam', 'date': self.day(20), 'time': '09:30', 'kind': 'final', 'url': HOST + '/courses/1', 'source': 'manual'},
            {'course': 'MORE', 'item': 'Old essay', 'date': self.day(-2), 'kind': 'assignment', 'url': HOST + '/courses/2', 'source': 'manual'},
        ]
        save_private(self.profile / 'state.json', {'manual_deadlines': manual})
        due = (datetime.now(HK) + timedelta(days=5)).replace(hour=15, minute=0, second=0, microsecond=0)
        assignment = {'course': 'MORE', 'name': 'Canvas lab report', 'due_at': due.isoformat(), 'html_url': HOST + '/courses/2/assignments/9'}
        if unsubmitted: assignment.update(sub_state='unsubmitted', submission_state_observed=True)
        save_private(self.profile / 'snapshot.json', {'assignments': {'9': assignment}})

    def handler(self, path, payload=None):
        handler = object.__new__(server.Handler)
        handler.server = Mock(server_port=8766)
        handler.path = path
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8766'
        handler.headers['Origin'] = 'http://127.0.0.1:8766'
        handler.headers['X-PolyU-Console'] = server.CSRF
        handler.headers['Cookie'] = server.ACCESS.cookie_name(8766) + '=' + server.ACCESS.token
        body = json.dumps(payload or {}).encode()
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.send_json = Mock()
        return handler

    def post(self, path, payload):
        handler = self.handler(path, payload); handler.do_POST()
        args = handler.send_json.call_args.args
        return args[0], (args[1] if len(args) > 1 else 200)

    def row(self, title, data=None):
        data = data or server.dashboard_data()
        return next(r for r in data['events'] + data['overdue'] if r['rawTitle'] == title)

    def test_release_identity_advances_from_published_baseline(self):
        self.assertEqual((VERSION, BUILD, BASELINE_VERSION, BASELINE_BUILD), ('2.2.0', '220', '2.1.0', '210'))
        self.assertIn('calendar_export.py', CONSOLE_FILES)
        self.assertTrue({'event-marks.json', 'exports', 'polyu-schedule.ics'} <= FORBIDDEN)
        self.assertFalse(set(CONSOLE_FILES) & FORBIDDEN)
        self.assertIn('event-marks.json', runtime_cache.FILES)
        for event in ('event.mark', 'calendar.export'): validate_event(event, 'success')

    def test_only_confirmed_assignments_receive_stable_mark_keys(self):
        self.seed()
        data = server.dashboard_data()
        reading, exam, old = self.row('Reading log', data), self.row('Final exam', data), self.row('Old essay', data)
        self.assertRegex(reading['markKey'], r'^[0-9a-f]{32}$')
        self.assertFalse(reading['done'])
        self.assertNotIn('markKey', exam)
        self.assertIn(old, data['overdue']); self.assertIn('markKey', old)
        # A manual key follows course/title/due moment, not the list index.
        state = json.loads((self.profile / 'state.json').read_text())
        state['manual_deadlines'].insert(0, {'course': 'TEST', 'item': 'Inserted first', 'date': self.day(1), 'kind': 'assignment', 'url': HOST + '/courses/1'})
        save_private(self.profile / 'state.json', state)
        self.assertEqual(self.row('Reading log')['markKey'], reading['markKey'])
        # Moving the due date re-surfaces a completed item.
        state['manual_deadlines'][1]['date'] = self.day(4)
        save_private(self.profile / 'state.json', state)
        self.assertNotEqual(self.row('Reading log')['markKey'], reading['markKey'])

    def test_mark_and_undo_round_trip_changes_revision_without_touching_sources(self):
        self.seed()
        key = self.row('Reading log')['markKey']
        state_before = (self.profile / 'state.json').read_bytes()
        before = server.status_data()['revision']
        result, status = self.post('/api/event/mark', {'key': key, 'done': True})
        self.assertEqual(status, 200, result)
        self.assertIn('已标记完成', result['message'])
        self.assertNotEqual(server.status_data()['revision'], before)
        marked = self.row('Reading log', result['data'])
        self.assertTrue(marked['done']); self.assertTrue(marked['doneAt'])
        self.assertEqual((self.profile / 'state.json').read_bytes(), state_before)
        if sys.platform != 'win32': self.assertEqual(oct((self.profile / 'event-marks.json').stat().st_mode & 0o777), '0o600')
        result, status = self.post('/api/event/mark', {'key': key, 'done': False})
        self.assertEqual(status, 200, result)
        self.assertFalse(self.row('Reading log', result['data'])['done'])
        result, status = self.post('/api/event/mark', {'key': key, 'done': False})
        self.assertEqual(status, 400)

    def test_mark_rejects_malformed_unknown_exam_and_pending_requests(self):
        self.seed()
        key = self.row('Reading log')['markKey']
        for payload in ({'key': key}, {'key': key, 'done': 'yes'}, {'key': key, 'done': True, 'extra': 1},
                        {'key': key.upper(), 'done': True}, {'key': 'a' * 31, 'done': True},
                        {'key': key, 'done': True, 'confirm': 1}, {'key': '0' * 32, 'done': True}):
            with self.subTest(payload=payload):
                result, status = self.post('/api/event/mark', payload)
                self.assertEqual(status, 400, result)
        self.assertFalse((self.profile / 'event-marks.json').exists())
        exam = self.row('Final exam')
        forged = server.mark_key(exam)
        result, status = self.post('/api/event/mark', {'key': forged, 'done': True})
        self.assertEqual(status, 400)
        self.assertFalse((self.profile / 'event-marks.json').exists())

    def test_explicit_canvas_unsubmitted_requires_confirmation_and_stays_visible(self):
        self.seed(unsubmitted=True)
        row = self.row('Canvas lab report')
        result, status = self.post('/api/event/mark', {'key': row['markKey'], 'done': True})
        self.assertEqual(status, 400)
        self.assertIn('Canvas', result['error'])
        self.assertFalse((self.profile / 'event-marks.json').exists())
        result, status = self.post('/api/event/mark', {'key': row['markKey'], 'done': True, 'confirm': True})
        self.assertEqual(status, 200, result)
        marked = self.row('Canvas lab report', result['data'])
        self.assertTrue(marked['done'])
        # The personal mark never rewrites Canvas submission evidence.
        self.assertEqual(marked['submissionState'], 'unsubmitted')
        self.assertTrue(marked['submissionStateObserved'])

    def test_marks_require_authorized_account_and_are_removed_on_logout(self):
        self.seed()
        key = self.row('Reading log')['markKey']
        self.post('/api/event/mark', {'key': key, 'done': True})
        self.post('/api/export/calendar', {'ids': [self.row('Reading log')['id']]})
        self.assertTrue((self.profile / 'exports' / calendar_export.FILENAME).exists())
        server.logout_native()
        self.assertFalse((self.profile / 'event-marks.json').exists())
        self.assertFalse((self.profile / 'exports').exists())
        result, status = self.post('/api/event/mark', {'key': key, 'done': True})
        self.assertEqual(status, 403)
        self.assertEqual(server.dashboard_data()['events'], [])

    def test_mark_store_is_bounded_and_ignores_corrupt_entries(self):
        self.seed()
        marks = {f'{i:032x}': {'at': f'2026-01-01T00:{i // 60:02d}:{i % 60:02d}+08:00'} for i in range(server.MARK_LIMIT)}
        marks['not-a-key'] = {'at': 'x'}; marks['f' * 32] = 'bad'
        save_private(self.profile / 'event-marks.json', {'version': 1, 'marks': marks})
        self.assertEqual(len(server.load_marks()), server.MARK_LIMIT)
        key = self.row('Reading log')['markKey']
        result, status = self.post('/api/event/mark', {'key': key, 'done': True})
        self.assertEqual(status, 200, result)
        saved = json.loads((self.profile / 'event-marks.json').read_text())['marks']
        self.assertEqual(len(saved), server.MARK_LIMIT)
        self.assertIn(key, saved)
        self.assertNotIn(f'{0:032x}', saved)

    def test_calendar_export_writes_private_rfc5545_file_for_requested_upcoming_rows(self):
        self.seed()
        data = server.dashboard_data()
        state = json.loads((self.profile / 'state.json').read_text())
        state['manual_deadlines'].append({'course': 'TEST', 'item': 'Studio, review; part\\2', 'date': self.day(6), 'kind': 'assignment',
                                          'url': HOST + '/courses/1', 'note': '带上\n模型', 'source': 'manual'})
        save_private(self.profile / 'state.json', state)
        data = server.dashboard_data()
        ids = [r['id'] for r in data['events']] + [self.row('Old essay', data)['id'], 'canvas:unknown']
        result, status = self.post('/api/export/calendar', {'ids': ids})
        self.assertEqual(status, 200, result)
        self.assertIn('已导出 4 项', result['message'])
        path = self.profile / 'exports' / calendar_export.FILENAME
        if sys.platform != 'win32': self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')
        body = path.read_bytes()
        self.assertTrue(body.startswith(b'BEGIN:VCALENDAR\r\n') and body.endswith(b'END:VCALENDAR\r\n'))
        self.assertNotIn(b'\n', body.replace(b'\r\n', b''))
        self.assertTrue(all(len(line) <= 75 for line in body.split(b'\r\n')))
        text = body.decode('utf-8').replace('\r\n ', '')
        self.assertEqual(text.count('BEGIN:VEVENT'), 4)
        self.assertNotIn('Old essay', text)
        reading = datetime.fromisoformat(self.day(3) + 'T23:59').replace(tzinfo=HK)
        self.assertIn('DTSTART:' + reading.astimezone(ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ'), text)
        all_day = self.today + timedelta(days=6)
        self.assertIn('DTSTART;VALUE=DATE:' + all_day.strftime('%Y%m%d'), text)
        self.assertIn('DTEND;VALUE=DATE:' + (all_day + timedelta(days=1)).strftime('%Y%m%d'), text)
        self.assertIn('Studio\\, review\\; part\\\\2', text)
        self.assertIn('带上\\n模型', text)
        self.assertIn('TRIGGER:-P1D', text)
        self.assertIn('TRIGGER:-PT15H', text)
        self.assertIn('URL:' + HOST + '/courses/1', text)
        uids = re.findall(r'UID:(\S+)', text)
        self.assertEqual(len(set(uids)), 4)
        self.post('/api/export/calendar', {'ids': ids})
        self.assertEqual(re.findall(r'UID:(\S+)', path.read_bytes().decode().replace('\r\n ', '')), uids)

    def test_calendar_export_rejects_empty_invalid_or_linked_destinations(self):
        self.seed()
        for payload in ({}, {'ids': []}, {'ids': 'x'}, {'ids': [1]}, {'ids': ['x' * 241]}, {'ids': ['a'] * 301}, {'ids': ['manual:0'], 'extra': True}):
            with self.subTest(payload=str(payload)[:40]):
                self.assertEqual(self.post('/api/export/calendar', payload)[1], 400)
        result, status = self.post('/api/export/calendar', {'ids': [self.row('Old essay')['id']]})
        self.assertEqual(status, 400)
        self.assertFalse((self.profile / 'exports').exists())
        outside = self.root / 'outside'; outside.mkdir()
        try: (self.profile / 'exports').symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest('Synthetic symlink requires extra Windows privilege')
        with self.assertRaises(RuntimeError):
            calendar_export.write(self.profile, b'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n')
        self.assertEqual(list(outside.iterdir()), [])

    def test_pending_rows_are_not_exported(self):
        self.seed()
        rows = [{'id': 'a', 'course': 'TEST', 'title': 'Uncertain', 'date': self.day(2), 'kind': 'assignment', 'pending': True},
                {'id': 'b', 'course': 'TEST', 'title': 'Confirmed exam', 'date': self.day(2), 'time': '14:30', 'endTime': '16:30', 'kind': 'final', 'location': 'Hall A'}]
        with patch.object(server, 'dashboard_data', return_value={'events': rows, 'overdue': []}):
            handler = object.__new__(server.Handler)
            message = handler.export_calendar({'ids': ['a', 'b']})
        self.assertIn('1 项日期待核对', message)
        text = (self.profile / 'exports' / calendar_export.FILENAME).read_text(encoding='utf-8')
        self.assertNotIn('Uncertain', text)
        start = datetime.fromisoformat(self.day(2) + 'T14:30').replace(tzinfo=HK).astimezone(ZoneInfo('UTC'))
        self.assertIn('DTEND:' + (start + timedelta(hours=2)).strftime('%Y%m%dT%H%M%SZ'), text)
        self.assertIn('LOCATION:Hall A', text)
        self.assertIn('（期末考试）', text)

    def test_ics_folding_never_splits_multibyte_characters(self):
        line = 'SUMMARY:' + '期末考试安排' * 30
        folded = calendar_export.fold(line)
        parts = folded.encode('utf-8').split(b'\r\n')
        self.assertTrue(all(len(p) <= 75 for p in parts))
        for p in parts: p.decode('utf-8')
        self.assertEqual(folded.replace('\r\n ', ''), line)

    def test_target_planning_only_for_coherent_complete_schemes(self):
        component = lambda name, weight, **extra: {'name': name, 'weight': weight, 'conflict': False, 'complex': False, **extra}
        good = [component('Quiz', 20), component('Project', 30), component('Exam', 50)]
        self.assertEqual(target_planning(good, True, 15.0, 20), {'totalWeight': 100, 'earned': 15.0, 'assessedWeight': 20, 'remainingWeight': 80})
        self.assertIsNone(target_planning(good, False, 15.0, 20))
        self.assertIsNone(target_planning(good, True, None, None))
        self.assertIsNone(target_planning(good, True, 90.0, 100))
        self.assertIsNone(target_planning([component('Quiz', 20), component('Exam', 60)], True, 15.0, 20))
        self.assertIsNone(target_planning([component('Quiz', 20), component('Exam', None)], True, 15.0, 20))
        self.assertIsNone(target_planning([component('Quiz', 20), component('Exam', 80, conflict=True)], True, 15.0, 20))
        self.assertIsNone(target_planning([component('Quiz', 20), component('Exam', 80, complex=True)], True, 15.0, 20))

    def test_course_grades_expose_planning_only_after_a_verified_fold(self):
        scheme = {'TEST': {'source': 'Synthetic outline', 'components': [
            {'name': 'Quiz', 'pattern': 'quiz', 'weight': 20, 'mode': 'single'},
            {'name': 'Exam', 'pattern': 'exam', 'weight': 80, 'mode': 'single'}]}}
        path = self.root / 'schemes.json'; path.write_text(json.dumps(scheme))
        assignments = {'1': {'course': 'TEST', 'name': 'Quiz 1', 'sub_state': 'graded', 'score': 18, 'points': 20},
                       '2': {'course': 'TEST', 'name': 'Final exam', 'sub_state': 'unsubmitted'}}
        course = build_grades([{'id': 1, 'code': 'TEST', 'name': 'Synthetic'}], assignments, lambda u: u or '', {}, path)['courses'][0]
        self.assertEqual(course['planning'], {'totalWeight': 100.0, 'earned': 18.0, 'assessedWeight': 20.0, 'remainingWeight': 80.0})
        scheme['TEST']['conflict'] = True; path.write_text(json.dumps(scheme))
        course = build_grades([{'id': 1, 'code': 'TEST', 'name': 'Synthetic'}], assignments, lambda u: u or '', {}, path)['courses'][0]
        self.assertIsNone(course['planning'])
        course = build_grades([{'id': 1, 'code': 'TEST', 'name': 'Synthetic'}], assignments, lambda u: u or '', {}, self.root / 'missing.json')['courses'][0]
        self.assertIsNone(course['planning'])

    def test_windows_bridge_opens_only_the_account_export_folder(self):
        import desktop
        app = Mock(stopping=False, root=self.root); app.backend.url = 'http://127.0.0.1:8766/'
        app.console.get_current_url.return_value = app.backend.url
        (self.root / 'account/exports').mkdir(parents=True)
        opened = []
        with patch.object(desktop.os, 'startfile', create=True, side_effect=opened.append), \
             patch.object(desktop, 'display_path', side_effect=lambda p: str(p)):
            self.assertEqual(desktop.Bridge(app).native_action('exportsFolder'), {'ok': True})
        self.assertEqual(opened, [str(self.root / 'account/exports')])

    def test_mac_shell_routes_export_folder_through_fixed_action(self):
        swift = (ROOT / 'MacApp/App.swift').read_text(encoding='utf-8')
        self.assertIn('case "exportsFolder": openExportsFolder()', swift)
        self.assertIn('account/exports', swift)
        self.assertIn('activateFileViewerSelecting', swift)


if __name__ == '__main__':
    unittest.main()
