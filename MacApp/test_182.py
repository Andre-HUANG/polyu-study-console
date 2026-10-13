"""Focused 1.8.2 behavior, isolation and failure-boundary regressions."""
import copy
import datetime as dt
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"学习控制台"))
sys.path.insert(0,str(ROOT/"WindowsApp"))
import academic_calendar as calendar
import timetable
import timetable_reader
import document_text
import runtime_cache
from native_canvas import save_private
from update_transaction import unpack_payload,replace_portable,restore,safe_program,cleanup_guard
from updater import verify_metadata

class CalendarAndTimetableChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.profile=pathlib.Path(self.temp.name)/"account";self.profile.mkdir()
        self.config={"user":{"id":"1"},"term":{"start":"2026-08-31","end":"2027-08-29"},"courses":[{"id":str(i),"code":"COURSE"+str(i)} for i in range(1,24)]}
        calendar.clear_memory()
    def tearDown(self):
        calendar.clear_memory();runtime_cache.clear();self.temp.cleanup()
    def row(self,**extra):
        value={"courseId":"1","title":"Lecture","kind":"lecture","date":"2026-10-13","time":"18:30","endTime":"20:30",**extra}
        if value.get("action") in {"confirm","hide","reset"} and "digest" not in value:
            saved=json.loads((self.profile/"documents/timetable.json").read_text())
            rule=next(r for r in saved["rules"] if r["id"]==value["id"])
            value["digest"]=rule["digest"]
        return value
    def read(self,text,**extra):
        doc={"id":"doc","courseId":"1","course":"COURSE1","title":"Teaching timetable","url":"https://canvas.polyu.edu.hk/courses/1/files/3"}
        return timetable_reader.rules_from(doc,[{"page":1,"text":text,"rows":text.splitlines(),**extra}],self.config)
    def test_calendar_context_batches_cover_more_than_ten_courses(self):
        calls=[]
        class API:
            def get(s,path):
                from urllib.parse import parse_qs,urlsplit
                args=parse_qs(urlsplit(path).query);calls.append(args)
                return [{"id":code,"context_code":code,"title":"Lecture","start_at":"2026-10-13T10:30:00Z"} for code in args["context_codes[]"]]
        value=timetable.collect(API(),self.profile,self.config)
        self.assertEqual([len(c["context_codes[]"]) for c in calls],[10,10,3])
        self.assertEqual(len(value["events"]),23);self.assertTrue(all(c["type"]==["event"] for c in calls))
    def test_partial_calendar_preserves_own_previous_events(self):
        old={"accountId":"1","events":[{"context_code":"course_1","id":1}],"issues":[]}
        save_private(self.profile/"documents/calendar-events.json",old)
        class API:
            def get(self,path): raise PermissionError()
        value=timetable.collect(API(),self.profile,self.config)
        self.assertEqual(value["events"],old["events"]);self.assertEqual(len(value["issues"]),23)
        self.config["user"]["id"]="2"
        self.assertEqual(timetable.collect(API(),self.profile,self.config)["events"],[])
    def test_canvas_due_date_is_never_an_exam_start(self):
        courses={"1":{"code":"C1"}}
        rows=[{"id":1,"context_code":"course_1","title":"Exam","due_at":"2026-10-13T10:30:00Z"}]
        self.assertEqual(timetable._canvas_rules({"events":rows},courses),[])
    def test_conflicting_exam_sources_stay_pending_in_both_modules(self):
        from course_evidence import facts_from,consolidate
        records=[]
        for number,date in [(1,"24"),(2,"25")]:
            text="Midterm test "+date+" October 2026 19:00"
            document={"type":"file","id":"document-"+str(number),"courseId":"1","course":"COURSE1","title":"Assessment notice","url":"https://canvas.polyu.edu.hk/courses/1/files/"+str(number)}
            pages=[{"page":1,"text":text,"rows":[text]}]
            records.append({**document,"facts":facts_from(document,pages,self.config),"timetableRules":timetable_reader.rules_from(document,pages,self.config)})
        index={"records":records,"evidence":consolidate(records)}
        self.assertTrue(index["evidence"]["pending"])
        value=timetable.rebuild(self.profile,self.config,index)
        self.assertEqual(len(value["rules"]),2)
        self.assertTrue(all(any("不一致" in issue for issue in r["issues"]) for r in value["rules"]))
        result=timetable.query(self.profile,self.config,"2026-10-24")
        self.assertFalse(result["events"]);self.assertEqual(len(result["pending"]),2)

    def test_source_dates_times_and_classroom_are_preserved(self):
        rules=self.read("Lecture Monday 18:30–21:30 Location: QR601\nExam 24 October 2026 19:00–21:00")
        self.assertEqual(len(rules),2)
        self.assertEqual(rules[0]["repeat"]["weekdays"],[0])
        self.assertEqual(rules[0]["location"],"QR601")
        self.assertEqual(rules[1]["dates"],["2026-10-24"])
        self.assertEqual(rules[1]["endTime"],"21:00")
        self.assertIn("24 October",rules[1]["sources"][0]["quote"])
    def test_scientific_test_dates_are_not_personal_exams(self):
        for text in ["Clinical trial test 12 October 2026 concentration measured",
                     "Lecture schedule\nResults published 12 October 2026 doi:10.1000/test",
                     "Laboratory test ISO 2026 measured 12 October 2026"]:
            self.assertEqual(self.read(text),[])

    def test_confirmation_preserves_evidence_and_confidence_after_source_revision(self):
        document={"type":"file","id":"doc","courseId":"1","course":"COURSE1","title":"Teaching timetable","url":"https://canvas.polyu.edu.hk/courses/1/files/3"}
        rule=self.read("Lecture 13 October 2026 18:30–20:30",confidence=.7)[0]
        index={"records":[{**document,"timetableRules":[rule]}]}
        generated=timetable.rebuild(self.profile,self.config,index)["rules"][0]
        timetable.mutate(self.profile,self.config,self.row(action="confirm",id=generated["id"]))
        event=timetable.query(self.profile,self.config,"2026-10-13")["events"][0]
        self.assertEqual(event["sources"],generated["sources"])
        self.assertEqual(event["confidence"],generated["confidence"])
        self.assertTrue(event["manualDecision"])
        rule["sources"][0]["quote"]+=" revised";rule["digest"]="revised"
        updated=timetable.rebuild(self.profile,self.config,index)["rules"][0]
        event=timetable.query(self.profile,self.config,"2026-10-13")["events"][0]
        self.assertEqual(event["sources"],updated["sources"])
        timetable.rebuild(self.profile,self.config,{"records":[]})
        event=timetable.query(self.profile,self.config,"2026-10-13")["events"][0]
        self.assertEqual(event["sources"],generated["sources"])

    def test_stale_confirmation_or_hide_cannot_replace_a_revised_source(self):
        rule=self.read("Lecture 13 October 2026 18:30–20:30")[0]
        index={"records":[{"courseId":"1","timetableRules":[rule]}]}
        shown=timetable.rebuild(self.profile,self.config,index)["rules"][0]
        rule["sources"][0]["quote"]+=" revision";rule["digest"]="revised"
        current=timetable.rebuild(self.profile,self.config,index)["rules"][0]
        self.assertNotEqual(shown["digest"],current["digest"])
        for action in ("confirm","hide"):
            with self.assertRaisesRegex(ValueError,"来源已更新"):
                timetable.mutate(self.profile,self.config,self.row(action=action,id=shown["id"],digest=shown["digest"]))
        self.assertEqual(timetable.manual(self.profile,self.config)["decisions"],{})

    def test_week_numbers_never_extend_before_anchor(self):
        timetable.mutate(self.profile,self.config,self.row(action="save",repeat={"start":"2026-08-31","end":"2026-10-31","weekdays":[0],"parity":"odd","weeks":[],"basis":"anchor","anchor":"2026-09-14"}))
        self.assertFalse(timetable.query(self.profile,self.config,"2026-08-31")["events"])
        self.assertEqual(len(timetable.query(self.profile,self.config,"2026-09-14")["events"]),1)
    def test_partial_source_removal_does_not_duplicate_merged_manual_override(self):
        one=self.read("Lecture 13 October 2026 18:30–20:30")[0]
        two=copy.deepcopy(one);two["id"]="second-source";two["sources"][0]["documentId"]="second-document"
        index={"records":[{"courseId":"1","timetableRules":[one]},{"courseId":"1","timetableRules":[two]}]}
        generated=timetable.rebuild(self.profile,self.config,index)["rules"][0]
        self.assertEqual(len(generated["sourceIds"]),2)
        timetable.mutate(self.profile,self.config,self.row(action="confirm",id=generated["id"]))
        timetable.rebuild(self.profile,self.config,{"records":index["records"][:1]})
        result=timetable.query(self.profile,self.config,"2026-10-13")
        self.assertEqual(len(result["events"]),1);self.assertEqual(len(result["conflicts"]),1)
    def test_lab_cancellation_does_not_cancel_lecture_or_exam(self):
        for kind,title,time,end in [("lecture","Lecture","09:00","11:00"),("lab","Lab","12:00","14:00"),("exam","Exam","15:00","17:00")]:
            timetable.mutate(self.profile,self.config,self.row(action="save",kind=kind,title=title,time=time,endTime=end))
        timetable.mutate(self.profile,self.config,self.row(action="save",kind="lab",title="Lab cancelled",time="12:00",endTime="14:00",cancelled=True))
        result=timetable.query(self.profile,self.config,"2026-10-13")
        self.assertEqual([r["kind"] for r in result["events"] if r["cancelled"]],["lab"])
    def test_ambiguous_cancellation_remains_pending(self):
        for time,end in [("09:00","11:00"),("13:00","15:00")]:
            timetable.mutate(self.profile,self.config,self.row(action="save",kind="lab",title="Lab",time=time,endTime=end))
        timetable.mutate(self.profile,self.config,self.row(action="save",kind="lab",title="Lab cancelled",time="",endTime="",cancelled=True))
        result=timetable.query(self.profile,self.config,"2026-10-13")
        self.assertTrue(all(not r["cancelled"] for r in result["events"]));self.assertEqual(len(result["pending"]),1)
    def test_dashboard_summary_bounds_rows_and_defers_original_sources(self):
        row={"id":"candidate","sources":[{"quote":"large original excerpt "*1000}],"dates":["2026-10-13"],"kind":"exam"}
        value={"events":[copy.deepcopy(row) for _ in range(100)],"pending":[copy.deepcopy(row) for _ in range(1000)],"conflicts":[]}
        summary=timetable.compact_summary(value)
        self.assertEqual(len(summary["events"]),80);self.assertEqual(len(summary["pending"]),20)
        self.assertEqual(summary["pendingTotal"],1000)
        self.assertTrue(all("sources" not in r for r in summary["pending"]));self.assertIn("sources",value["pending"][0])

    def test_multiple_dates_survive_manual_confirmation(self):
        timetable.mutate(self.profile,self.config,self.row(action="save",dates=["2026-10-13","2026-10-20"]))
        for date in ["2026-10-13","2026-10-20"]:
            self.assertEqual(len(timetable.query(self.profile,self.config,date)["events"]),1)

    def test_unpublished_calendar_month_has_explicit_coverage_message(self):
        value=calendar.month_summary(None,None,"2029-09")
        self.assertIsNone(value["year"]);self.assertIn("尚未发布",value["issue"])

    def test_matching_native_text_corroborates_low_ocr_schedule_row(self):
        weak=self.read("Exam 24 October 2026 19:00",rowConfidences=[.4])[0]
        strong=self.read("Exam 24 October 2026 19:00")[0];strong["id"]="other"
        rules=timetable.build_rules(self.config,{"records":[{"courseId":"1","timetableRules":[weak,strong]}]})["rules"]
        self.assertEqual(len(rules),1);self.assertFalse(rules[0]["issues"])
        self.assertEqual(sorted(s["confidence"] for s in rules[0]["sources"]),[.4,1.0])

    def test_low_ocr_confidence_is_pending_and_native_reading_survives(self):
        rules=self.read("Exam 24 October 2026 19:00",rowConfidences=[.4])
        self.assertIn("OCR 行置信度不足",rules[0]["issues"])
    def test_unknown_group_and_week_anchor_are_pending(self):
        rules=self.read("Lecture Group A Monday odd weeks 18:30–21:30")
        self.assertIn("请选择所属班组",rules[0]["issues"])
        self.assertIn("教学周定义待确认",rules[0]["issues"])
    def test_explicit_semester_weeks_skip_lunar_new_year_break(self):
        row=self.row(repeat={"start":"2027-01-11","end":"2027-04-17","weekdays":[0],"basis":"semester","semester":2,"weeks":[5],"parity":"all"})
        self.assertTrue(timetable._matches(row,"2027-02-15",calendar.base()))
        self.assertFalse(timetable._matches(row,"2027-02-08",calendar.base()))
    def test_odd_even_and_specified_anchor_weeks(self):
        row=self.row(repeat={"start":"2026-08-31","end":"2026-11-28","weekdays":[0],"basis":"anchor","anchor":"2026-08-31","parity":"odd","weeks":[1,3,5]})
        row.pop("date",None)
        self.assertTrue(timetable._matches(row,"2026-09-14",calendar.base()))
        self.assertFalse(timetable._matches(row,"2026-09-07",calendar.base()))
    def test_calendar_all_day_evening_and_cross_academic_year(self):
        self.assertTrue(any(h["scope"]=="evening" for h in calendar.hints("2026-09-25")))
        self.assertTrue(any(h["scope"]=="all" for h in calendar.hints("2027-02-08")))
        self.assertEqual(calendar.selected_year(calendar.base(),dt.date(2027,8,30))["year"],"2027/28")
        self.assertEqual(calendar.month_summary(None,None,"2027-09")["year"],"2027/28")
    def test_non_schedule_prose_does_not_reassign_manual_decision(self):
        first=self.read("Exam 24 October 2026 19:00")[0]
        changed=self.read("General introductory prose\nExam 25 October 2026 20:00")[0]
        self.assertEqual(first["id"],changed["id"])
        self.assertNotEqual(first["digest"],changed["digest"])

    def test_removed_source_preserves_confirmed_manual_arrangement(self):
        rule=self.read("Exam 24 October 2026 19:00")[0]
        value=timetable.rebuild(self.profile,self.config,{"records":[{"courseId":"1","timetableRules":[rule]}]})
        timetable.mutate(self.profile,self.config,self.row(action="confirm",id=value["rules"][0]["id"],date="2026-10-24",kind="exam"))
        timetable.rebuild(self.profile,self.config,{"records":[]})
        value=timetable.query(self.profile,self.config,"2026-10-24")
        self.assertEqual(len(value["events"]),1)
        self.assertEqual(len(value["conflicts"]),1)

    def test_manual_exam_without_time_stays_untimed(self):
        timetable.mutate(self.profile,self.config,self.row(action="save",kind="exam",time="",endTime=""))
        event=timetable.query(self.profile,self.config,"2026-10-13")["events"][0]
        self.assertEqual(event["time"],"");self.assertEqual(event["endTime"],"")
    def test_time_overlap_and_explicit_cancellation(self):
        for row in [self.row(action="save"),self.row(action="save",title="Lab",kind="lab",time="20:00",endTime="21:00")]:
            timetable.mutate(self.profile,self.config,row)
        rows=timetable.query(self.profile,self.config,"2026-10-13")["events"]
        self.assertTrue(all(r["overlaps"] for r in rows))
        timetable.mutate(self.profile,self.config,self.row(action="save",title="No class",cancelled=True))
        rows=timetable.query(self.profile,self.config,"2026-10-13")["events"]
        self.assertEqual([r["kind"] for r in rows if r["cancelled"]],["lecture"])
        self.assertFalse(next(r for r in rows if r["kind"]=="lab")["cancelled"])
    def test_manual_recurring_exception_and_makeup(self):
        row=self.row(action="save",repeat={"start":"2026-10-01","end":"2026-10-31","weekdays":[1],"parity":"all"},exceptions=["2026-10-13"])
        row.pop("date");timetable.mutate(self.profile,self.config,row)
        self.assertFalse(timetable.query(self.profile,self.config,"2026-10-13")["events"])
        timetable.mutate(self.profile,self.config,self.row(action="save",date="2026-10-14",makeup=True))
        self.assertTrue(timetable.query(self.profile,self.config,"2026-10-14")["events"][0]["makeup"])
    def test_manual_decisions_survive_sync_and_signal_source_revision(self):
        rules=self.read("Exam 24 October 2026 19:00")
        index={"records":[{"courseId":"1","timetableRules":rules}]}
        result=timetable.rebuild(self.profile,self.config,index)
        timetable.mutate(self.profile,self.config,self.row(action="hide",id=result["rules"][0]["id"]))
        self.assertFalse(timetable.query(self.profile,self.config,"2026-10-24")["events"])
        index["records"][0]["timetableRules"][0]["digest"]="revision"
        timetable.rebuild(self.profile,self.config,index)
        value=timetable.query(self.profile,self.config,"2026-10-24")
        self.assertFalse(value["events"]);self.assertEqual(len(value["conflicts"]),1)
    def test_account_isolation_and_mutation_validation(self):
        timetable.mutate(self.profile,self.config,self.row(action="save"))
        other={**self.config,"user":{"id":"2"}}
        self.assertEqual(timetable.query(self.profile,other,"2026-10-13")["events"],[])
        for row in [self.row(courseId="999"),self.row(time="25:00"),self.row(endTime="17:00"),self.row(date="invalid")]:
            with self.assertRaises(ValueError):timetable.validate_rule(row,self.config)
    def test_html_has_priority_and_combines_published_ranges(self):
        html="<table><tr><td>2026</td></tr><tr><td>August</td></tr><tr><td>31</td><td>Monday</td><td>Semester One teaching commences</td></tr><tr><td>December</td></tr><tr><td>3</td><td>Thursday</td><td>Examination Period for Semester One commences</td></tr><tr><td>18</td><td>Friday</td><td>Examination Period for Semester One ends</td></tr><tr><td>2027</td></tr><tr><td>August</td></tr><tr><td>29</td><td>Sunday</td><td>Academic Year 2026/27 ends</td></tr></table>"
        values=calendar.parse_html(html)
        period=next(e for e in values[0]["events"] if e["kind"]=="exam_period")
        self.assertEqual((period["start"],period["end"]),("2026-12-03","2026-12-18"))
        self.assertEqual(period["source"],calendar.SOURCE)
    def test_failed_refresh_retains_previous_and_backs_off(self):
        prior=calendar.base()
        with patch.object(calendar,"_fetch",side_effect=ConnectionError()):
            result=calendar.refresh()
        self.assertEqual(result["years"],prior["years"]);self.assertTrue(result["issue"])
        self.assertFalse(calendar.due())
    def test_cancelled_refresh_never_recreates_account_directory(self):
        def cancel(): raise RuntimeError("cancelled")
        with patch.object(calendar,"_fetch",return_value=(b"",None)):
            with self.assertRaises(RuntimeError):calendar.refresh(self.profile,cancelled=cancel)
        self.assertFalse((self.profile/"calendar").exists())

    def test_saved_file_and_page_upgrade_runs_without_canvas_requests(self):
        import canvas_documents as documents
        file={"type":"file","id":"1:file:12","courseId":"1","course":"COURSE1","fileId":"12","title":"Teaching timetable","url":"https://canvas.polyu.edu.hk/courses/1/files/12","fingerprint":"a"*64,"status":"read","readerVersion":0,"timetableVersion":0}
        text="Lecture 13 October 2026 18:30–20:30"
        root=self.profile/"documents";(root/"cache").mkdir(parents=True)
        save_private(root/"cache/1-12.json",{"accountId":"1","readerVersion":documents.READER_VERSION,"pages":[{"page":1,"text":text,"rows":[text],"method":"native","confidence":1.0,"readable":True,"processed":True}]})
        path="/courses/1/pages/timetable";web=copy.deepcopy(file);web.update(type="page",id="1:page:web",fileId=None,url="https://canvas.polyu.edu.hk"+path)
        (root/"web/1").mkdir(parents=True)
        save_private(root/"web/1"/(hashlib.sha256(path.encode()).hexdigest()+".json"),{"accountId":"1","path":path,"rows":[text]})
        index={"accountId":"1","records":[file,web],"version":0}
        save_private(root/"index.json",index)
        self.assertTrue(documents.needs_cached_upgrade(index,self.config))
        with patch.object(documents,"extract",side_effect=AssertionError("Unexpected file reread")):
            result=documents.reprocess_cached_documents(self.profile,self.config,None,lambda:None,lambda message:None)
        self.assertTrue(all(r["timetableRules"] for r in result["records"]))
        self.assertFalse(documents.needs_cached_upgrade(result,self.config))

