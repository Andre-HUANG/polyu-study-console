"""Concurrency, incremental imports, integrity and hourly scheduling regressions."""
import hashlib
import io
import itertools
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '学习控制台'))
import canvas_archive
import canvas_documents
import native_canvas
import server
from sync_support import bounded_results, sync_settings


class FixtureCanvas:
    download_workers = 3
    def __init__(self, count=12):
        self.data = {str(i): f'Group project due 27 November 2026\nFile {i}'.encode() for i in range(1, count+1)}
        self.calls, self.active, self.maximum = [], 0, 0
        self.lock, self.local = threading.Lock(), threading.local()
        self.fail, self.user, self.duplicates = set(), '1', False

    @property
    def last_download(self): return self.local.result

    def fetch(self, url, **kw):
        page = '<script>window.ENV={"current_user_id":"' + self.user + '"}</script><main>'
        if url.endswith('/modules'):
            page += '<div class="context_module" aria-label="Course materials"><ul>'
            for fid in self.data:
                entry = f'<li class="context_module_item Attachment_{fid}"><a class="item_link" title="Assessment {fid}.txt" href="/courses/5/modules/items/{fid}">Assessment {fid}.txt</a></li>'
                page += entry * (3 if self.duplicates else 1)
            page += '</ul></div>'
        page += '<p>Course information.</p></main>'
        headers = Message(); headers['Content-Type'] = 'text/html; charset=utf-8'
        return headers, page.encode()

    def download_file(self, url, destination, cancelled=lambda: None, validators=None):
        fid = url.split('/files/')[1].split('/')[0]
        with self.lock:
            self.active += 1; self.maximum = max(self.maximum, self.active)
            self.calls.append((fid, validators))
        try:
            time.sleep(.012)
            cancelled()
            if fid in self.fail: raise RuntimeError('Synthetic transient failure')
            self.local.result = {'etag': '"' + fid + '"', 'transfer': 'revalidated' if validators else 'downloaded'}
            if not validators:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(self.data[fid])
            return destination.stat().st_size
        finally:
            with self.lock: self.active -= 1


class SyncChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='polyu-sync-test-')
        self.profile = Path(self.tmp.name)
        self.cfg = {'user': {'id': '1'}, 'term': {'start': '2026-09-01', 'end': '2027-08-31'},
                    'courses': [{'id': '5', 'code': 'TEST5', 'name': 'Synthetic course'}]}

    def tearDown(self): self.tmp.cleanup()

    def archive(self, api):
        return canvas_archive.archive_courses(api, self.profile, self.cfg, lambda: None, lambda _: None)

    def scan(self, api, archive, cfg=None):
        return canvas_documents.scan_courses(api, self.profile, cfg or self.cfg, None, lambda: None, lambda _: None, archive=archive)

    def test_download_queue_is_bounded_and_metadata_matches_each_file(self):
        api = FixtureCanvas()
        archive = self.archive(api)
        self.assertEqual(api.maximum, 3)
        self.assertTrue(archive['complete'])
        self.assertEqual(archive['progress'], {'completed': 12, 'discovered': 12})
        for row in archive['entries']:
            self.assertEqual(row['etag'], '"' + row['id'] + '"')
            self.assertEqual(row['sha256'], hashlib.sha256(api.data[row['id']]).hexdigest())

    def test_same_attachment_is_downloaded_once_and_all_placements_survive(self):
        api = FixtureCanvas(1); api.duplicates = True
        row = self.archive(api)['entries'][0]
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(len(set(row['paths'])), 3)
        for path in row['paths']:
            self.assertEqual((self.profile/'documents/files'/path).read_bytes(), api.data['1'])

    def test_warm_downloads_validate_content_and_corruption_forces_transfer(self):
        api = FixtureCanvas(4)
        first = self.archive(api)
        path = self.profile/'documents/files'/first['entries'][0]['paths'][0]
        path.write_bytes(b'corrupted')
        api.calls.clear()
        second = self.archive(api)
        self.assertTrue(second['complete'])
        calls = dict(api.calls)
        self.assertIsNone(calls['1'])
        self.assertTrue(all(calls[fid] for fid in ('2','3','4')))
        self.assertEqual(path.read_bytes(), api.data['1'])

    def test_partial_failure_is_visible_and_next_run_retries_only_failed_bytes(self):
        api = FixtureCanvas(4); api.fail = {'2'}
        first = self.archive(api)
        self.assertFalse(first['complete'])
        self.assertEqual(next(e for e in first['entries'] if e['id']=='2')['status'], 'failed')
        api.fail.clear(); api.calls.clear()
        self.assertTrue(self.archive(api)['complete'])
        self.assertIsNone(dict(api.calls)['2'])
        self.assertTrue(all(dict(api.calls)[fid] for fid in ('1','3','4')))

    def test_other_account_cannot_reuse_manifest_or_evidence(self):
        api = FixtureCanvas(2); first = self.archive(api)
        api.user = '2'; self.cfg['user']['id'] = '2'; api.calls.clear()
        self.archive(api)
        self.assertTrue(all(not validators for _,validators in api.calls))
        with self.assertRaisesRegex(RuntimeError, '账号'):
            self.scan(api, first)

    def test_cancellation_joins_active_download_threads(self):
        stopped = threading.Event()
        finished = []
        def cancelled():
            if stopped.is_set(): raise RuntimeError('Synthetic logout')
        def work(n):
            try:
                stopped.set()
                cancelled()
            finally: finished.append(n)
        with self.assertRaisesRegex(RuntimeError, 'logout'):
            list(bounded_results(work, range(50), 3, cancelled))
        self.assertLessEqual(len(finished), 3)
        self.assertFalse(any(t.name.startswith('Canvas-download') for t in threading.enumerate()))

    def test_native_stream_digest_and_etag_are_thread_local(self):
        api = native_canvas.session_class()(native_canvas.HOST, self.profile, cookies=[], persist=True)
        class Response(io.BytesIO):
            def __init__(self, data, fid):
                super().__init__(data); self.headers = Message()
                self.headers['Content-Type'] = 'application/octet-stream'
                self.headers['Content-Length'] = str(len(data)); self.headers['ETag'] = fid
        class Opener:
            def open(self, request, **kw):
                fid = request.full_url.split('/files/')[1].split('/')[0]
                time.sleep(.01)
                return Response(fid.encode()*50, fid)
        def transfer(fid):
            api.download_file(native_canvas.HOST+f'/courses/5/files/{fid}/download', self.profile/(fid+'.txt'))
            time.sleep(.01)
            return fid, api.last_download
        with patch('urllib.request.build_opener', return_value=Opener()), ThreadPoolExecutor(max_workers=3) as pool:
            for fid, result in pool.map(transfer, ['1','2','3','4','5','6']):
                self.assertEqual(result['etag'], fid)
                self.assertEqual(result['sha256'], hashlib.sha256(fid.encode()*50).hexdigest())
        self.assertEqual(json.loads(api.cookie_path.read_text()), [])
        self.assertFalse(list(self.profile.rglob('*.tmp')))

    def test_retry_after_is_honored_and_wait_is_cancellable(self):
        self.assertEqual(native_canvas.retry_delay('120', 0), 120)
        self.assertEqual(native_canvas.retry_delay('NaN', 1), 2)
        with patch('native_canvas.time.time', return_value=0):
            self.assertEqual(native_canvas.retry_delay('Thu, 01 Jan 1970 00:02:00 GMT', 0), 120)
        gate = native_canvas.DownloadGate(); gate.defer(120)
        with self.assertRaisesRegex(RuntimeError, 'cancel'):
            gate.wait(lambda: (_ for _ in ()).throw(RuntimeError('cancel')))

    def test_unchanged_import_reuses_interpretation_and_config_change_invalidates_it(self):
        api = FixtureCanvas(3); archive = self.archive(api)
        first = self.scan(api, archive)
        with patch.object(canvas_documents, 'extract', wraps=canvas_documents.extract) as extract, patch.object(canvas_documents, 'describe', wraps=canvas_documents.describe) as describe:
            second = self.scan(api, archive)
            self.assertEqual(extract.call_count, 0)
            self.assertEqual(describe.call_count, 0)
        self.assertEqual(first['evidence'], second['evidence'])
        cfg = {**self.cfg, 'term': {'start': '2027-09-01', 'end': '2028-08-31'}}
        with patch.object(canvas_documents, 'extract', wraps=canvas_documents.extract) as extract, patch.object(canvas_documents, 'describe', wraps=canvas_documents.describe) as describe:
            self.scan(api, archive, cfg)
            self.assertEqual(extract.call_count, 0)
            self.assertGreater(describe.call_count, 0)

    def test_reader_upgrade_reprocesses_saved_files(self):
        api = FixtureCanvas(2); archive = self.archive(api); self.scan(api, archive)
        with patch.object(canvas_documents, 'READER_VERSION', canvas_documents.READER_VERSION+1), patch.object(canvas_documents, 'extract', wraps=canvas_documents.extract) as extract:
            self.scan(api, archive)
            self.assertEqual(extract.call_count, 2)

    def test_interpreter_upgrade_reinterprets_account_cache_without_new_file_reads(self):
        api = FixtureCanvas(1); api.data['1'] = b'Final exam\n15 December 2026'
        archive = self.archive(api)
        with patch.object(canvas_documents, 'VERSION', 7): self.scan(api, archive)
        with patch.object(canvas_documents, 'extract', wraps=canvas_documents.extract) as extract, patch.object(canvas_documents, 'describe', wraps=canvas_documents.describe) as describe:
            upgraded = self.scan(api, archive)
        self.assertEqual(upgraded['version'], 8)
        self.assertEqual(extract.call_count, 0)
        self.assertGreater(describe.call_count, 0)
        self.assertEqual(upgraded['evidence']['deadlines'][0]['kind'], 'final')

    def test_changed_content_reinterprets_only_that_file(self):
        api = FixtureCanvas(3); archive = self.archive(api); first = self.scan(api, archive)
        row = archive['entries'][0]
        data = b'Group project due 30 November 2026'
        (self.profile/'documents/files'/row['paths'][0]).write_bytes(data)
        row['sha256'] = hashlib.sha256(data).hexdigest()
        with patch.object(canvas_documents, 'extract', wraps=canvas_documents.extract) as extract:
            second = self.scan(api, archive)
            self.assertEqual(extract.call_count, 1)
        self.assertNotEqual(first['evidence'], second['evidence'])

    def test_partial_parse_remains_visible_and_retries_next_import(self):
        api = FixtureCanvas(1); archive = self.archive(api)
        partial = [{'page':1,'text':'Group project due 27 November 2026','rows':['Group project due 27 November 2026'],
                    'readable':True,'processed':True,'issues':['Synthetic unreadable image']}]
        with patch.object(canvas_documents, 'extract', return_value=partial):
            first = self.scan(api, archive)
        self.assertEqual(next(r for r in first['records'] if r.get('fileId'))['status'],'partial')
        with patch.object(canvas_documents, 'extract', wraps=canvas_documents.extract) as extract:
            second = self.scan(api, archive)
            self.assertEqual(extract.call_count, 1)
        self.assertEqual(next(r for r in second['records'] if r.get('fileId'))['status'],'read')

    def test_plain_office_avoids_second_pass_and_images_follow_all_text(self):
        api = FixtureCanvas(1); api.data['2'] = api.data['1']
        archive = self.archive(api)
        for row in archive['entries']:
            old = self.profile/'documents/files'/row['paths'][0]
            blob = old.with_suffix('.docx')
            with zipfile.ZipFile(blob,'w') as z:
                z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Assessment notice</w:t></w:r></w:p></w:body></w:document>')
                if row['id'] == '2': z.writestr('word/media/image1.png', b'synthetic image for mocked OCR')
            old.unlink(); row.update(paths=[str(blob.relative_to(self.profile/'documents/files'))], sha256=hashlib.sha256(blob.read_bytes()).hexdigest())
        phases = []
        original_extract = canvas_documents.extract
        def extract(path, *a, **kw):
            phases.append(('text' if kw.get('text_only') else 'images', Path(path).stem))
            return original_extract(path,*a,**kw)
        import document_text
        with patch.object(canvas_documents,'extract',side_effect=extract), patch.object(document_text,'image_ocr',return_value=[{'rows':['Group project due 27 November 2026'],'rowConfidences':[.95],'processed':True,'readable':True}]):
            result = self.scan(api, archive)
        self.assertEqual([kind for kind,_ in phases], ['text','text','images'])
        self.assertTrue(all(r['status']=='read' for r in result['records']))
        image = next(r for r in result['records'] if r.get('fileId')=='2')
        self.assertTrue(image['findings'])

    def test_empty_text_is_processed_and_integration_failure_is_visible(self):
        import document_text
        blank = self.profile/'blank.txt'; blank.write_bytes(b'')
        pages = document_text.extract(blank,None,self.profile/'cache')
        doc = {'id':'5:file:1','type':'file','course':'TEST5','courseId':'5','title':'Blank','url':native_canvas.HOST+'/courses/5/files/1'}
        canvas_documents.describe(doc,pages,self.cfg)
        self.assertEqual(doc['status'],'read'); self.assertEqual(doc['issues'],[])
        api=FixtureCanvas(1); archive=self.archive(api)
        result=canvas_documents.scan_courses(api,self.profile,self.cfg,None,lambda:None,lambda _:None,
            checkpoint=lambda _: (_ for _ in ()).throw(RuntimeError('Synthetic integration failure')),archive=archive)
        self.assertIn('Synthetic integration failure',result['integrationIssue'])
        self.assertIn('integrationIssue',json.loads((self.profile/'documents/index.json').read_text()))

    def test_cdn_requests_never_carry_cookies_or_authorization(self):
        api=native_canvas.session_class()(native_canvas.HOST,self.profile,cookies=[],persist=False)
        processor=next(h for h in api.opener.handlers if isinstance(h,urllib.request.HTTPCookieProcessor))
        request=urllib.request.Request('https://canvas-user-content.com/files/synthetic',headers={'Cookie':'synthetic=value','Authorization':'synthetic','Referer':native_canvas.HOST})
        processed=processor.http_request(request)
        for header in ('Cookie','Authorization','Referer'): self.assertIsNone(processed.get_header(header))

    def test_final_rate_limit_still_defers_other_transfers(self):
        api=native_canvas.session_class()(native_canvas.HOST,self.profile,cookies=[],persist=False)
        headers=Message(); headers['Retry-After']='120'
        error=urllib.error.HTTPError(native_canvas.HOST+'/courses/5/files/1/download',429,'Throttled',headers,io.BytesIO())
        with patch('urllib.request.build_opener') as opener, patch.object(api._download_gate,'wait'), patch.object(api._download_gate,'defer') as defer, patch('native_canvas.time.monotonic',side_effect=itertools.count(0,121)):
            opener.return_value.open.side_effect=error
            with self.assertRaises(urllib.error.HTTPError): api.download_file(error.url,self.profile/'notice.txt')
            self.assertEqual(opener.return_value.open.call_count,3)
            self.assertEqual(defer.call_count,3)
            self.assertTrue(all(call.args==(120,) for call in defer.call_args_list))

    def test_default_migrates_and_preserves_custom_interval_and_preferences(self):
        self.assertEqual(sync_settings({})['syncIntervalSeconds'], 3600)
        self.assertEqual(sync_settings({'syncIntervalSeconds':1800})['syncIntervalSeconds'],3600)
        saved = {'autoSync':False,'theme':'saved','syncIntervalSeconds':1800,'syncIntervalSource':'custom'}
        self.assertEqual(sync_settings(saved), saved)
        self.assertEqual(sync_settings({'syncIntervalSeconds':900})['syncIntervalSeconds'],900)
        for invalid in (True, float('nan'), -10, '3600'):
            self.assertEqual(sync_settings({'syncIntervalSeconds':invalid})['syncIntervalSeconds'],3600)

    def test_hourly_boundaries_disabled_busy_and_wake_do_not_duplicate_sync(self):
        at = datetime(2026,10,6,12,tzinfo=server.HK)
        snapshot = {'collected_at':at.isoformat()}
        index = {'version':server.EVIDENCE_VERSION,'completedAt':at.isoformat()}
        with patch.dict(server.JOB, {'busy':False,'needsLogin':False,'lastAttempt':100}):
            due = lambda seconds, mono, settings={}: server.automatic_sync_due(settings, {'user_id':'1'}, snapshot, index, True, at+timedelta(seconds=seconds), mono)
            self.assertFalse(due(1800,1900))
            self.assertFalse(due(3599,3699))
            self.assertTrue(due(3600,3700))
            self.assertFalse(due(7200,7300,{'autoSync':False}))
            server.JOB['busy'] = True
            self.assertFalse(due(86400,86500))
            server.JOB['busy'] = False; server.JOB['lastAttempt'] = 86500
            self.assertFalse(due(86420,86520))


if __name__ == '__main__': unittest.main()
