"""Tests of redaction, staged inspection and dependency classification."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "security"))
import privacy_scan as gate
gate.OWNER_EMAILS = set()

class PrivacyGateChecks(unittest.TestCase):
    def test_email_and_local_path_are_redacted(self):
        email = b"synthetic.person" + b"@gmail.com"
        path = b"/Users/" + b"synthetic-private/file"
        results = gate.scan_bytes("fixture", email + b"\n" + path)
        self.assertEqual({r["rule"] for r in results}, {"email", "local-user-path"})
        self.assertNotIn(email.decode(), str(results)); self.assertNotIn(path.decode(), str(results))

    def test_synthetic_and_noreply_addresses_are_allowed(self):
        self.assertFalse(gate.scan_bytes("fixture", b"fixture@example.com 1+handle@users.noreply.github.com"))

    def test_vendor_licenses_are_classified_but_credentials_still_fail(self):
        email = b"upstream" + b"@gmail.com"
        self.assertEqual(gate.scan_bytes("vendor/LICENSE", email, third_party=True)[0]["classification"], "THIRD_PARTY")
        credential = b"ghp_" + b"A"*36
        self.assertEqual(gate.scan_bytes("vendor/LICENSE", credential, third_party=True)[0]["classification"], "FAIL")

    def test_private_key_format_constant_is_not_a_key(self):
        marker = b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----"
        self.assertFalse(gate.scan_bytes("constant", marker))
        self.assertTrue(gate.scan_bytes("key", marker + b"\n" + b"A"*100))

    def test_private_filenames_are_detected(self):
        for path in ["验证/native-gui-evidence.json", "account/state.json", "验证/gui-component-synthetic/view.png", "example/secret.p12"]:
            self.assertTrue(gate.risky_path(path))
        self.assertFalse(gate.risky_path("学习控制台/assets/polyu-logo.png"))

    def test_staged_scanner_reads_index_not_worktree(self):
        with patch.object(gate, "git", side_effect=[b"README.md\0", b"synthetic" + b"@gmail.com"]) as git:
            results, count = gate.files(staged=True)
            self.assertEqual(count, 1); self.assertEqual(results[0]["rule"], "email")
            self.assertEqual(git.call_args.args, ("show", ":README.md"))

    def test_webview_library_name_is_not_a_blanket_exception(self):
        import tempfile,zipfile
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"changed.zip"
            with zipfile.ZipFile(path,"w") as archive:
                archive.writestr("runtime/Lib/site-packages/webview/__init__.py",b"changed-library")
            findings,_,_=gate.artifact(path)
            self.assertTrue(any(r["rule"]=="private-generated-path" for r in findings))

    def test_only_hash_locked_updater_gets_upstream_contact_classification(self):
        import hashlib,tempfile,zipfile
        contents=b"public upstream contact "+b"upstream"+b"@gmail.com"
        with tempfile.TemporaryDirectory() as temporary:
            package=Path(temporary)/"updater.zip"
            def write(data):
                with zipfile.ZipFile(package,"w") as archive:archive.writestr("bundle/updater/WinSparkle.dll",data)
            with patch.object(gate,"UPDATER_FILES",{"updater/WinSparkle.dll":hashlib.sha256(contents).hexdigest()}):
                write(contents);findings,_,_=gate.artifact(package)
                self.assertEqual([r["classification"] for r in findings],["THIRD_PARTY"])
                write(contents+b"modified");findings,_,_=gate.artifact(package)
                self.assertEqual([r["classification"] for r in findings],["FAIL"])

    def test_identity_number_format_is_detected_and_redacted(self):
        # Deliberately assembled synthetic format examples, never real identities.
        for prefix in (b"X", b"ZX"):
            for check in (b"3", b"A"):
                value = prefix + b"987654" + b"(" + check + b")"
                rows = gate.scan_bytes("synthetic-fixture", b"id: " + value)
                self.assertEqual([r["rule"] for r in rows], ["identity-number"])
                self.assertNotIn(value.decode(), str(rows))
        self.assertFalse(gate.scan_bytes("ordinary", b"release987654(v3)"))
