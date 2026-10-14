"""1.8 categories, native event boundaries and account-scoped diagnostics."""
import concurrent.futures
import io
import json
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '学习控制台'))
sys.path.insert(0, str(ROOT / 'WindowsApp'))
import server
import desktop
from assessment_types import classify
from course_evidence import facts_from, consolidate
from diagnostic_log import DiagnosticLog, LIMIT, error_code
from canvas_api import CanvasAuthError
from native_canvas import HOST, save_private


class SettingsChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='polyu-settings-')
        self.root = Path(self.temp.name)
        self.profile = self.root / 'account'
        self.patch = patch.multiple(server, PROJECT=self.root, PROFILE=self.profile, DIAGNOSTICS=None,
                                    ACCESS=server.LocalAccess(), NATIVE=True, NATIVE_SECRET='synthetic-native-key')
        self.patch.start()
        self.job = server.JOB.copy()
        server.JOB.update(busy=False, stage='', lastError='', needsLogin=False)

    def tearDown(self):
        server.JOB.clear(); server.JOB.update(self.job)
        self.patch.stop(); self.temp.cleanup()

    def authorize(self, account='synthetic-account'):
        save_private(self.profile / 'config.json', {'user': {'id': account}, 'courses': [{'id': 1, 'code': 'TEST', 'name': 'Synthetic'}]})
        save_private(self.profile / 'login.json', {'user_id': account, 'transport': 'webkit'})

    def handler(self, path, payload=None, native=False, trusted=True):
        handler = object.__new__(server.Handler)
        handler.server = Mock(server_port=8766)
        handler.path = path
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8766'
        handler.headers['Origin'] = 'http://127.0.0.1:8766'
        if native: handler.headers['X-PolyU-Native'] = server.NATIVE_SECRET if trusted else 'forged'
        else:
            handler.headers['X-PolyU-Console'] = server.CSRF if trusted else 'forged'
            if trusted: handler.headers['Cookie'] = server.ACCESS.cookie_name(8766) + '=' + server.ACCESS.token
        body = json.dumps(payload or {}).encode()
        handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.send_json = Mock()
        return handler

    def document(self, rows, doc_id='notice'):
        doc = {'id': doc_id, 'courseId': '1', 'course': 'TEST', 'title': 'Assessment notice',
               'type': 'announcement', 'url': HOST + '/courses/1/pages/' + doc_id}
        cfg = {'term': {'start': '2026-09-01', 'end': '2027-08-31'}}
        return {'facts': facts_from(doc, [{'page': 1, 'rows': rows, 'method': 'Synthetic text'}], cfg)}

    def test_final_names_and_non_exam_deliverables(self):
        for title in ['Final exam', 'FINAL-EXAMINATION', 'Final test', '期末考试', '期末考試', '期末測試', 'End-of-term examination']:
            with self.subTest(title=title): self.assertEqual(classify(title), 'final')
        for title in ['Final project', 'Final presentation', 'Final report', '期末项目', '期末报告']:
            with self.subTest(title=title): self.assertEqual(classify(title), 'assignment')
        self.assertEqual(classify('Mid-term quiz'), 'midterm')
        self.assertEqual(classify('Midterm / Final exam'), 'test')
        self.assertEqual(classify('Written examination'), 'test')

    def test_final_notice_retains_separated_time_venue_and_original_excerpt(self):
        row = self.document(['Final-examination notice', 'Date: 15 December 2026', 'Time: 14:00–16:00', 'Venue: ZS301', 'Closed-book'])
        result = consolidate([row])['deadlines'][0]
        self.assertEqual(result['kind'], 'final')
        self.assertEqual(result['date'], '2026-12-15')
        self.assertEqual(result['time'], '14:00')
        self.assertEqual(result['location'], 'ZS301')
        self.assertTrue(any('15 December 2026' in s['quote'] for s in result['sources']))
        self.assertTrue(all(s['url'].startswith(HOST) and s['page'] == 1 for s in result['sources']))

    def test_final_undated_and_conflicting_dates_stay_pending(self):
        result = consolidate([self.document(['Final exam', 'Date to be announced'])])
        self.assertFalse(result['deadlines'])
        self.assertEqual(result['pending'][0]['kind'], 'final')
        self.assertFalse(result['pending'][0]['needsReview'])
        result = consolidate([self.document(['Final exam', '15 December 2026'], 'a'), self.document(['Final exam', '16 December 2026'], 'b')])
        self.assertFalse(result['deadlines'])
        self.assertTrue(result['pending'][0]['needsReview'])

    def test_final_and_midterm_timetable_rows_are_independent(self):
        row = self.document(['Assessment schedule', 'Midterm test\t13 October 2026\t14:00', 'Final exam\t15 December 2026\t16:00'])
        result = consolidate([row])['deadlines']
        self.assertEqual([(r['kind'], r['date']) for r in result], [('midterm', '2026-10-13'), ('final', '2026-12-15')])

    def test_duplicate_final_evidence_does_not_duplicate_event(self):
        result = consolidate([self.document(['Final exam', '15 December 2026'], 'a'), self.document(['期末考試', '15 December 2026'], 'b')])
        self.assertEqual(len(result['deadlines']), 1)
        self.assertEqual(result['deadlines'][0]['kind'], 'final')
        self.assertEqual({s['documentId'] for s in result['deadlines'][0]['sources']}, {'a', 'b'})

    def test_mixed_heading_retains_both_exam_mentions_without_guessing_dates(self):
        result = consolidate([self.document(['Assessment methods', 'Midterm and final exam', 'Dates to be announced'])])
        self.assertEqual({r['kind'] for r in result['pending']}, {'midterm', 'final'})
        self.assertFalse(result['deadlines'])

    def test_mixed_exam_row_with_shared_dates_stays_pending(self):
        result = consolidate([self.document(['Assessment schedule', 'Midterm / Final exam\t15 December 2026'])])
        self.assertFalse(result['deadlines'])
        self.assertEqual({r['kind'] for r in result['pending']}, {'midterm', 'final'})
        self.assertTrue(all(r['needsReview'] and '对应关系' in r['reason'] for r in result['pending']))

    def test_written_exam_synonym_merges_only_with_one_matching_exam_date(self):
        final = self.document(['Final exam', '15 December 2026'], 'final')
        written = self.document(['Written examination', '15 December 2026'], 'written')
        result = consolidate([final, written])['deadlines']
        self.assertEqual(len(result), 1); self.assertEqual(result[0]['kind'], 'final')
        self.assertEqual({s['documentId'] for s in result[0]['sources']}, {'final','written'})
        midterm = self.document(['Midterm exam', '15 December 2026'], 'mid')
        self.assertEqual(len(consolidate([final, written, midterm])['deadlines']), 3)

    def test_final_scheme_fold_uses_scores_only_when_weights_are_coherent(self):
        from canvas_grading import merge_document_grading
        from grades import build_grades
        record = self.document(['Assessment methods', 'Midterm exam 30%', 'Group project 30%', 'Final exam 40%'])
        index = {'courses':[{'code':'TEST','complete':True}], 'evidence':consolidate([record])}
        scheme = merge_document_grading(self.profile, index)
        self.assertTrue(scheme['TEST']['verified'])
        rows = {'1':{'course':'TEST','name':'Midterm exam','sub_state':'graded','score':80,'points':100},
                '2':{'course':'TEST','name':'Group project','sub_state':'submitted','score':None,'points':100},
                '3':{'course':'TEST','name':'Final exam','sub_state':'unsubmitted','score':None,'points':100}}
        result = build_grades([{'code':'TEST'}], rows, server.safe_url, schemes_path=self.profile/'grading_schemes.json')['courses'][0]
        self.assertEqual(result['earned'],24)
        final = next(c for c in result['components'] if c['name']=='期末考试')
        self.assertIsNone(final['weighted'])
        rows['3'].update(sub_state='graded',score=70)
        result = build_grades([{'code':'TEST'}], rows, server.safe_url, schemes_path=self.profile/'grading_schemes.json')['courses'][0]
        self.assertEqual(result['earned'],52)
        conflict = self.document(['Assessment methods','Final exam 50%'], 'conflict')
        merge_document_grading(self.profile, {'courses':index['courses'],'evidence':consolidate([record, conflict])})
        result = build_grades([{'code':'TEST'}], rows, server.safe_url, schemes_path=self.profile/'grading_schemes.json')['courses'][0]
        self.assertIsNone(result['earned'])

    def test_final_canvas_undated_assignment_is_not_silently_dropped(self):
        self.authorize()
        save_private(self.profile / 'snapshot.json', {'assignments': {'1': {'course':'TEST','name':'Final exam','due_at':None}}})
        self.assertEqual(server.dashboard_data()['undated'][0]['kind'], 'final')

    def test_historical_dates_remain_in_evidence_with_current_date(self):
        result = consolidate([self.document(['Final exam', '15 December 2026', '15 December 2025'])])
        event = result['deadlines'][0]
        self.assertEqual(event['date'], '2026-12-15')
        self.assertTrue(any('2025' in s['quote'] for s in event['sources']))
        self.assertTrue(event['warnings'])

    def test_personal_final_classification_survives_edit_and_old_records(self):
        self.authorize()
        handler = object.__new__(server.Handler)
        payload = {'course': 'TEST', 'title': 'End of semester assessment', 'date': '2026-12-15', 'kind': 'final', 'url': HOST + '/courses/1'}
        handler.save_event(payload)
        save_private(self.profile / 'snapshot.json', {'assignments': {'1': {'course': 'TEST', 'name': 'Final exam', 'due_at': '2026-12-16T08:00:00Z'}}})
        rows = server.dashboard_data()['events']
        self.assertEqual([r['kind'] for r in rows], ['final', 'final'])
        edit = {**payload, 'date': '2026-12-17', 'original': {'course': 'TEST', 'title': payload['title']}}
        edit.pop('kind')
        handler.save_event(edit)
        self.assertEqual(json.loads((self.profile / 'state.json').read_text())['manual_deadlines'][0]['kind'], 'final')
        for invalid in ['other', [], {'kind': 'final'}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): handler.save_event({**payload, 'kind': invalid})

    def test_document_generated_final_keeps_category_after_reconciliation(self):
        self.authorize()
        event = consolidate([self.document(['Final exam', '15 December 2026'])])['deadlines'][0]
        save_private(self.profile / 'state.json', {'manual_deadlines': [{'course': 'TEST', 'item': '笔试', 'date': '2026-12-15', 'source': server.DOCUMENT_DEADLINE_PREFIX + event['id']}]})
        with patch.object(server, 'public_summary', return_value={'evidence': {'deadlines': [event]}}):
            rows = server.dashboard_data()['events']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'final')

    def test_anonymous_logs_never_create_account_files(self):
        log = server.diagnostics()
        log.record('login.open', 'attempt', origin='native')
        log.record('login.verify', 'failure', 'INVALID_INPUT')
        self.assertFalse(self.profile.exists())
        self.assertFalse(log.summary()['persistent'])
        self.assertEqual(len(log.summary()['events']), 2)

    def test_account_log_reopens_without_account_identifiers_in_export(self):
        self.authorize('private-account-id')
        log = server.diagnostics(); log.record('login.verify', 'success')
        reopened = DiagnosticLog(self.profile, '1.8.1').for_account('private-account-id')
        self.assertEqual(reopened.summary()['events'][0]['result'], 'success')
        exported = json.dumps(reopened.summary())
        self.assertNotIn('private-account-id', exported)
        self.assertNotIn('scope', exported)
        self.assertNotIn('private-account-id', reopened.path.read_text())
        if sys.platform != 'win32': self.assertEqual(reopened.path.stat().st_mode & 0o777, 0o600)

    def test_rollover_is_bounded_and_atomic_under_concurrent_events(self):
        self.authorize()
        log = server.diagnostics()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: log.record('sync', 'success'), range(LIMIT+30)))
        saved = json.loads(log.path.read_text())['events']
        self.assertEqual(len(saved), LIMIT)
        self.assertEqual(len({r['id'] for r in saved}), LIMIT)
        self.assertEqual({r['result'] for r in saved}, {'success'})
        self.assertFalse(list(log.path.parent.glob('*.tmp')))

    def test_log_rejects_raw_text_or_unrecognized_fields(self):
        log = server.diagnostics()
        for event,result,code in [('password=secret', 'failure', ''), ('login.verify', 'secret-token', ''), ('login.verify', 'failure', 'Cookie: secret'), ([], 'success', '')]:
            with self.subTest(event=event), self.assertRaises(ValueError): log.record(event, result, code)
        handler = self.handler('/api/native/diagnostics', {'event':'login.verify','result':'success','code':'','cookies':'SENSITIVE'}, native=True)
        handler.do_POST()
        self.assertEqual(handler.send_json.call_args.args[1], 400)
        self.assertFalse(log.summary()['events'])

    def test_untrusted_saved_log_rows_cannot_inject_private_text(self):
        self.authorize()
        log = server.diagnostics(); row = log.record('sync', 'success')
        saved = json.loads(log.path.read_text())
        saved['events'] += [{**row, 'code': 'SENSITIVE cookie'}, {**row, 'at': 'SECRET'}, {**row, 'extra': 'PRIVATE BODY'}]
        log.path.write_text(json.dumps(saved))
        fresh = DiagnosticLog(self.profile, '1.8.1').for_account('synthetic-account')
        self.assertEqual(len(fresh.summary()['events']), 1)
        self.assertNotIn('SENSITIVE', json.dumps(fresh.summary()))

    def test_error_codes_do_not_read_or_serialize_exception_messages(self):
        error = CanvasAuthError('password=secret cookies=secret private document body')
        self.assertEqual(error_code(error), 'SESSION_EXPIRED')
        with patch.object(server, '_connect_native', side_effect=error):
            with self.assertRaises(CanvasAuthError): server.connect_native({'cookies': 'PRIVATE'})
        exported = json.dumps(server.diagnostics().summary())
        self.assertNotIn('secret', exported)
        self.assertNotIn('private document', exported)
        self.assertIn('SESSION_EXPIRED', exported)

    def test_login_success_and_failure_are_recorded_and_account_switch_rejected(self):
        with self.assertRaises(ValueError): server.connect_native({'cookies': []})
        fake = Mock(); fake.get.return_value = {'id': 'A', 'name': 'Private name'}
        cookie = {'name': 's', 'value': 'SENSITIVE', 'domain': 'canvas.polyu.edu.hk'}
        with patch.object(server, 'native_api', return_value=fake): server.connect_native({'cookies': [cookie]})
        events = server.diagnostics().summary()['events']
        self.assertEqual(events[0]['event'], 'login.verify'); self.assertEqual(events[0]['result'], 'success')
        fake.get.return_value = {'id': 'B', 'name': 'Different private name'}
        with patch.object(server, 'native_api', return_value=fake), self.assertRaises(ValueError): server.connect_native({'cookies': [cookie]})
        exported = json.dumps(server.diagnostics().summary())
        for private in ['SENSITIVE', 'Private name', 'Different private name']: self.assertNotIn(private, exported)
        self.assertEqual(json.loads((self.profile / 'login.json').read_text())['user_id'], 'A')
        self.assertEqual(server.diagnostics().summary()['events'][0]['result'], 'failure')

    def test_another_account_does_not_read_previous_account_log(self):
        self.authorize('A'); first = server.diagnostics(); first.record('sync', 'failure', 'NETWORK_ERROR')
        second = DiagnosticLog(self.profile, '1.8.1').for_account('B')
        self.assertFalse(second.summary()['events'])

    def test_logout_deletes_disk_history_and_new_login_discards_logout_memory(self):
        self.authorize('A'); old = server.diagnostics(); old.record('sync', 'failure', 'NETWORK_ERROR')
        save_private(self.profile / 'documents/sentinel.json', {'private': 'synthetic'})
        server.logout_native()
        self.assertFalse(old.path.exists())
        self.assertFalse((self.profile / 'documents').exists())
        summary = server.diagnostics().summary()
        self.assertFalse(summary['persistent'])
        self.assertEqual(summary['events'][0]['event'], 'logout')
        self.assertEqual(summary['events'][0]['result'], 'success')
        self.assertNotIn('sync', {r['event'] for r in summary['events']})
        server.diagnostics().record('login.verify', 'attempt')
        self.authorize('B'); new = server.diagnostics()
        new.record('login.verify', 'success')
        self.assertEqual({r['event'] for r in new.summary()['events']}, {'login.verify'})

    @unittest.skipIf(sys.platform == 'win32', 'Synthetic symlink requires no extra privilege on macOS')
    def test_log_path_link_does_not_read_or_write_outside_account(self):
        self.authorize(); self.profile.joinpath('diagnostics').mkdir()
        outside = self.root / 'outside.json'; outside.write_text('preserve')
        (self.profile / 'diagnostics/events.json').symlink_to(outside)
        log = server.diagnostics(); log.record('sync', 'success')
        self.assertTrue(log.summary()['storageIssue'])
        self.assertEqual(outside.read_text(), 'preserve')

    def test_native_events_require_secret_and_can_record_while_sync_is_busy(self):
        payload = {'event': 'login.open', 'result': 'attempt', 'code': ''}
        bad = self.handler('/api/native/diagnostics', payload, native=True, trusted=False); bad.do_POST()
        self.assertEqual(bad.send_json.call_args.args[1], 403)
        self.assertFalse(server.diagnostics().summary()['events'])
        server.MUTATION_LOCK.acquire()
        try:
            good = self.handler('/api/native/diagnostics', payload, native=True); good.do_POST()
        finally: server.MUTATION_LOCK.release()
        self.assertEqual(good.send_json.call_args.args[0], {'ok': True})
        self.assertEqual(server.diagnostics().summary()['events'][0]['event'], 'login.open')

    def test_logs_read_and_clear_require_console_csrf_even_before_login(self):
        server.diagnostics().record('login.open', 'attempt')
        bad = self.handler('/api/diagnostics', trusted=False); bad.do_GET()
        self.assertEqual(bad.send_json.call_args.args[1], 401)
        good = self.handler('/api/diagnostics'); good.do_GET()
        self.assertEqual(len(good.send_json.call_args.args[0]['events']), 1)
        clear = self.handler('/api/diagnostics/clear'); clear.do_POST()
        self.assertFalse(server.diagnostics().summary()['events'])
        self.assertFalse(self.profile.exists())

    def test_clearing_logs_preserves_all_account_business_data(self):
        self.authorize(); save_private(self.profile / 'state.json', {'manual_deadlines': [{'item': 'Keep'}]})
        server.diagnostics().record('sync', 'success')
        clear = self.handler('/api/diagnostics/clear'); clear.do_POST()
        self.assertFalse(server.diagnostics().summary()['events'])
        self.assertTrue(server.connection_data()['authorized'])
        self.assertEqual(json.loads((self.profile / 'state.json').read_text())['manual_deadlines'][0]['item'], 'Keep')

    def test_windows_login_open_cancel_failure_and_logout_cancel_are_logged(self):
        webview = MagicMock(); app = desktop.Desktop(self.root, webview)
        app.console = Mock(); app.backend = Mock()
        app.login(); app._login_closed()
        self.assertIn(('login.open', 'success'), [c.args[:2] for c in app.backend.record.call_args_list])
        self.assertIn(('login.close', 'cancelled'), [c.args[:2] for c in app.backend.record.call_args_list])
        app.login_window = None; webview.create_window.side_effect = OSError('private details')
        with self.assertRaises(OSError): app.login()
        self.assertEqual(app.backend.record.call_args.args, ('login.open', 'failure', 'IO_ERROR'))
        with patch.object(desktop, 'native_confirm', return_value=(False, False)):
            app.logout()
        self.assertEqual(app.backend.record.call_args.args, ('logout', 'cancelled'))
        self.assertFalse(app.stopping)

    def test_windows_native_log_queue_retains_only_fixed_events(self):
        backend = desktop.Backend(self.root)
        backend.record('login.open', 'attempt')
        self.assertEqual(len(backend.diagnostic_pending), 1)
        backend.url = 'http://127.0.0.1:8766/'
        with patch.object(backend, 'request', return_value={'ok': True}) as request:
            backend.flush_diagnostics()
        self.assertEqual(request.call_args.args, ('diagnostics', {'event':'login.open','result':'attempt','code':''}))
        self.assertFalse(backend.diagnostic_pending)
        with self.assertRaises(ValueError): backend.record('login.open', 'failure', 'Cookie: sensitive')


if __name__ == '__main__': unittest.main(verbosity=2)
