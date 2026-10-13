"""Offline checks for real platform boundaries; no GUI or account required."""
import json
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "学习控制台"))
import platform_support
import native_canvas
import server
import desktop
from canvas_api import Canvas
from canvas_archive import safe_component


class PlatformChecks(unittest.TestCase):
    def test_native_update_prepare_uses_authorized_fixed_backend_operation(self):
        backend=desktop.Backend(Path(tempfile.gettempdir())/"synthetic-backend")
        backend.url="http://127.0.0.1:12345/";backend.secret="synthetic-native-secret";backend.process=Mock()
        backend.process.poll.return_value=None
        from io import BytesIO
        backend.opener=Mock();backend.opener.open.return_value=BytesIO(b'{"ok":true}')
        self.assertEqual(backend.request("update/prepare",{}),{"ok":True})
        request=backend.opener.open.call_args.args[0]
        self.assertEqual(request.full_url,"http://127.0.0.1:12345/api/native/update/prepare")
        self.assertEqual(request.get_header("X-polyu-native"),"synthetic-native-secret")
        with self.assertRaises(ValueError):backend.request("../arbitrary",{})

    def test_windows_never_calls_posix_setsid(self):
        with patch.object(platform_support, "IS_WINDOWS", True), patch.object(platform_support.os, "setsid", create=True) as group:
            platform_support.backend_session()
            group.assert_not_called()

    def test_mac_retains_process_group(self):
        with patch.object(platform_support, "IS_WINDOWS", False), patch.object(platform_support.os, "setsid", create=True) as group:
            platform_support.backend_session()
            group.assert_called_once_with()

    def test_mac_existing_session_is_allowed(self):
        with patch.object(platform_support, "IS_WINDOWS", False), patch.object(platform_support.os, "setsid", side_effect=OSError, create=True):
            platform_support.backend_session()

    def test_windows_bootstrap_runs_in_synthetic_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            http = Mock(server_port=8766)
            http.serve_forever.side_effect = KeyboardInterrupt
            with patch.multiple(server, PROJECT=root, PROFILE=root / "account", NATIVE=True, NATIVE_SECRET="synthetic-" + "x"*48), \
                 patch.object(platform_support, "IS_WINDOWS", True), \
                 patch.object(platform_support.os, "setsid", create=True) as group, \
                 patch.object(server, "ThreadingHTTPServer", return_value=http), \
                 patch.object(server.threading, "Thread"), \
                 patch.dict(server.os.environ, {"POLYU_READY_FILE": str(root / "ready.json"), "POLYU_CONSOLE_PORT": "0"}), \
                 patch.object(sys, "argv", ["server.py", "--no-open"]):
                server.main()
            group.assert_not_called()
            http.server_close.assert_called_once()
            self.assertEqual(json.loads((root / "ready.json").read_text())["url"], "http://127.0.0.1:8766/")

    def test_extended_paths_and_shell_paths(self):
        with patch.object(platform_support, "IS_WINDOWS", True):
            path = "C:\\Users\\学生\\" + "课程\\" * 100
            extended = platform_support.long_path(path)
            self.assertTrue(extended.startswith("\\\\?\\C:"))
            self.assertEqual(platform_support.display_path(extended), path)
            self.assertEqual(platform_support.display_path(platform_support.long_path("\\\\host\\share\\file")), "\\\\host\\share\\file")

    def test_windows_reserved_names_and_unicode(self):
        for name in ("CON.pdf", "aux", "Lpt9.txt", "COM¹.csv"):
            self.assertTrue(safe_component(name, "file").startswith("_"))
        name = safe_component("课程😀" * 100 + ".pdf", "file")
        self.assertLessEqual(len(name.encode("utf-16-le")), 320)
        self.assertLessEqual(len(name.encode("utf-8")), 230)
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(safe_component("课程?.pdf", "file"), "课程_.pdf")

    def test_windows_cookies_are_protected_and_round_trip(self):
        cookies = [{"name": "session", "value": "synthetic-secret"}]
        encoded = json.dumps(cookies, ensure_ascii=False).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "canvas-cookies.json"
            with patch.object(native_canvas, "IS_WINDOWS", True), patch.object(native_canvas, "dpapi", side_effect=[b"protected", encoded]) as protect:
                native_canvas.save_private(path, cookies)
                self.assertNotIn("synthetic-secret", path.read_text())
                self.assertEqual(native_canvas.load_cookies(path), cookies)
            self.assertEqual(protect.call_args_list[1].kwargs, {"decrypt": True})

    @unittest.skipUnless(sys.platform == "darwin", "Mac Keychain storage boundary")
    def test_mac_cookie_format_remains_compatible(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(native_canvas, "IS_WINDOWS", False), \
             patch("session_store.vault", side_effect=[None, [{"name": "session", "value": "synthetic"}], [{"name": "session", "value": "synthetic"}]]):
            path = Path(directory) / "canvas-cookies.json"
            native_canvas.save_private(path, [{"name": "session", "value": "synthetic"}])
            self.assertEqual(native_canvas.load_cookies(path), [{"name": "session", "value": "synthetic"}])

    def test_frozen_pdf_helper_uses_explicit_mode(self):
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(platform_support.helper_command("pdf_layout.py", "课程.pdf", "[1]")[1:], ["--pdf-layout", "课程.pdf", "[1]"])
            with self.assertRaises(ValueError):
                platform_support.helper_command("arbitrary.py")

    def test_private_pythonw_routes_workers_to_console_python_and_keeps_unicode(self):
        executable = r"C:\应用 目录\runtime\pythonw.exe"
        pdf = r"C:\课程 文件\课程😀与考核.pdf"
        with patch.object(sys, "platform", "win32"), patch.object(sys, "executable", executable), \
             patch.object(sys, "frozen", False, create=True), \
             patch.object(platform_support, "IS_WINDOWS", True), \
             patch.object(platform_support, "Path", PureWindowsPath), \
             patch.object(desktop, "Path", PureWindowsPath), \
             patch.object(platform_support, "__file__", r"C:\应用 目录\console\platform_support.py"), \
             patch.object(desktop, "__file__", r"C:\应用 目录\WindowsApp\desktop.py"):
            worker = desktop.entry_command("--backend")
            self.assertEqual(worker, [r"C:\应用 目录\runtime\python.exe", "-I", "-B", "-X", "utf8", r"C:\应用 目录\WindowsApp\main.py", "--backend"])
            gui = desktop.entry_command()
            self.assertEqual(gui, [executable, "-I", "-B", "-X", "utf8", r"C:\应用 目录\WindowsApp\main.py"])
            helper = platform_support.helper_command("pdf_layout.py", pdf, "[1, 3]")
            self.assertEqual(helper, [r"C:\应用 目录\runtime\python.exe", "-I", "-B", "-X", "utf8", r"C:\应用 目录\console\pdf_layout.py", pdf, "[1, 3]"])

    def test_side_effecting_get_is_rejected_before_network(self):
        api = Canvas("https://canvas.polyu.edu.hk")
        with patch.object(api.opener, "open") as request:
            with self.assertRaises(ValueError):
                api.fetch("https://canvas.polyu.edu.hk/api/v1/courses/1/folders/media", "application/json")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
