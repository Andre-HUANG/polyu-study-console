"""Synthetic privacy regressions; HTTP handler tests do not open sockets."""
import io
import json
import os
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "学习控制台"))
import server
import session_store
from native_canvas import save_private, write_private, load_cookies
from local_access import LocalAccess


class PrivacyChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="polyu-privacy-")
        self.profile = Path(self.temp.name) / "account"
        self.patch = patch.multiple(server, PROFILE=self.profile, PROJECT=self.profile.parent,
            ACCESS=LocalAccess(), NATIVE=True, NATIVE_SECRET="synthetic-native-" + "x" * 48, DIAGNOSTICS=None)
        self.patch.start()
        self.csrf = server.CSRF

    def tearDown(self):
        server.CSRF = self.csrf
        self.patch.stop()
        self.temp.cleanup()

    def handler(self, path, payload=None, authenticated=False, native=False, origin=True):
        h = object.__new__(server.Handler)
        h.server = Mock(server_port=8766)
        h.path, h.headers = path, Message()
        h.headers["Host"] = "127.0.0.1:8766"
        if origin: h.headers["Origin"] = "http://127.0.0.1:8766"
        if authenticated:
            h.headers["Cookie"] = server.ACCESS.cookie_name(8766) + "=" + server.ACCESS.token
            h.headers["X-PolyU-Console"] = server.CSRF
        if native: h.headers["X-PolyU-Native"] = server.NATIVE_SECRET
        body = json.dumps(payload or {}).encode()
        h.headers["Content-Length"] = str(len(body))
        h.rfile, h.send_json = io.BytesIO(body), Mock()
        h.send_bytes = Mock()
        return h

    def test_all_sensitive_reads_deny_public_clients(self):
        for path in ["/api/data", "/api/documents/search?q=synthetic", "/api/diagnostics"]:
            h = self.handler(path); h.do_GET()
            self.assertEqual(h.send_json.call_args.args[1], 401, path)

    def test_public_html_and_js_do_not_disclose_credentials(self):
        for path in ["/", "/app.js"]:
            h = self.handler(path); h.do_GET()
            body = h.send_bytes.call_args.args[0]
            for secret in [server.CSRF, server.ACCESS.token, server.NATIVE_SECRET]:
                self.assertNotIn(secret.encode(), body)
        h = self.handler("/", authenticated=True); h.do_GET()
        self.assertIn(server.CSRF.encode(), h.send_bytes.call_args.args[0])

    def test_public_mutation_cannot_use_public_csrf_as_authentication(self):
        h = self.handler("/api/settings", {"autoSync": False})
        h.headers["X-PolyU-Console"] = server.CSRF
        h.do_POST()
        self.assertEqual(h.send_json.call_args.args[1], 403)
        self.assertFalse(self.profile.exists())

    def test_native_only_ticket_is_single_use_and_cookie_is_http_only(self):
        bad = self.handler("/api/native/bootstrap"); bad.do_POST()
        self.assertEqual(bad.send_json.call_args.args[1], 403)
        trusted = self.handler("/api/native/bootstrap", native=True); trusted.do_POST()
        ticket = trusted.send_json.call_args.args[0]["ticket"]
        h = self.handler("/api/client/session", {"ticket": ticket}); h.do_POST()
        self.assertEqual(h.send_json.call_args.args[0], {"csrf": server.CSRF})
        self.assertIn("HttpOnly; SameSite=Strict", h.session_cookie)
        again = self.handler("/api/client/session", {"ticket": ticket}); again.do_POST()
        self.assertEqual(again.send_json.call_args.args[1], 403)

    def test_ticket_expiry_and_new_process_reject_old_credentials(self):
        access = LocalAccess(); ticket = access.issue()
        with patch("local_access.time.monotonic", return_value=10**12): self.assertIsNone(access.redeem(ticket))
        headers = {"Cookie": access.cookie_name(8766) + "=" + access.token}
        self.assertTrue(access.authorized(headers, 8766))
        self.assertFalse(LocalAccess().authorized(headers, 8766))
        self.assertFalse(access.authorized(headers, 9999))

    def test_logout_revokes_sessions_and_tickets_and_empties_account(self):
        save_private(self.profile / "config.json", {"user": {"id": "synthetic-A"}, "courses": []})
        save_private(self.profile / "login.json", {"user_id": "synthetic-A"})
        old = self.handler("/api/data", authenticated=True)
        ticket = server.ACCESS.issue()
        server.logout_native()
        old.do_GET(); self.assertEqual(old.send_json.call_args.args[1], 401)
        self.assertIsNone(server.ACCESS.redeem(ticket))
        self.assertEqual(list(self.profile.iterdir()), [])
        self.assertEqual(server.dashboard_data()["courses"], [])

    def test_valid_native_client_can_read_and_mutate_empty_framework(self):
        h = self.handler("/api/data", authenticated=True); h.do_GET()
        self.assertEqual(h.send_json.call_args.args[0]["courseCount"], 0)
        h = self.handler("/api/settings", {"autoSync": False}, authenticated=True); h.do_POST()
        self.assertIn("message", h.send_json.call_args.args[0])
        self.assertFalse(json.loads((self.profile / "app-settings.json").read_text())["autoSync"])

    def test_bad_host_origin_and_cookie_are_rejected(self):
        for header, value in [("Host", "attacker.invalid:8766"), ("Origin", "https://attacker.invalid"),
                              ("Cookie", server.ACCESS.cookie_name(8766) + "=wrong"), ("Sec-Fetch-Site", "cross-site")]:
            h = self.handler("/api/data", authenticated=True)
            if header in h.headers: del h.headers[header]
            h.headers[header] = value; h.do_GET()
            self.assertIn(h.send_json.call_args.args[1], (401, 403))

    def test_native_secret_never_bypasses_foreign_origin(self):
        h = self.handler("/api/native/bootstrap", native=True)
        h.headers.replace_header("Origin", "https://attacker.invalid"); h.do_POST()
        self.assertEqual(h.send_json.call_args.args[1], 403)

    def test_duplicate_hosts_and_chunked_mutations_are_rejected(self):
        h = self.handler("/api/data", authenticated=True); h.headers["Host"] = "attacker.invalid"; h.do_GET()
        self.assertEqual(h.send_json.call_args.args[1], 403)
        h = self.handler("/api/settings", {"autoSync": False}, authenticated=True)
        h.headers["Transfer-Encoding"] = "chunked"; h.do_POST()
        self.assertEqual(h.send_json.call_args.args[1], 400)

    def test_storage_failure_preserves_previous_bytes(self):
        path = self.profile / "browser/canvas-cookies.json"
        write_private(path, [{"name": "synthetic", "value": "synthetic-old"}]); previous = path.read_bytes()
        with patch("session_store.protect_session", side_effect=RuntimeError("synthetic vault failure")):
            with self.assertRaises(RuntimeError): save_private(path, [{"name": "synthetic", "value": "synthetic-new"}])
        self.assertEqual(path.read_bytes(), previous)
        self.assertFalse(list(path.parent.glob("*.tmp")))

    @unittest.skipUnless(sys.platform == "darwin", "Mac migration policy")
    def test_legacy_migration_verifies_before_atomic_replacement(self):
        path = self.profile / "browser/canvas-cookies.json"; rows = [{"name": "synthetic", "value": "synthetic"}]
        write_private(path, rows)
        protected = {"protection": session_store.PROTECTION, "account": session_store.account_key(path)}
        with patch("session_store.vault", side_effect=[None, rows]) as vault:
            self.assertEqual(load_cookies(path), rows)
            self.assertEqual([c.args[0] for c in vault.call_args_list], ["set", "get"])
        self.assertEqual(json.loads(path.read_text()), protected)
        with patch("session_store.vault", return_value=rows): self.assertEqual(load_cookies(path), rows)

    @unittest.skipUnless(sys.platform == "darwin", "Mac migration policy")
    def test_bad_keychain_readback_preserves_legacy_file(self):
        path = self.profile / "browser/canvas-cookies.json"; write_private(path, [{"name": "synthetic"}])
        original = path.read_bytes()
        with patch("session_store.vault", side_effect=[None, []]):
            with self.assertRaises(RuntimeError): load_cookies(path)
        self.assertEqual(path.read_bytes(), original)

    def test_symlink_session_cannot_read_or_overwrite_target(self):
        outside = Path(self.temp.name) / "synthetic-target"; outside.write_text("synthetic")
        path = self.profile / "browser/canvas-cookies.json"; path.parent.mkdir(parents=True); path.symlink_to(outside)
        with self.assertRaises(RuntimeError): load_cookies(path)
        with self.assertRaises(RuntimeError): write_private(path, {})
        self.assertEqual(outside.read_text(), "synthetic")

    def test_failed_keychain_protection_does_not_create_login_metadata(self):
        api = Mock(); api.get.return_value = {"id": "synthetic", "name": "Synthetic"}
        api.close.side_effect = RuntimeError("synthetic protection failure")
        with patch.object(server, "native_api", return_value=api):
            with self.assertRaises(RuntimeError): server.connect_native({"cookies": [{"name": "synthetic", "value": "synthetic", "domain": "canvas.polyu.edu.hk"}]})
        self.assertFalse((self.profile / "login.json").exists())
        self.assertFalse((self.profile / "config.json").exists())

    def test_native_reload_uses_new_document_url(self):
        import desktop
        backend=desktop.Backend(Path(self.temp.name))
        backend.url="http://127.0.0.1:8766/"
        with patch.object(backend,"request",return_value={"ticket":"synthetic-single-use"}):
            first=backend.bootstrap_url();second=backend.bootstrap_url()
        self.assertNotEqual(first,second)
        self.assertIn("?view=",first)
        self.assertIn("#bootstrap=",first)

    def test_revocation_during_read_cannot_return_new_account_data(self):
        h=self.handler("/api/data",authenticated=True)
        def revoke():
            server.ACCESS.rotate()
            return {"private":"synthetic-other-account"}
        with patch.object(server,"dashboard_data",side_effect=revoke): h.do_GET()
        self.assertEqual(h.send_json.call_args.args[1],401)
        self.assertNotIn("private",h.send_json.call_args.args[0])

    def test_truncated_post_and_non_ascii_native_secret_are_rejected(self):
        h=self.handler("/api/settings",{"autoSync":False},authenticated=True)
        h.headers.replace_header("Content-Length","999");h.do_POST()
        self.assertEqual(h.send_json.call_args.args[1],400)
        h=self.handler("/api/native/bootstrap",native=True)
        h.headers.replace_header("X-PolyU-Native","synthetic-invalid-é");h.do_POST()
        self.assertEqual(h.send_json.call_args.args[1],403)

    def test_unowned_account_files_are_preserved_and_cannot_attach_to_new_user(self):
        save_private(self.profile/"state.json",{"manual_deadlines":[{"item":"synthetic-old-owner"}]})
        save_private(self.profile/"announcements.json",[{"title":"synthetic-private-notice","posted_at":"2026-10-01T10:00:00Z"}])
        api=Mock();api.get.return_value={"id":"synthetic-new","name":"Synthetic"}
        original=(self.profile/"state.json").read_bytes()
        with patch.object(server,"native_api",return_value=api):
            with self.assertRaises(ValueError):
                server.connect_native({"cookies":[{"name":"synthetic","value":"synthetic","domain":"canvas.polyu.edu.hk"}]})
        api.close.assert_not_called()
        self.assertEqual((self.profile/"state.json").read_bytes(),original)
        data=server.dashboard_data()
        self.assertTrue(data["connection"]["needsDataReview"])
        self.assertIsNone(data["latestCanvasChange"])
        self.assertNotIn("synthetic-private-notice",json.dumps(data))

    def test_mismatched_saved_account_identity_blocks_reauthorization(self):
        save_private(self.profile/"config.json",{"user":{"id":"synthetic-A"}})
        save_private(self.profile/"login.json",{"user_id":"synthetic-B"})
        self.assertTrue(server.account_needs_review())
        with self.assertRaises(ValueError):
            server.connect_native({"cookies":[{"name":"synthetic","value":"synthetic","domain":"canvas.polyu.edu.hk"}]})
