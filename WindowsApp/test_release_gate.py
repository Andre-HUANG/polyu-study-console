"""Release failures must stop publication before any remote mutation."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"security"))
import release as publishing
import release_guard as guard
import github_api

class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.head="1"*40
        self.reports={}
        for platform,suffixes in {"mac":["-arm64.dmg","-arm64.zip"],"windows":["-windows-x64-setup.exe","-windows-x64.zip"]}.items():
            artifacts={}
            for suffix in suffixes:
                name="PolyUStudyConsole-"+publishing.VERSION+suffix
                content=("synthetic-package"+suffix).encode()
                (self.root/name).write_bytes(content)
                artifacts[name]={"sha256":hashlib.sha256(content).hexdigest(),"bytes":len(content)}
            self.reports[platform]={"ok":True,"platform":platform,"version":publishing.VERSION,"sourceSHA":self.head,
                "sourceTreeClean":True,"archivePrivacyScan":True,"artifacts":artifacts,
                "platformChecks":dict.fromkeys(["nativeGUI","backendRuntime","dmgReadOnlyScan","adHocSigned","dpapi","installUninstall","nativeUpdates"],True)}
        self.save()
        self.mock=patch.object(publishing,"source_sha",return_value=self.head)
        self.mock.start()
    def tearDown(self):
        self.mock.stop();self.temp.cleanup()
    def save(self):
        for platform,report in self.reports.items():
            (self.root/(platform+"-validation.json")).write_text(json.dumps(report))
    def collect(self):
        return publishing.collect(self.root,self.root/"output")
    def test_full_verified_payload_produces_checksums(self):
        self.assertEqual(self.collect(),self.head)
        self.assertEqual(len((self.root/"output/SHA256SUMS.txt").read_text().splitlines()),4)
    def test_missing_platform_blocks(self):
        (self.root/"windows-validation.json").unlink()
        with self.assertRaises(RuntimeError):self.collect()
    def test_missing_native_installation_blocks(self):
        self.reports["windows"]["platformChecks"]["installUninstall"]=False;self.save()
        with self.assertRaises(RuntimeError):self.collect()
    def test_missing_native_updater_rehearsal_blocks(self):
        for platform in ("mac","windows"):
            self.reports[platform]["platformChecks"]["nativeUpdates"]=False;self.save()
            with self.assertRaises(RuntimeError):self.collect()
            self.reports[platform]["platformChecks"]["nativeUpdates"]=True
    def test_different_or_stale_commit_blocks(self):
        self.reports["windows"]["sourceSHA"]="2"*40;self.save()
        with self.assertRaises(RuntimeError):self.collect()
    def test_uncommitted_build_blocks(self):
        self.reports["mac"]["sourceTreeClean"]=False;self.save()
        with self.assertRaises(RuntimeError):self.collect()
    def test_changed_package_blocks(self):
        (self.root/next(iter(self.reports["mac"]["artifacts"]))).write_bytes(b"changed")
        with self.assertRaises(RuntimeError):self.collect()
    def test_duplicate_package_blocks(self):
        name=next(iter(self.reports["mac"]["artifacts"]))
        (self.root/"duplicate").mkdir();(self.root/"duplicate"/name).write_bytes((self.root/name).read_bytes())
        with self.assertRaises(RuntimeError):self.collect()
    def test_platform_misassignment_blocks(self):
        name=next(iter(self.reports["windows"]["artifacts"]))
        self.reports["mac"]["artifacts"][name]=self.reports["windows"]["artifacts"][name];self.save()
        with self.assertRaises(RuntimeError):self.collect()
    def test_only_trusted_successful_main_run_is_accepted(self):
        valid={"status":"completed","conclusion":"success","head_branch":"main","head_sha":self.head,"event":"push","path":".github/workflows/ci.yml"}
        guard.trusted_run(valid,self.head)
        for key,value in [("event","pull_request"),("head_branch","security/synthetic"),("head_sha","2"*40),("conclusion","failure"),("status","in_progress"),("path",".github/workflows/other.yml")]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):guard.trusted_run(dict(valid,**{key:value}),self.head)
    def test_independent_receipt_requires_signature_current_source_and_freshness(self):
        import base64,datetime as dt,uuid
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        key=Ed25519PrivateKey.generate()
        public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        (self.root/"updater-lock.json").write_text(json.dumps({"publicKey":public}))
        value={"schema":1,"sourceSHA":self.head,"checkedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"scope":"independent-local-mac","receipt":str(uuid.uuid4()),"checks":dict.fromkeys(["authenticatedOwnerChecked","calendarServiceChecked","savedInterpretationChecked","accountFilesUnmodified"],True)}
        def signed(data):
            body=json.dumps(data).encode()
            return json.dumps({"body":base64.b64encode(body).decode(),"signature":base64.b64encode(key.sign(body)).decode()})
        with patch.object(guard,"ROOT",self.root):
            self.assertTrue(guard.independent_acceptance(signed(value),self.head))
            with self.assertRaises(RuntimeError):guard.independent_acceptance("",self.head)
            with self.assertRaises(RuntimeError):guard.independent_acceptance(signed(value),"2"*40)
            old={**value,"checkedAt":(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=2)).isoformat()}
            with self.assertRaises(RuntimeError):guard.independent_acceptance(signed(old),self.head)
            bad={**value,"checks":{**value["checks"],"accountFilesUnmodified":False}}
            with self.assertRaises(RuntimeError):guard.independent_acceptance(signed(bad),self.head)
            bad={**value,"accountId":"synthetic-not-allowed"}
            with self.assertRaises(RuntimeError):guard.independent_acceptance(signed(bad),self.head)

    def test_final_performance_receipt_rejects_stale_or_incomplete_measurements(self):
        import base64,copy,datetime as dt
        import performance_receipt as perf
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        key=Ed25519PrivateKey.generate()
        public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        (self.root/"updater-lock.json").write_text(json.dumps({"publicKey":public}))
        import release_build
        report={"measuredCandidateCommit":self.head,"sourceHashes":{},"checkedAt":dt.datetime.now(dt.timezone.utc).isoformat(),
            "idleCPU":{"realAccountAccessed":False,"actualBatteryEnergyMeasured":False,"trials":[{"trial":t,"version":v,"state":state,"seconds":60,"cpuSeconds":.1,"wakeups":10} for t in range(1,4) for v in ("1.8.1","1.8.2") for state in ("visible","hidden")]},
            "syntheticSynchronization":{label:{"version":version,"trials":[{"trial":t,"condition":condition,"files":46,"pages":102,"indexWrites":1} for t in range(1,4) for condition in ("cold","warm")]} for label,version in (("baseline","1.8.1"),("candidate","1.8.2"))}}
        def signed(value):
            body=perf.canonical(value)
            return json.dumps({"body":base64.b64encode(body).decode(),"signature":base64.b64encode(key.sign(body)).decode()})
        with patch.object(perf,"ROOT",self.root),patch.object(release_build,"source_hashes",return_value={}):
            self.assertEqual(perf.validate(signed(report),self.head),report)
            with self.assertRaises(RuntimeError):perf.validate(signed(report),"2"*40)
            bad=copy.deepcopy(report);bad["idleCPU"]["trials"].pop()
            with self.assertRaises(RuntimeError):perf.validate(signed(bad),self.head)
            bad=copy.deepcopy(report);bad["syntheticSynchronization"]["candidate"]["trials"][-1]["indexWrites"]=4
            with self.assertRaises(RuntimeError):perf.validate(signed(bad),self.head)
            with self.assertRaises(RuntimeError):perf.validate("",self.head)

    def test_real_receipt_rejects_failed_calendar_or_unread_documents(self):
        import record_real_acceptance as real
        home=self.root/"synthetic-home"
        profile=home/"Library/Application Support/PolyU study控制台/account"
        (profile/"browser").mkdir(parents=True);(profile/"documents").mkdir()
        (profile/"config.json").write_text(json.dumps({"user":{"id":"1"},"courses":[{"id":"1"}]}))
        (profile/"browser/canvas-cookies.json").write_text("[]")
        (profile/"documents/index.json").write_text(json.dumps({"accountId":"1","records":[]}))
        api=Mock();api.get.return_value=[]
        with patch.object(real.pathlib.Path,"home",return_value=home),patch.object(real,"source_sha",return_value=self.head),patch.object(real.sys,"platform","darwin"),patch.object(real,"session_class",return_value=Mock(return_value=api)),patch.object(real,"authenticated_user",return_value={"id":"1"}):
            api.get.side_effect=ConnectionError()
            with self.assertRaisesRegex(RuntimeError,"calendar acceptance"):real.main()
            api.get.side_effect=None
            with self.assertRaisesRegex(RuntimeError,"readable saved documents"):real.main()
        self.assertFalse((self.root/"independent-acceptance.json").exists())

    def test_dirty_checkout_has_no_source_acceptance(self):
        with patch.object(guard.subprocess,"check_output",side_effect=[self.head+"\\n",b" M source.py\\n"]):
            with self.assertRaises(RuntimeError):guard.source_sha()
    def test_authenticated_transport_rejects_endpoint_escape(self):
        for endpoint in ["/releases/../../other","/actions/%2e%2e/other","/releases-elsewhere","https://example.invalid"]:
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):github_api.request(endpoint)
