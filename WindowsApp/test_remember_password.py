"""Credential consent, verified identity, failed writes and logout lifecycle regressions."""
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / '学习控制台'))
sys.path.insert(0, str(ROOT / 'WindowsApp'))
from credential_store import RememberedCredentials, LoginAttempt, sso_url, read_logout_policy
import desktop


def synthetic_protector(data, decrypt=False):
    # Deterministic mock for offline policy tests, never used by production.
    if decrypt:
        if not data.startswith(b'mock-only:') or len(data) < 42: raise ValueError()
        raw = bytes(b ^ 165 for b in data[42:])
        if hashlib.sha256(raw).digest() != data[10:42]: raise ValueError()
        return raw
    return b'mock-only:' + hashlib.sha256(data).digest() + bytes(b ^ 165 for b in data)


class CredentialPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name) / 'application'
        self.root.mkdir()
        self.store = RememberedCredentials(self.root, synthetic_protector)
        self.secret = 'synthetic-only-' + chr(34) + chr(39) + '\\换行\n $() </script>'

    def tearDown(self): self.temp.cleanup()

    def saved(self):
        self.store.enable()
        self.store.save_verified('synthetic-netid', self.secret, '123')

    def test_default_off_and_read_does_not_create_files(self):
        self.assertEqual(self.store.status()['state'], 'off')
        self.assertFalse(self.store.directory.exists())
        with self.assertRaises(RuntimeError): self.store.save_verified('demo', self.secret, '123')

    def test_successful_save_restart_and_no_plaintext(self):
        self.saved()
        another = RememberedCredentials(self.root, synthetic_protector)
        self.assertEqual(another.record()['password'], self.secret)
        self.assertEqual(another.record()['verifiedUserId'], '123')
        self.assertNotIn(self.secret.encode(), self.store.file.read_bytes())
        self.assertNotIn('synthetic-netid', self.store.settings.read_text())
        self.assertEqual(another.status()['state'], 'saved')
        self.assertNotIn('password', another.status())
        self.assertNotEqual(another.status()['maskedUsername'], 'synthetic-netid')

    def test_bad_encryption_keeps_old_file(self):
        self.saved(); original = self.store.file.read_bytes()
        self.store.protector = lambda *a, **kw: (_ for _ in ()).throw(OSError())
        with self.assertRaises(RuntimeError): self.store.save_verified('changed', 'new-demo', '123')
        self.assertEqual(self.store.file.read_bytes(), original)
        self.assertEqual(self.store.status()['state'], 'unavailable')

    def test_readback_failure_keeps_old_file(self):
        self.saved(); original = self.store.file.read_bytes()
        self.store.protector = lambda value, decrypt=False: b'invalid' if decrypt else b'encrypted'
        with self.assertRaises(RuntimeError): self.store.save_verified('changed', 'new-demo', '123')
        self.assertEqual(self.store.file.read_bytes(), original)

    def test_corrupted_cipher_is_unavailable_without_plaintext_fallback(self):
        self.saved(); self.store.file.write_bytes(b'invalid-protected-record')
        self.assertEqual(self.store.status()['state'], 'unavailable')
        with self.assertRaises(RuntimeError): self.store.record()

    def test_owner_mismatch_is_visible(self):
        self.saved(); status = self.store.status('456')
        self.assertTrue(status['differentAccount']); self.assertEqual(status['state'], 'pending')

    def test_forget_does_not_remove_account(self):
        self.saved(); account = self.root / 'account'; account.mkdir(); (account/'manual.txt').write_text('preserve')
        self.store.forget()
        self.assertFalse(self.store.file.exists()); self.assertEqual(self.store.status()['state'], 'off')
        self.assertEqual((account/'manual.txt').read_text(), 'preserve')

    def test_failed_delete_disables_and_retries(self):
        self.saved()
        original_unlink = pathlib.Path.unlink
        def blocked(path, *a, **kw):
            if path == self.store.file: raise PermissionError()
            return original_unlink(path, *a, **kw)
        with patch.object(pathlib.Path, 'unlink', blocked):
            with self.assertRaises(RuntimeError): self.store.forget()
            self.assertFalse(self.store.status()['enabled'])
            self.assertTrue(json.loads(self.store.settings.read_text())['purgePending'])
        self.store.resume_purge(); self.assertEqual(self.store.status()['state'], 'off')
        self.assertFalse(self.store.file.exists())

    def test_failed_disable_write_stops_fill_until_delete_retry(self):
        self.saved()
        with patch.object(self.store, '_save_state', side_effect=PermissionError()):
            with self.assertRaises(PermissionError): self.store.forget()
            self.assertEqual(self.store.status()['state'], 'unavailable')
            self.assertFalse(self.store.status()['enabled'])
            with self.assertRaises(PermissionError): self.store.record()
        self.assertIsNone(self.store.record())
        self.assertFalse(self.store.file.exists())

    def test_linked_credentials_never_reads_or_writes_outside(self):
        outside = pathlib.Path(self.temp.name)/'outside'; outside.mkdir(); (outside/'sentinel').write_text('preserve')
        try: self.store.directory.symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest('Symlink creation unavailable')
        with self.assertRaises(RuntimeError): self.store.enable()
        self.assertEqual((outside/'sentinel').read_text(), 'preserve')

    def test_logout_keep_preserves_only_credentials(self):
        self.saved(); (self.root/'account').mkdir(); (self.root/'account/manual.txt').write_text('old-account')
        (self.root/'webview').mkdir(); (self.root/'webview/cookies').write_text('synthetic-session')
        desktop.mark_logout(self.root, True)
        self.assertTrue(read_logout_policy(self.root))
        desktop.delete_account(self.root, Mock())
        self.assertEqual({p.name for p in self.root.iterdir()}, {'credentials'})
        self.assertEqual(self.store.record()['password'], self.secret)

    def test_logout_delete_removes_credentials_and_account(self):
        self.saved(); (self.root/'account').mkdir()
        desktop.mark_logout(self.root, False); desktop.delete_account(self.root, Mock())
        self.assertFalse(self.root.exists())

    def test_legacy_or_corrupt_logout_defaults_to_delete(self):
        for marker in ('pending\n', '{}', '{"version":2,"keepRememberedPassword":"true"}'):
            (self.root/'logout-pending').write_text(marker)
            self.assertFalse(read_logout_policy(self.root))

    def test_retry_preserves_retention_policy(self):
        self.saved(); (self.root/'account').mkdir(); desktop.mark_logout(self.root, True)
        with patch.object(desktop.shutil, 'rmtree', side_effect=PermissionError()), patch.object(desktop.time, 'sleep'):
            with self.assertRaises(RuntimeError): desktop.delete_account(self.root, Mock())
        self.assertTrue(read_logout_policy(self.root))
        desktop.delete_account(self.root, Mock())
        self.assertEqual(self.store.status()['state'], 'saved')

    def app_attempt(self):
        app = desktop.Desktop(self.root, Mock())
        app.credentials = self.store
        app.backend = Mock(); app.console = Mock(); app.login_window = Mock(); app.login_attempt = LoginAttempt()
        app.login_attempt.candidate = ('synthetic-netid', self.secret)
        return app, app.login_attempt

    def test_cancelled_consent_keeps_default_off(self):
        app, _ = self.app_attempt()
        with patch.object(desktop, 'native_confirm', return_value=(False, False)):
            app.enable_credentials()
        self.assertFalse(self.store.status()['enabled'])

    def test_only_verified_success_can_commit(self):
        self.store.enable(); app, attempt = self.app_attempt()
        app._save_login_credential({}, app.login_window, attempt)
        self.assertIsNone(self.store.record()); self.assertIsNone(attempt.candidate)
        attempt.candidate = ('synthetic-netid', self.secret)
        app._save_login_credential({'verifiedUserId':'123'}, app.login_window, attempt)
        self.assertEqual(self.store.record()['password'], self.secret)
        app.backend.request.assert_not_called()

    def test_closed_or_replaced_attempt_cannot_commit(self):
        self.store.enable(); app, attempt = self.app_attempt(); attempt.close()
        app._save_login_credential({'verifiedUserId':'123'}, app.login_window, attempt)
        self.assertIsNone(self.store.record())
        app, attempt = self.app_attempt(); app.login_attempt = LoginAttempt()
        app._save_login_credential({'verifiedUserId':'123'}, app.login_window, attempt)
        self.assertIsNone(self.store.record())

    def test_account_replacement_requires_explicit_confirmation(self):
        self.saved(); app, attempt = self.app_attempt()
        with patch.object(desktop, 'native_confirm', return_value=(False, False)) as confirm:
            app._save_login_credential({'verifiedUserId':'456'}, app.login_window, attempt)
            confirm.assert_called_once()
        self.assertEqual(self.store.record()['verifiedUserId'], '123')
        attempt.candidate = ('synthetic-new-netid', self.secret)
        with patch.object(desktop, 'native_confirm', return_value=(True, False)):
            app._save_login_credential({'verifiedUserId':'456'}, app.login_window, attempt)
        self.assertEqual(self.store.record()['verifiedUserId'], '456')

    @unittest.skipUnless(sys.platform == 'win32', 'Native Windows user DPAPI requires Windows')
    def test_native_dpapi_roundtrip_restart_and_corruption(self):
        native = RememberedCredentials(self.root)
        native.enable(); native.save_verified('synthetic-netid', self.secret, '123')
        self.assertNotIn(self.secret.encode(), native.file.read_bytes())
        self.assertEqual(RememberedCredentials(self.root).record()['password'], self.secret)
        original = native.file.read_bytes(); native.file.write_bytes(original[:-1]+bytes([original[-1]^1]))
        self.assertEqual(native.status()['state'], 'unavailable')
        native.forget(); self.assertFalse(native.file.exists())