class CacheAndOCRChecks(unittest.TestCase):
    def test_timetable_reader_upgrade_schedules_cached_file_reinterpretation(self):
        import server
        now=dt.datetime.now(server.HK)
        index={"version":server.EVIDENCE_VERSION,"completedAt":now.isoformat(),"records":[{"status":"read"}]}
        with patch.dict(server.JOB,busy=False,needsLogin=False,lastAttempt=0):
            self.assertTrue(server.automatic_sync_due({"autoSync":True},{"user_id":"1"},{"collected_at":now.isoformat()},index,True,now,100))
            index["records"][0]["timetableVersion"]=timetable_reader.VERSION
            self.assertFalse(server.automatic_sync_due({"autoSync":True},{"user_id":"1"},{"collected_at":now.isoformat()},index,True,now,100))

    def test_read_cache_file_revision_day_and_account_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/"config.json";save_private(path,{"n":1})
            with runtime_cache.snapshot():
                self.assertEqual(runtime_cache.read_json(path,{}),{"n":1})
                save_private(path,{"n":2});self.assertEqual(runtime_cache.read_json(path,{}),{"n":2})
            keys=[runtime_cache.revision(tmp,epoch,day) for epoch,day in [("a","2026-10-13"),("b","2026-10-13"),("a","2026-10-14")]]
            self.assertEqual(len(set(keys)),3)
    def test_pdf_second_pass_keeps_text_tables_and_confidence(self):
        original=[{"page":1,"text":"Exam 24 October 2026","rows":["Exam 24 October 2026"],"rowConfidences":[1.0],"tables":[{"cells":[["Exam","24 October 2026"]]}],"textOnly":True,"processed":True,"readable":True}]
        with tempfile.TemporaryDirectory() as tmp,patch.object(document_text,"native_read",return_value=[{"page":1,"ocrRows":["Exam"],"ocrRowConfidences":[.88],"processed":True}]) as native,patch.object(document_text,"pdf_tables") as tables:
            result=document_text.extract(pathlib.Path(tmp)/"a.pdf","reader",tmp,cached_pages=original)
        native.assert_called_once();self.assertTrue(native.call_args.kwargs["ocr_only"]);tables.assert_not_called()
        self.assertEqual(result[0]["tables"],original[0]["tables"]);self.assertEqual(result[0]["rows"],original[0]["rows"])
        self.assertEqual(result[0]["ocrRowConfidences"],[.88]);self.assertFalse(result[0]["textOnly"]);self.assertTrue(original[0]["textOnly"])
    def test_pdf_missing_ocr_page_fails_visibly(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(document_text,"native_read",return_value=[]):
            with self.assertRaises(RuntimeError):document_text.extract(pathlib.Path(tmp)/"a.pdf","reader",tmp,cached_pages=[{"page":1}])

class UpdateChecks(unittest.TestCase):
    def test_manifest_signature_version_architecture_and_url(self):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest("Signature dependency is validated by Windows private-runtime CI")
        from cryptography.hazmat.primitives import serialization
        import base64
        key=Ed25519PrivateKey.generate();public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        value={"schema":1,"repository":"Andre-HUANG/polyu-study-console","version":"1.8.3","build":183,"draft":False,"prerelease":False,"artifacts":{"test.zip":{"sha256":"a"*64,"bytes":32,"url":"https://github.com/Andre-HUANG/polyu-study-console/releases/download/v1.8.3/test.zip"}}}
        body=json.dumps(value).encode();signature=base64.b64encode(key.sign(body)).decode()
        self.assertEqual(verify_metadata(body,signature,public,"1.8.2")["build"],183)
        with self.assertRaises(Exception):verify_metadata(body+b" ",signature,public)
        with self.assertRaises(ValueError):verify_metadata(body,signature,public,"1.8.3")
        value["artifacts"]["test.zip"]["url"]="https://example.invalid/test.zip"
        body=json.dumps(value).encode();signature=base64.b64encode(key.sign(body)).decode()
        with self.assertRaises(ValueError):verify_metadata(body,signature,public)
    def program(self,folder,version):
        folder.mkdir()
        files={"WindowsApp/main.py":b"pass","runtime/python.exe":b"synthetic","console/release.json":json.dumps({"version":version,"targets":["Windows-x64"]}).encode()}
        for name,body in files.items():
            p=folder/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body)
        return folder
    def test_portable_replacement_and_rollback_preserve_separate_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();old=self.program(root/"app","1.8.2");new=self.program(root/"new","1.8.3")
            account=root/"account";account.mkdir();(account/"manual.txt").write_text("synthetic")
            replace_portable(old,new,root/"backup")
            self.assertEqual(json.loads((old/"console/release.json").read_text())["version"],"1.8.3")
            restore(old,root/"backup")
            self.assertEqual(json.loads((old/"console/release.json").read_text())["version"],"1.8.2")
            self.assertEqual((account/"manual.txt").read_text(),"synthetic")
    def test_updates_reject_links_account_files_zip_traversal_and_bad_hash(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();old=self.program(root/"app","1.8.2")
            (old/"account").mkdir()
            with self.assertRaises(ValueError):safe_program(old)
            archive=root/"bad.zip"
            with zipfile.ZipFile(archive,"w") as z:z.writestr("../account/login.json","synthetic")
            with self.assertRaises(ValueError):unpack_payload(archive,root/"stage","1.8.3")
    def test_updater_accepts_bundled_webview_code_but_rejects_private_webview(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();program=self.program(root/"app","1.8.3")
            module=program/"runtime/Lib/site-packages/webview/__init__.py"
            module.parent.mkdir(parents=True);module.write_text("pass")
            self.assertEqual(safe_program(program),program)
            manifest={p.relative_to(program).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in program.rglob("*") if p.is_file()}
            (program/"payload-sha256.json").write_text(json.dumps(manifest))
            archive=root/"good.zip"
            with zipfile.ZipFile(archive,"w") as z:
                for p in program.rglob("*"):
                    if p.is_file(): z.write(p,p.relative_to(root).as_posix())
            self.assertEqual(unpack_payload(archive,root/"stage","1.8.3").name,"app")
            (program/"webview").mkdir()
            with self.assertRaises(ValueError):safe_program(program)
            (program/"webview").rmdir()
            private=module.parent/"account";private.mkdir()
            with self.assertRaises(ValueError):safe_program(program)
    def test_updates_reject_nested_program_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();program=self.program(root/"app","1.8.3")
            outside=root/"separate";outside.mkdir()
            link=program/"linked"
            try: link.symlink_to(outside,target_is_directory=True)
            except (OSError,NotImplementedError):self.skipTest("Symlink privilege unavailable")
            with self.assertRaises(ValueError):safe_program(program)

    def test_program_cleanup_requires_exact_transaction_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();program=self.program(root/"app","1.8.3")
            stage=root/"PolyU-program-update-synthetic";stage.mkdir()
            (stage/"spec.json").write_text(json.dumps({"target":str(program)}))
            marker=stage/"cleanup-owner.json"
            marker.write_text(json.dumps({"schema":1,"target":str(program),"helperPID":42}))
            self.assertEqual(cleanup_guard(stage,program,42),stage)
            with self.assertRaises(ValueError):cleanup_guard(stage,program,43)
            marker.write_text(json.dumps({"schema":1,"target":str(root/"account"),"helperPID":42}))
            with self.assertRaises(ValueError):cleanup_guard(stage,program,42)

    def helper_fixture(self,root,mode):
        import base64,zipfile
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        parent=root/"Programs";parent.mkdir()
        target=self.program(parent/"PolyUStudyConsole","1.8.2")
        stage=parent/"PolyU-program-update-synthetic";stage.mkdir()
        name="PolyUStudyConsole-1.8.3-windows-x64"+(".zip" if mode=="portable" else "-setup.exe")
        package=stage/name
        if mode=="portable":
            fresh=self.program(root/"fresh","1.8.3")
            manifest={p.relative_to(fresh).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in fresh.rglob("*") if p.is_file()}
            (fresh/"payload-sha256.json").write_text(json.dumps(manifest))
            with zipfile.ZipFile(package,"w") as z:
                for p in fresh.rglob("*"):
                    if p.is_file():z.write(p,p.relative_to(root).as_posix())
        else:package.write_bytes(b"synthetic-installer")
        key=Ed25519PrivateKey.generate()
        public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        value={"schema":1,"repository":"Andre-HUANG/polyu-study-console","version":"1.8.3","build":183,"draft":False,"prerelease":False,"artifacts":{name:{"sha256":hashlib.sha256(package.read_bytes()).hexdigest(),"bytes":package.stat().st_size,"url":"https://github.com/Andre-HUANG/polyu-study-console/releases/download/v1.8.3/"+name,"platform":"windows","architecture":"x64"}}}
        body=json.dumps(value).encode();(stage/"updates.json").write_bytes(body)
        (stage/"updates.json.sig").write_text(base64.b64encode(key.sign(body)).decode())
        (stage/"updater-lock.json").write_text(json.dumps({"publicKey":public}))
        spec={"target":str(target),"mode":mode,"pid":1,"payload":name,"asset":name}
        (stage/"spec.json").write_text(json.dumps(spec))
        manifest={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob("*") if p.is_file()}
        (target/"payload-sha256.json").write_text(json.dumps(manifest))
        account=root/"account";account.mkdir();(account/"sentinel.txt").write_text("synthetic-account")
        return target,stage,spec,account
    def test_helper_disk_full_preserves_program_and_separate_account(self):
        import ctypes,types,update_transaction as transaction
        with tempfile.TemporaryDirectory() as tmp:
            target,stage,spec,account=self.helper_fixture(pathlib.Path(tmp).resolve(),"portable")
            kernel=types.SimpleNamespace(OpenProcess=Mock(return_value=None),WaitForSingleObject=Mock(),CloseHandle=Mock())
            with patch.object(transaction,"__file__",str(stage/"update_transaction.py")),patch.object(ctypes,"windll",types.SimpleNamespace(kernel32=kernel),create=True),patch.object(transaction.shutil,"disk_usage",return_value=types.SimpleNamespace(free=0)),patch.object(transaction,"schedule_cleanup"),self.assertRaisesRegex(OSError,"磁盘空间"):
                transaction.run_helper(spec)
            self.assertEqual(json.loads((target/"console/release.json").read_text())["version"],"1.8.2")
            self.assertEqual((account/"sentinel.txt").read_text(),"synthetic-account")
            self.assertFalse(target.with_name(target.name+".previous-1.8.3").exists())
    def test_helper_interrupted_installer_restores_program_and_separate_account(self):
        import ctypes,types,subprocess,update_transaction as transaction
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();target,stage,spec,account=self.helper_fixture(root,"installed")
            kernel=types.SimpleNamespace(OpenProcess=Mock(return_value=None),WaitForSingleObject=Mock(),CloseHandle=Mock())
            def interrupt(*args,**kwargs):
                (target/"console").mkdir(parents=True,exist_ok=True)
                (target/"console/release.json").write_text('{"version":"broken"}')
                raise subprocess.TimeoutExpired("synthetic-installer",180)
            with patch.object(transaction,"__file__",str(stage/"update_transaction.py")),patch.object(ctypes,"windll",types.SimpleNamespace(kernel32=kernel),create=True),patch.dict(transaction.os.environ,{"LOCALAPPDATA":str(root)}),patch.object(transaction.subprocess,"run",side_effect=interrupt),patch.object(transaction,"schedule_cleanup"),self.assertRaises(subprocess.TimeoutExpired):
                transaction.run_helper(spec)
            self.assertEqual(json.loads((target/"console/release.json").read_text())["version"],"1.8.2")
            self.assertEqual((account/"sentinel.txt").read_text(),"synthetic-account")
            self.assertFalse(target.with_name(target.name+".previous-1.8.3").exists())

    def test_failed_swap_restores_previous_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp).resolve();old=self.program(root/"app","1.8.2");new=self.program(root/"new","1.8.3")
            original=pathlib.Path.rename
            def rename(path,destination):
                if path==new:raise PermissionError("synthetic disk/permission failure")
                return original(path,destination)
            with patch.object(pathlib.Path,"rename",rename),self.assertRaises(PermissionError):replace_portable(old,new,root/"backup")
            self.assertEqual(json.loads((old/"console/release.json").read_text())["version"],"1.8.2")
            self.assertFalse((root/"backup").exists())
