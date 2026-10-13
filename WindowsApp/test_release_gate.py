"""Release failures must stop publication before any remote mutation."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
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
    def test_dirty_checkout_has_no_source_acceptance(self):
        with patch.object(guard.subprocess,"check_output",side_effect=[self.head+"\\n",b" M source.py\\n"]):
            with self.assertRaises(RuntimeError):guard.source_sha()
    def test_authenticated_transport_rejects_endpoint_escape(self):
        for endpoint in ["/releases/../../other","/actions/%2e%2e/other","/releases-elsewhere","https://example.invalid"]:
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):github_api.request(endpoint)