class SchoolMessageTests(unittest.TestCase):
    def setUp(self):
        self.attempt = LoginAttempt(); self.source = 'https://adfs.polyu.edu.hk/adfs/ls/?fixture=true'
        self.ready = {'type':'polyuRemember', 'kind':'ready', 'nonce':self.attempt.nonce, 'documentId':'a'*32}
        self.candidate = {**self.ready, 'kind':'candidate', 'username':'synthetic-netid', 'password':'synthetic-only'}

    def test_scoped_origin_and_path(self):
        self.assertTrue(sso_url(self.source))
        for url in ('http://adfs.polyu.edu.hk/adfs/ls/', 'https://adfs.polyu.edu.hk.evil.example/adfs/ls/',
                    'https://adfs.polyu.edu.hk:444/adfs/ls/', 'https://demo' + chr(64) + 'adfs.polyu.edu.hk/adfs/ls/',
                    'https://canvas.polyu.edu.hk/adfs/ls/', 'https://adfs.polyu.edu.hk/adfs/portal/updatepassword/'):
            self.assertFalse(self.attempt.receive(url, self.ready, True))
            self.assertIsNone(self.attempt.candidate)

    def test_requires_consent_nonce_and_ready_document(self):
        self.assertFalse(self.attempt.receive(self.source, self.ready, False))
        self.assertFalse(self.attempt.receive(self.source, {**self.ready,'nonce':'b'*64}, True))
        self.assertFalse(self.attempt.receive(self.source, self.candidate, True))
        self.assertTrue(self.attempt.receive(self.source, self.ready, True))
        self.assertTrue(self.attempt.receive(self.source, self.candidate, True))
        self.assertEqual(self.attempt.candidate, ('synthetic-netid','synthetic-only'))

    def test_error_form_ready_and_closed_attempt_discard_candidate(self):
        self.attempt.receive(self.source,self.ready,True); self.attempt.receive(self.source,self.candidate,True)
        self.attempt.receive(self.source,{**self.ready,'documentId':'b'*32},True)
        self.assertIsNone(self.attempt.candidate)
        self.attempt.close(); self.assertFalse(self.attempt.receive(self.source,self.ready,True))

    def test_extra_fields_and_oversized_values_are_rejected(self):
        self.attempt.receive(self.source,self.ready,True)
        for packet in ({**self.candidate,'password':'x'*4097},{**self.candidate,'username':None},{**self.candidate,'url':self.source},
                       {**self.candidate,'documentId':'b'*32}):
            self.assertFalse(self.attempt.receive(self.source,packet,True))
        self.assertIsNone(self.attempt.candidate)

