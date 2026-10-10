"""Synthetic account/search/date/download/shutdown regression scenarios."""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '学习控制台'))
sys.path.insert(0, str(ROOT / 'WindowsApp'))
import desktop
import native_canvas
import platform_support
from assessment_reader import readings
from canvas_api import Canvas, CanvasPagingError
from canvas_archive import archive_courses, safe_file, safe_component
from canvas_sync import collect
from document_search import search
from freshness import summary
from change_detection import changes
from native_verify import sample_pdf
from release_build import CONSOLE_FILES, FORBIDDEN


class FeatureChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='polyu-feature-')
        self.root = Path(self.temp.name)
        self.profile = self.root / 'account'
        self.documents = self.profile / 'documents'
        self.write(self.profile / 'config.json', {'user':{'id':'99'}, 'courses':[{'id':1,'code':'TEST'}]})
        self.doc = {'id':'1:file:10','courseId':'1','fileId':'10','course':'TEST','title':'Lecture.pdf',
            'type':'file','url':native_canvas.HOST+'/courses/1/files/10','fingerprint':'sample','status':'read'}
        self.index = {'accountId':'99','records':[self.doc]}
        self.write(self.documents / 'index.json', self.index)
        self.write(self.documents / 'cache/1-10.json', {'fingerprint':'sample','pages':[{'page':2,'text':'Original ecology evidence','ocrRows':['图片考试范围'],'processed':True}]})

    def tearDown(self): self.temp.cleanup()

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_search_original_text_and_ocr_keep_page_and_source(self):
        for query,expected in [('ecology','Original ecology evidence'),('考试范围','图片考试范围')]:
            with self.subTest(query=query):
                result = search(self.profile,query)['results'][0]
                self.assertIn(expected, result['excerpt'])
                self.assertEqual(result['page'],2)
                self.assertEqual(result['url'],self.doc['url'])

    def test_search_does_not_read_other_account(self):
        self.index['accountId']='100'; self.write(self.documents / 'index.json', self.index)
        with self.assertRaises(ValueError): search(self.profile,'ecology')

    def test_search_rejects_stale_fingerprint(self):
        self.doc['fingerprint']='changed'; self.write(self.documents / 'index.json',self.index)
        self.assertEqual(search(self.profile,'ecology')['results'],[])

    def test_search_rejects_unlisted_course_or_external_source(self):
        for key,value in [('courseId','2'),('url','https://example.com/courses/1/files/10')]:
            with self.subTest(key=key):
                doc = {**self.doc,key:value}; self.write(self.documents / 'index.json',{'records':[doc]})
                self.assertEqual(search(self.profile,'ecology')['results'],[])

    @unittest.skipIf(sys.platform=='win32', 'Symlink fixture needs no extra Windows privilege')
    def test_search_rejects_symlink_cache(self):
        cache = self.documents / 'cache/1-10.json'
        outside = self.root / 'outside.json'; outside.write_bytes(cache.read_bytes())
        cache.unlink(); cache.symlink_to(outside)
        self.assertEqual(search(self.profile,'ecology')['results'],[])

    def test_search_web_text_requires_matching_account(self):
        import hashlib
        path='/courses/1/pages/notice'
        self.write(self.documents/'index.json',{'records':[{**self.doc,'fileId':None,'type':'page','url':native_canvas.HOST+path}]})
        saved = self.documents/'web/1'/ (hashlib.sha256(path.encode()).hexdigest()+'.json')
        self.write(saved,{'accountId':'99','path':path,'rows':['Ecology original notice']})
        self.assertIn('网页正文行',search(self.profile,'ecology')['results'][0]['locator'])
        self.write(saved,{'accountId':'100','path':path,'rows':['Ecology original notice']})
        self.assertEqual(search(self.profile,'ecology')['results'],[])

    def test_search_reports_result_limit(self):
        self.write(self.documents/'cache/1-10.json',{'fingerprint':'sample','pages':[{'page':i,'text':'ecology'} for i in range(1,4)]})
        result=search(self.profile,'ecology',limit=2)
        self.assertEqual(len(result['results']),2); self.assertTrue(result['truncated'])

    def test_search_bounds_query_and_rejects_traversal_id(self):
        with self.assertRaises(ValueError): search(self.profile,'x'*101)
        self.doc['courseId']='../account'; self.write(self.documents/'index.json',self.index)
        self.assertEqual(search(self.profile,'ecology')['results'],[])

    def test_partial_attempt_keeps_last_complete_time(self):
        old='2026-10-05T00:00:00+00:00'
        self.write(self.profile/'snapshot.json',{'collected_at':old,'complete':True,'assignments':{'20':{'course':'TEST','name':'Original','due_at':'2026-10-12T00:00:00Z'}}})
        api=Mock(); api.get.side_effect=CanvasPagingError('incomplete page')
        course={'id':1,'code':'TEST','name':'Synthetic','term':'','start':None,'end':None}
        with patch('canvas_sync.current_courses',return_value=[course]): collect(api,self.profile,{'id':'99'})
        result=json.loads((self.profile/'snapshot.json').read_text())
        self.assertFalse(result['complete']); self.assertEqual(result['last_complete_at'],old)
        self.assertEqual(result['assignments']['20']['name'],'Original')
        self.assertEqual(summary(result)['state'],'partial')

    def test_due_date_retains_personalized_api_date_and_hong_kong_time(self):
        api=Mock(); api.get.side_effect=[[{'id':21,'name':'Essay','due_at':'2026-10-12T16:00:00Z',
            'unlock_at':'2026-10-01T00:00:00Z','lock_at':'2026-10-20T00:00:00Z','submission':{}}],[]]
        course={'id':1,'code':'TEST','name':'Synthetic','term':'','start':None,'end':None}
        with patch('canvas_sync.current_courses',return_value=[course]): collect(api,self.profile,{'id':'99'})
        result=json.loads((self.profile/'snapshot.json').read_text())['assignments']['21']
        import server
        self.assertEqual(server.local_time(result['due_at']).isoformat(),'2026-10-13T00:00:00+08:00')
        self.assertNotEqual(result['due_at'],result['lock_at'])

    def test_incomplete_roster_never_claims_complete_sync(self):
        api=Mock(); api.get.return_value=[]
        course={'id':1,'code':'TEST','name':'Synthetic','term':'','start':None,'end':None,'rosterIncomplete':True}
        with patch('canvas_sync.current_courses',return_value=[course]): collect(api,self.profile,{'id':'99'})
        self.assertFalse(json.loads((self.profile/'snapshot.json').read_text())['complete'])

    def test_freshness_old_complete_and_absent_snapshots(self):
        now=datetime(2026,10,6,tzinfo=timezone.utc)
        self.assertEqual(summary({},now)['state'],'missing')
        self.assertEqual(summary({'complete':True,'collected_at':'2026-10-01T00:00:00Z'},now)['state'],'stale')
        self.assertEqual(summary({'complete':True,'collected_at':'2026-10-05T23:00:00Z'},now)['state'],'current')

    def test_unknown_ocr_never_fabricates_confidence(self):
        pairs,page=next(readings({'rows':['Exam 12 Oct 2026'],'rowConfidences':[None],'confidenceUnknown':True}))
        self.assertEqual(pairs[0][1],0.0); self.assertTrue(page['confidenceUnknown'])

    def test_local_pdf_fixture_extracts_text_and_tables(self):
        from windows_document import read
        from pdf_layout import read_layout
        path=self.root/'synthetic.pdf'; sample_pdf(path)
        self.assertIn('Assessment',read(path,text_only=True)[0]['text'])
        self.assertTrue(read_layout(path,[1])['1']['tables'])

    def test_windows_plaintext_cookie_is_rejected(self):
        path=self.root/'canvas-cookies.json'; self.write(path,[{'name':'synthetic','value':'sample'}])
        with patch.object(native_canvas,'IS_WINDOWS',True),patch.object(native_canvas,'dpapi') as protect:
            with self.assertRaises(RuntimeError): native_canvas.load_cookies(path)
            protect.assert_not_called()

    def test_dpapi_write_failure_leaves_no_plaintext_or_directory(self):
        path=self.root/'new/canvas-cookies.json'
        with patch.object(native_canvas,'IS_WINDOWS',True),patch.object(native_canvas,'dpapi',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError): native_canvas.save_private(path,[{'name':'s','value':'synthetic'}])
        self.assertFalse(path.parent.exists())

    def test_mutating_get_aliases_and_redirects_are_rejected(self):
        client=native_canvas.session_class()(native_canvas.HOST,self.profile,cookies=[],persist=False)
        aliases=('media','media.json','media%2ejson/','MEDIA/','%256dedia','icon_maker','icon_maker.json','x/../media')
        for alias in aliases:
            url=native_canvas.HOST+'/api/v1/courses/1/folders/'+alias
            with self.subTest(alias=alias),patch.object(client.opener,'open') as opener:
                with self.assertRaises(ValueError): client.fetch(url)
                opener.assert_not_called()
                redirects=[h for h in client.opener.handlers if isinstance(h,urllib.request.HTTPRedirectHandler)]
                with self.assertRaises(ValueError): redirects[0].redirect_request(urllib.request.Request(native_canvas.HOST),None,302,'',Message(),url)

    def test_conditional_download_304_reuses_only_existing_file(self):
        path=self.root/'file.pdf'; path.write_bytes(b'synthetic')
        api=native_canvas.session_class()(native_canvas.HOST,self.profile,cookies=[],persist=False)
        error=urllib.error.HTTPError(native_canvas.HOST+'/courses/1/files/10/download',304,'Not Modified',Message(),io.BytesIO())
        opener=Mock(); opener.open.side_effect=error
        with patch('native_canvas.urllib.request.build_opener',return_value=opener):
            self.assertEqual(api.download_file(error.url,path,validators={'etag':'"same"'}),9)
            self.assertEqual(api.last_download['transfer'],'revalidated')
            self.assertEqual(opener.open.call_args.args[0].get_header('If-none-match'),'"same"')
            path.unlink()
            with self.assertRaises(urllib.error.HTTPError): api.download_file(error.url,path,validators={'etag':'"same"'})

    def test_archive_path_traversal_is_rejected(self):
        for relative in ('../outside.pdf',str(self.root/'outside.pdf')):
            with self.assertRaises(ValueError): safe_file(self.documents,relative)

    def test_long_chinese_filename_can_be_created_without_losing_suffix(self):
        name = safe_component('课程😀资料' * 100 + '.pdf', 'file')
        self.assertLessEqual(len(name.encode('utf-8')), 230)
        self.assertLessEqual(len(name.encode('utf-16-le')), 320)
        self.assertTrue(name.endswith('.pdf'))
        target = self.root / (Path(name).stem + ' [1234567890].pdf')
        target.write_bytes(b'synthetic')
        self.assertEqual(target.read_bytes(), b'synthetic')

    def test_random_download_temp_does_not_overwrite_a_course_filename(self):
        path = self.root / 'course.pdf'
        legitimate = self.root / 'course.pdf.download'
        legitimate.write_bytes(b'preserve this separate file')
        api = native_canvas.session_class()(native_canvas.HOST, self.profile, cookies=[], persist=False)
        def response(*args, **kwargs):
            value = Mock()
            value.headers = Message(); value.headers['Content-Type'] = 'application/pdf'
            value.headers['Content-Length'] = '9'
            value.read.side_effect = [b'synthetic', b'']
            value.__enter__ = Mock(return_value=value); value.__exit__ = Mock(return_value=False)
            return value
        opener = Mock(); opener.open.side_effect = response
        with patch('native_canvas.urllib.request.build_opener', return_value=opener), \
             patch('native_canvas.os.open', wraps=native_canvas.os.open) as opened:
            for _ in range(2): api.download_file(native_canvas.HOST + '/courses/1/files/10/download', path)
        temporary = [Path(call.args[0]) for call in opened.call_args_list]
        self.assertEqual(len(set(temporary)), 2)
        self.assertTrue(all(p.name.startswith('.download-') for p in temporary))
        self.assertTrue(all(call.args[1] & native_canvas.os.O_EXCL for call in opened.call_args_list))
        self.assertTrue(all(not p.exists() for p in temporary))
        self.assertEqual(legitimate.read_bytes(), b'preserve this separate file')
        self.assertEqual(path.read_bytes(), b'synthetic')

    def test_junction_archive_root_is_rejected(self):
        with patch.object(platform_support, 'IS_WINDOWS', True), \
             patch.object(Path, 'is_junction', lambda path: path == self.documents, create=True):
            with self.assertRaises(ValueError): safe_file(self.documents, 'file.pdf')

    def test_junction_text_cache_is_not_searched(self):
        with patch.object(platform_support, 'IS_WINDOWS', True), \
             patch.object(Path, 'is_junction', lambda path: path == self.documents / 'cache', create=True):
            self.assertEqual(search(self.profile, 'ecology')['results'], [])

    def test_junction_profile_blocks_logout_without_deleting_target(self):
        with patch.object(platform_support, 'IS_WINDOWS', True), \
             patch.object(Path, 'is_junction', lambda path: path == self.profile, create=True):
            with self.assertRaises(RuntimeError): desktop.delete_account(self.profile, Mock())
        self.assertTrue((self.profile / 'config.json').exists())

    def test_child_junction_is_removed_without_recursing(self):
        root = self.root / 'logout-junction'; child = root / 'linked-directory'
        child.mkdir(parents=True)
        with patch.object(platform_support, 'IS_WINDOWS', True), \
             patch.object(Path, 'is_junction', lambda path: path == child, create=True), \
             patch('desktop.shutil.rmtree') as recursive:
            desktop.delete_account(root, Mock())
            recursive.assert_not_called()
        self.assertFalse(root.exists())

    @unittest.skipIf(sys.platform == 'win32', 'Synthetic symlink requires no Windows privilege')
    def test_logout_marker_cannot_overwrite_a_symlink_target(self):
        root = self.root / 'logout-app'; root.mkdir()
        outside = self.root / 'separate-file'; outside.write_text('preserve')
        (root / 'logout-pending').symlink_to(outside)
        with self.assertRaises(RuntimeError): desktop.delete_account(root, Mock())
        self.assertEqual(outside.read_text(), 'preserve')

    def test_frozen_worker_is_used_for_backend_and_pipe_helper(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'PolyUStudyConsole.exe')),patch.object(platform_support,'IS_WINDOWS',True):
            self.assertTrue(desktop.entry_command('--backend')[0].endswith('PolyUWorker.exe'))
            self.assertTrue(platform_support.helper_command('pdf_layout.py','file.pdf','[1]')[0].endswith('PolyUWorker.exe'))

    def test_logout_failure_keeps_marker_and_blocks_restart(self):
        root=self.root/'logout-app'; (root/'account').mkdir(parents=True)
        (root/'account/file').write_text('synthetic')
        with patch('desktop.shutil.rmtree',side_effect=PermissionError('locked')),patch('desktop.time.sleep'):
            with self.assertRaises(RuntimeError): desktop.delete_account(root,Mock())
            self.assertTrue((root/'logout-pending').exists())
            app=desktop.Desktop(root,Mock()); app.backend=Mock()
            with self.assertRaises(RuntimeError): app.run()
            app.backend.start.assert_not_called()

    def test_logout_stops_writer_before_removing_all_profile_data(self):
        root=self.root/'logout-app'; (root/'account/documents').mkdir(parents=True)
        (root/'account/documents/cache').write_text('synthetic')
        backend=Mock()
        backend.stop.side_effect=lambda:self.assertTrue((root/'account/documents/cache').exists())
        desktop.delete_account(root,backend)
        backend.stop.assert_called_once(); self.assertFalse(root.exists())

    def test_bridge_refuses_school_origin_and_arbitrary_action(self):
        app=Mock(stopping=False); app.backend.url='http://127.0.0.1:8766/'
        app.console.get_current_url.return_value=native_canvas.HOST
        with self.assertRaises(ValueError): desktop.Bridge(app).native_action('login')
        app.console.get_current_url.return_value=app.backend.url
        with self.assertRaises(ValueError): desktop.Bridge(app).native_action('arbitrary')
        with self.assertRaises(ValueError): desktop.Bridge(app).native_action('openSource','https://example.com')

    def test_local_secret_transport_disables_proxy_and_redirect(self):
        backend=desktop.Backend(self.root)
        self.assertTrue(any(isinstance(h,desktop.NoRedirect) for h in backend.opener.handlers))
        self.assertFalse(any(isinstance(h,urllib.request.ProxyHandler) and h.proxies for h in backend.opener.handlers))

    def test_bundle_allowlist_excludes_account_data(self):
        self.assertFalse(set(CONSOLE_FILES)&FORBIDDEN)
        self.assertIn('release.json',CONSOLE_FILES)

    def test_first_sync_and_timestamp_order_changes_do_not_notify(self):
        row={'name':'Essay','course':'TEST','due_at':'2026-10-12','updated_at':'old'}
        self.assertEqual(changes({}, {'1':row}, [], []), [])
        previous={'collected_at':'before','complete':True,'assignments':{'1':row}}
        self.assertEqual(changes(previous,{'1':{**row,'updated_at':'new'}},[],[]),[])

    def test_published_score_and_due_date_changes_are_observed_by_id(self):
        row={'name':'Same title','course':'TEST','due_at':'2026-10-12','score':None}
        previous={'collected_at':'before','complete':True,'assignments':{'1':row,'2':row}}
        result=changes(previous,{'2':row,'1':{**row,'due_at':'2026-10-14','score':70}},[],[])
        self.assertEqual(len(result),1)
        self.assertIn('截止日期',result[0]['detail']); self.assertIn('分数',result[0]['detail'])

    def test_partial_or_missing_rows_never_infer_deletion(self):
        previous={'collected_at':'before','complete':False,'assignments':{'1':{'name':'Essay'}}}
        self.assertEqual(changes(previous,{},[],[]),[])
        self.assertEqual(changes(previous,{'2':{'name':'Essay'}},[],[]),[])

    def test_announcement_content_ignores_timestamp_and_whitespace(self):
        old={'id':1,'title':'Notice','message':'<p>Original text</p>','posted_at':'before'}
        previous={'collected_at':'before','complete':True}
        new={**old,'message':'<p>Original   text</p>','posted_at':'after'}
        self.assertEqual(changes(previous,{},[old],[new]),[])
        self.assertEqual(len(changes(previous,{},[old],[{**new,'message':'Changed text'}])),1)


if __name__=='__main__': unittest.main(verbosity=2)
