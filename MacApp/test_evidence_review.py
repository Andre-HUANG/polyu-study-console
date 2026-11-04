"""Human review round trips with synthetic accounts and unchanged source evidence."""
import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '学习控制台'))
import server
import evidence_review
import runtime_cache
from native_canvas import save_private, HOST


class EvidenceReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='polyu-review-')
        self.profile = Path(self.tmp.name) / 'account'
        self.config = {'user': {'id': 'synthetic-review'}, 'courses': [{'id': 1, 'code': 'TEST', 'name': 'Synthetic'}]}
        self.patch = patch.multiple(server, PROFILE=self.profile, PROJECT=Path(self.tmp.name), NATIVE=True,
                                    ACCESS=server.LocalAccess(), DIAGNOSTICS=None)
        self.patch.start()
        runtime_cache.clear(); runtime_cache.STOP.clear(); server.DASHBOARD_CACHE.clear()
        self.date = (datetime.now(server.HK).date() + timedelta(days=20)).isoformat()
        self.source = {'documentId': 'f1', 'title': 'Syllabus', 'url': HOST + '/courses/1/files/1', 'locator': 'page 1', 'quote': 'Due date needs review'}
        self.pending = {'id': 'p1', 'course': 'TEST', 'title': 'Report', 'kind': 'assignment', 'needsReview': True,
                        'reason': '日期识别或年份存在歧义', 'sources': [self.source]}
        self.index = {'courses': [{'id': 1, 'code': 'TEST', 'complete': True}], 'records': [], 'evidence': {
            'pending': [self.pending], 'deadlines': [], 'grading': {}, 'detailConflicts': []}}
        save_private(self.profile / 'config.json', self.config)
        save_private(self.profile / 'login.json', {'user_id': 'synthetic-review', 'transport': 'webkit'})
        self.save_index()

    def tearDown(self):
        self.patch.stop(); runtime_cache.clear(); server.DASHBOARD_CACHE.clear(); self.tmp.cleanup()

    def save_index(self): save_private(self.profile / 'documents/index.json', self.index)

    def post(self, payload, authorized=True):
        handler = object.__new__(server.Handler)
        handler.server = Mock(server_port=8766); handler.path = '/api/evidence/review'
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8766'; handler.headers['Origin'] = 'http://127.0.0.1:8766'
        handler.headers['X-PolyU-Console'] = server.CSRF
        if authorized: handler.headers['Cookie'] = server.ACCESS.cookie_name(8766) + '=' + server.ACCESS.token
        body = json.dumps(payload).encode(); handler.headers['Content-Length'] = str(len(body))
        handler.rfile = io.BytesIO(body); handler.send_json = Mock(); handler.do_POST()
        args = handler.send_json.call_args.args
        return args[0], args[1] if len(args) > 1 else 200

    def item(self, kind='date'):
        return next(r for r in server.dashboard_data()['reviews']['items'] if r['type'] == kind)

    def confirm(self, item=None, **values):
        item = item or self.item()
        return self.post({'id': item['id'], 'digest': item['digest'], 'action': 'confirm', **values})

    def test_confirm_round_trip_keeps_sources_and_reduces_pending(self):
        before = (self.profile / 'documents/index.json').read_bytes()
        revision = server.status_data()['revision']
        result, status = self.confirm(date=self.date, time='15:00')
        self.assertEqual(status, 200, result)
        data = result['data']
        self.assertFalse(data['undated']); self.assertFalse(data['reviews']['items'])
        self.assertEqual(data['documents']['reviewCount'], 0)
        event = next(r for r in data['events'] if r.get('reviewId'))
        self.assertEqual((event['date'], event['time'], event['title']), (self.date, '15:00', 'Report'))
        self.assertIn('markKey', event)
        self.assertEqual((self.profile / 'documents/index.json').read_bytes(), before)
        self.assertNotEqual(server.status_data()['revision'], revision)
        if sys.platform != 'win32': self.assertEqual((self.profile / evidence_review.FILE).stat().st_mode & 0o777, 0o600)
        history = data['reviews']['history'][0]
        result, status = self.post({'id': history['id'], 'digest': history['digest'], 'action': 'reset'})
        self.assertEqual(status, 200); self.assertEqual(len(result['data']['undated']), 1)
        self.assertFalse(result['data']['events'])

    def test_stale_confirmation_rejected_then_source_change_resurfaces(self):
        old = self.item()
        self.pending['sources'][0]['quote'] = 'Revised deadline'; self.save_index()
        result, status = self.confirm(old, date=self.date)
        self.assertEqual(status, 400); self.assertFalse((self.profile / evidence_review.FILE).exists())
        self.assertEqual(self.confirm(date=self.date)[1], 200)
        self.pending['sources'][0]['quote'] = 'Revised again'; self.save_index()
        data = server.dashboard_data()
        self.assertTrue(data['reviews']['items'][0]['stale'])
        self.assertTrue(data['reviews']['history'][0]['stale']); self.assertFalse(data['events'])
        history = data['reviews']['history'][0]
        self.assertEqual(self.post({'id': history['id'], 'digest': history['digest'], 'action': 'reset'})[1], 200)

    def test_dismiss_requires_reason_and_undo_is_available(self):
        item = self.item(); payload = {'id': item['id'], 'digest': item['digest'], 'action': 'dismiss'}
        self.assertEqual(self.post(payload)[1], 400)
        result, status = self.post({**payload, 'note': 'Not required for my group'})
        self.assertEqual(status, 200); self.assertFalse(result['data']['undated']); self.assertFalse(result['data']['events'])
        self.assertEqual(result['data']['reviews']['history'][0]['action'], 'dismiss')

    def test_invalid_input_and_unauthorized_never_write(self):
        for values in ({'date': '2026-02-31'}, {'date': self.date, 'time': '25:00'},
                       {'date': self.date, 'time': '14:00', 'endTime': '13:00'}, {'date': self.date, 'kind': 'bogus'},
                       {'date': self.date, 'extra': 'untrusted'}):
            self.assertEqual(self.confirm(**values)[1], 400)
        self.assertEqual(self.post({'action': 'confirm'}, authorized=False)[1], 403)
        self.assertFalse((self.profile / evidence_review.FILE).exists())

    def test_account_switch_and_logout_never_expose_review(self):
        self.confirm(date=self.date)
        other = {**self.config, 'user': {'id': 'other'}}
        save_private(self.profile / 'config.json', other)
        save_private(self.profile / 'login.json', {'user_id': 'other'})
        self.assertFalse(server.dashboard_data()['reviews']['history'])
        (self.profile / 'login.json').unlink()
        data = server.dashboard_data()
        self.assertEqual(data['reviews'], {'items': [], 'history': []}); self.assertFalse(data['events'])

    def test_canvas_conflict_confirm_preserves_unsubmitted_evidence_without_duplicate(self):
        self.pending['sources'].append({'documentId': 'canvas-assignment:9', 'url': HOST + '/courses/1/assignments/9', 'quote': 'Old date'})
        self.index['evidence']['assignmentConflicts'] = {'9': 'conflict'}; self.save_index()
        assignment = {'course': 'TEST', 'name': 'Report', 'due_at': self.date + 'T12:00:00+08:00',
                      'html_url': HOST + '/courses/1/assignments/9', 'sub_state': 'unsubmitted', 'submission_state_observed': True}
        save_private(self.profile / 'snapshot.json', {'assignments': {'9': assignment}})
        before = (self.profile / 'snapshot.json').read_bytes()
        result, status = self.confirm(date=self.date, time='16:00')
        self.assertEqual(status, 200)
        rows = result['data']['events']; self.assertEqual(len(rows), 1)
        self.assertTrue(server.canvas_reports_unsubmitted(rows[0]))
        self.assertEqual((self.profile / 'snapshot.json').read_bytes(), before)

    def test_canvas_unpublished_date_can_be_entered(self):
        self.index['evidence']['pending'] = []; self.save_index()
        save_private(self.profile / 'snapshot.json', {'assignments': {'9': {'course': 'TEST', 'name': 'Essay',
            'points': 100, 'html_url': HOST + '/courses/1/assignments/9'}}})
        item = self.item(); self.assertFalse(item['needsReview'])
        result, status = self.confirm(item, date=self.date)
        self.assertEqual(status, 200); self.assertFalse(result['data']['undated'])
        self.assertEqual(len(result['data']['events']), 1)

    def test_document_assignment_source_and_canvas_are_one_review(self):
        self.pending['sources'][0]['documentId'] = '1:assignment:9'; self.save_index()
        save_private(self.profile / 'snapshot.json', {'assignments': {'9': {'course': 'TEST', 'name': 'Report',
            'points': 100, 'html_url': HOST + '/courses/1/assignments/9'}}})
        data = server.dashboard_data()
        self.assertEqual(len(data['reviews']['items']), 1); self.assertEqual(len(data['undated']), 1)
        result, status = self.confirm(date=self.date)
        self.assertEqual(status, 200); self.assertEqual(len(result['data']['events']), 1)
        self.assertFalse(result['data']['undated']); self.assertFalse(result['data']['reviews']['items'])

    def test_multiple_assignment_sources_cannot_collapse_to_one_date(self):
        self.pending['sources'] = [{**self.source, 'documentId': '1:assignment:' + aid} for aid in ('9', '10')]
        self.save_index()
        save_private(self.profile / 'snapshot.json', {'assignments': {aid: {'course': 'TEST', 'name': 'Report ' + aid,
            'points': 100, 'html_url': HOST + '/courses/1/assignments/' + aid} for aid in ('9', '10')}})
        item = next(r for r in server.dashboard_data()['reviews']['items'] if r.get('evidenceId'))
        self.assertTrue(item['lockedReason']); self.assertEqual(self.confirm(item, date=self.date)[1], 400)

    def test_detail_confirms_update_event_and_document_counts(self):
        self.index['evidence']['pending'] = []
        self.index['evidence']['deadlines'] = [{'id': 'e1', 'course': 'TEST', 'title': 'Exam', 'date': self.date,
            'kind': 'final', 'pattern': 'Exam', 'sources': [self.source], 'warnings': ['文件中的时间不一致']}]
        self.index['evidence']['detailConflicts'] = [{'course': 'TEST', 'title': 'Exam', 'field': 'time',
            'alternatives': ['10:00', '11:00'], 'sources': [self.source]}]
        self.save_index()
        result, status = self.confirm(self.item('detail'), time='10:00', endTime='12:00')
        self.assertEqual(status, 200); self.assertEqual(result['data']['events'][0]['time'], '10:00')
        self.assertEqual(result['data']['documents']['reviewCount'], 0)
        self.assertFalse(result['data']['events'][0]['warnings'])

    def seed_grades(self):
        parts = [{'key': 'essay', 'name': 'Essay', 'weight': None, 'pattern': 'Essay', 'mode': 'single',
                  'conflict': True, 'alternatives': [40, 50], 'sources': [self.source]},
                 {'key': 'exam', 'name': 'Exam', 'weight': 60, 'pattern': 'Exam', 'mode': 'single', 'sources': [self.source]}]
        save_private(self.profile / 'grading_schemes.json', {'TEST': {'verified': False, 'conflict': True, 'components': parts}})
        save_private(self.profile / 'snapshot.json', {'assignments': {'9': {'course': 'TEST', 'name': 'Essay',
            'sub_state': 'graded', 'score': 80, 'points': 100}}})
        return parts

    def test_date_and_detail_decisions_compose_and_undo_independently(self):
        self.index['evidence']['detailConflicts'] = [{'course': 'TEST', 'title': 'Report', 'field': 'time',
            'alternatives': ['10:00', '11:00'], 'sources': [self.source]}]
        self.save_index()
        self.assertEqual(self.confirm(date=self.date, title='My report', time='09:00')[1], 200)
        result, status = self.confirm(self.item('detail'), time='10:00', endTime='11:00')
        self.assertEqual(status, 200)
        self.assertEqual((result['data']['events'][0]['title'], result['data']['events'][0]['time']), ('My report', '10:00'))
        detail = next(h for h in result['data']['reviews']['history'] if h['type'] == 'detail')
        result, status = self.post({'id': detail['id'], 'digest': detail['digest'], 'action': 'reset'})
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['events'][0]['time'], '09:00')
        self.assertEqual(result['data']['documents']['reviewCount'], 1)

    def test_detail_first_prefills_date_and_remains_after_date_confirmation(self):
        self.index['evidence']['detailConflicts'] = [{'course': 'TEST', 'title': 'Report', 'field': 'location',
            'alternatives': ['Room A', 'Room B'], 'sources': [self.source]}]
        self.save_index()
        result, status = self.confirm(self.item('detail'), location='Room A')
        self.assertEqual(status, 200)
        item = self.item(); self.assertEqual(item['location'], 'Room A')
        result, status = self.confirm(item, date=self.date)
        self.assertEqual(status, 200); self.assertEqual(result['data']['events'][0]['location'], 'Room A')

    def test_full_grading_scheme_enters_fold_only_with_100_percent(self):
        self.seed_grades(); item = self.item('grading')
        bad = [{'key': 'essay', 'weight': 50}, {'key': 'exam', 'weight': 60}]
        self.assertEqual(self.confirm(item, components=bad)[1], 400)
        good = [{'key': 'essay', 'weight': 40}, {'key': 'exam', 'weight': 60}]
        result, status = self.confirm(item, components=good)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['data']['grades']['courses'][0]['earned'], 32)
        self.assertEqual(result['data']['grades']['courses'][0]['assessedWeight'], 40)

    def test_hidden_complex_and_canvas_conflicts_cannot_be_unlocked(self):
        for lock in ('hidden', 'complex', 'canvas'):
            parts = self.seed_grades()
            if lock == 'hidden': save_private(self.profile / 'canvas-grading-settings.json', {'TEST': {'hidden': True}})
            elif lock == 'complex': parts[0]['complex'] = True
            else: parts[0]['sources'] = [{'documentId': 'canvas-grade:1:2'}]
            if lock != 'hidden':
                (self.profile / 'canvas-grading-settings.json').unlink(missing_ok=True)
                save_private(self.profile / 'grading_schemes.json', {'TEST': {'verified': False, 'conflict': True, 'components': parts}})
            item = self.item('grading'); self.assertTrue(item['lockedReason'])
            result, status = self.confirm(item, components=[{'key': 'essay', 'weight': 40}, {'key': 'exam', 'weight': 60}])
            self.assertEqual(status, 400, result)

    def test_linked_manual_assignment_requires_unsubmitted_confirmation(self):
        save_private(self.profile / 'state.json', {'manual_deadlines': [{'course': 'TEST', 'item': 'Essay', 'date': self.date,
            'kind': 'assignment', 'url': HOST + '/courses/1/assignments/9'}]})
        save_private(self.profile / 'snapshot.json', {'assignments': {'9': {'course': 'TEST', 'name': 'Essay',
            'html_url': HOST + '/courses/1/assignments/9', 'sub_state': 'unsubmitted', 'submission_state_observed': True}}})
        row = server.dashboard_data()['events'][0]
        self.assertTrue(server.canvas_reports_unsubmitted(row))

    def test_new_canvas_restrictions_invalidate_saved_scheme_before_document_sync(self):
        parts = self.seed_grades()
        parts[0].update(canvasGroupId=2, weight=40, conflict=False)
        parts[1]['weight'] = None
        save_private(self.profile / 'assignment-meta.json', {'9': {'assignment_group_id': 2}})
        save_private(self.profile / 'grading_schemes.json', {'TEST': {'verified': False, 'components': parts}})
        result, status = self.confirm(self.item('grading'), components=[{'key': 'essay', 'weight': 40}, {'key': 'exam', 'weight': 60}])
        self.assertEqual(status, 200); self.assertEqual(result['data']['grades']['courses'][0]['earned'], 32)
        before = server.status_data()['revision']
        save_private(self.profile / 'canvas-grading-settings.json', {'TEST': {'components': [{'canvasGroupId': 2, 'weight': 30}]}})
        self.assertNotEqual(before, server.status_data()['revision'])
        item = self.item('grading'); self.assertTrue(item['lockedReason'])
        self.assertTrue(item['stale']); self.assertIsNone(server.dashboard_data()['grades']['courses'][0]['earned'])

    def test_review_storage_rejects_symlinks(self):
        if sys.platform == 'win32': self.skipTest('Symlink creation requires platform permission')
        target = Path(self.tmp.name) / 'untouched.json'
        target.write_text('untouched')
        (self.profile / evidence_review.FILE).symlink_to(target)
        result, status = self.confirm(date=self.date)
        self.assertEqual(status, 502, result)
        self.assertEqual(target.read_text(), 'untouched')

    def test_equal_item_group_redistribution_invalidates_review_before_reconciliation(self):
        parts = self.seed_grades()
        parts[0].update(weight=40, conflict=False, mode='group_all', equalItems=True,
                        expectedCount=2, canvasGroupIds=[1, 2])
        parts[0]['sources'].append({'documentId': 'canvas-grade:1:1', 'quote': '20% each'})
        parts[1]['weight'] = None
        save_private(self.profile / 'grading_schemes.json', {'TEST': {'verified': False, 'components': parts}})
        save_private(self.profile / 'assignment-meta.json', {'9': {'assignment_group_id': 1}})
        groups = [{'canvasGroupId': i, 'weight': 20, 'mode': 'group_all', 'complex': False} for i in (1, 2)]
        save_private(self.profile / 'canvas-grading-settings.json', {'TEST': {'components': groups}})
        item = self.item('grading')
        weights = [{'key': 'essay', 'weight': 40}, {'key': 'exam', 'weight': 60}]
        result, status = self.confirm(item, components=weights)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['data']['grades']['courses'][0]['earned'], 16)
        # API ordering alone must not revoke a valid decision.
        save_private(self.profile / 'canvas-grading-settings.json', {'TEST': {'components': list(reversed(groups))}})
        data = server.dashboard_data()
        self.assertFalse(any(r['type'] == 'grading' for r in data['reviews']['items']))
        self.assertEqual(data['grades']['courses'][0]['earned'], 16)
        original_scheme = (self.profile / 'grading_schemes.json').read_bytes()
        original_reviews = (self.profile / evidence_review.FILE).read_bytes()
        # Same total, but each previously equal group now has a different weight.
        groups[0]['weight'], groups[1]['weight'] = 10, 30
        save_private(self.profile / 'canvas-grading-settings.json', {'TEST': {'components': groups}})
        changed = self.item('grading')
        self.assertNotEqual(item['digest'], changed['digest'])
        self.assertTrue(changed['stale']); self.assertTrue(changed['lockedReason'])
        self.assertIsNone(server.dashboard_data()['grades']['courses'][0]['earned'])
        self.assertEqual(self.confirm(item, components=weights)[1], 400)
        self.assertEqual(self.confirm(changed, components=weights)[1], 400)
        self.assertEqual((self.profile / 'grading_schemes.json').read_bytes(), original_scheme)
        self.assertEqual((self.profile / evidence_review.FILE).read_bytes(), original_reviews)


if __name__ == '__main__': unittest.main()