class NativeBridgeBoundaryTests(unittest.TestCase):
    def test_school_bridge_removes_generic_handler_and_browser_autosave(self):
        from login_credentials import SchoolCredentialBridge
        from unittest.mock import MagicMock
        app = Mock(); app.login_attempt = LoginAttempt(); window = MagicMock()
        app.login_window = window; app.stopping = False
        app.account_owner.return_value = '123'; app.credentials.status.return_value = {'enabled':True}
        control = window.native.browser.webview
        original_messages = control.WebMessageReceived
        bridge = SchoolCredentialBridge(app, window, ROOT)
        original_messages.__isub__.assert_called_once_with(window.native.browser.on_script_notify)
        self.assertIs(control.CoreWebView2.Settings.IsPasswordAutosaveEnabled, False)
        self.assertIs(control.CoreWebView2.Settings.IsGeneralAutofillEnabled, False)
        packet = {'type':'polyuRemember','kind':'ready','nonce':app.login_attempt.nonce,'documentId':'a'*32}
        event = Mock(); event.Source = 'https://evil.example/adfs/ls/'
        event.get_WebMessageAsJson.return_value = json.dumps(packet)
        bridge.receive(None, event)
        app.credentials.record.assert_not_called()
        app.credentials.status.assert_not_called()
        self.assertIsNone(app.login_attempt.document)
        bridge.close(); self.assertFalse(app.login_attempt.active)
