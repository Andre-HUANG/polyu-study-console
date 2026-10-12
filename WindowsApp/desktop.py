"""Windows WebView2 shell; the existing loopback backend owns all business data."""
from __future__ import annotations
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from app_config import VERSION
from native_canvas import HOST, normalized_cookies
from platform_support import APP_NAME, account_root, display_path, is_link
from diagnostic_log import validate_event, error_code


def school_url(url):
    value = urlsplit(url or "")
    return value.scheme == "https" and value.hostname == "canvas.polyu.edu.hk" and value.port in (None, 443) and not value.username and not value.password


def console_url(url, base):
    value, expected = urlsplit(url or ""), urlsplit(base or "")
    return bool(base) and value.scheme == "http" and value.hostname == "127.0.0.1" and value.port == expected.port and not value.username and not value.password


def export_cookies(cookies):
    rows = []
    for cookie in cookies:
        for name, morsel in cookie.items():
            expires = -1
            if morsel["expires"]:
                try: expires = parsedate_to_datetime(morsel["expires"]).timestamp()
                except (ValueError, TypeError, OverflowError, OSError): continue
            rows.append({"name": name, "value": morsel.value, "domain": morsel["domain"],
                         "path": morsel["path"] or "/", "expires": expires,
                         "secure": bool(morsel["secure"]), "httpOnly": bool(morsel["httponly"])})
    return normalized_cookies(rows)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("本机授权请求不允许跳转")


def entry_command(*args):
    if getattr(sys, "frozen", False):
        executable = str(Path(sys.executable).with_name("PolyUWorker.exe")) if args else sys.executable
        return [executable, *args]
    executable = sys.executable
    if sys.platform == "win32":
        # pythonw has no pipe stdout; backend/helper processes require python.exe.
        executable = str(Path(executable).with_name("python.exe" if args else "pythonw.exe"))
    return [executable, "-I", "-B", "-X", "utf8", str(Path(__file__).with_name("main.py")), *args]


def windows_prerequisites():
    """Read only the official .NET/evergreen WebView2 installation markers."""
    import winreg
    framework = 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full", 0,
                winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
            framework = winreg.QueryValueEx(key, "Release")[0]
    except OSError:
        pass
    if not isinstance(framework, int) or framework < 528040:
        raise RuntimeError("缺少 .NET Framework 4.8 或更新版本，请查看安装与使用说明中的微软官方链接")
    client = r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    runtime = None
    for hive, view in ((winreg.HKEY_CURRENT_USER, 0),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)):
        try:
            with winreg.OpenKey(hive, client, 0, winreg.KEY_READ | view) as key:
                version = winreg.QueryValueEx(key, "pv")[0]
            components = version.split(".") if isinstance(version, str) else []
            if len(components) == 4 and all(part.isdigit() for part in components) and any(int(part) for part in components):
                runtime = version
                break
        except OSError:
            pass
    if not runtime:
        raise RuntimeError("缺少 Microsoft Edge WebView2 Runtime（仅安装 Edge 浏览器不足以运行），请查看安装与使用说明中的微软官方链接")
    return {"netFrameworkRelease": framework, "webview2RuntimeVersion": runtime}


class Backend:
    def __init__(self, root):
        self.root = Path(root)
        self.process = self.job = self.log = None
        self.url = None
        self.secret = secrets.token_urlsafe(48)
        self.ready = self.root / (".ready-" + secrets.token_hex(16) + ".json")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.diagnostic_pending = []
        self.diagnostic_lock = threading.RLock()

    def record(self, event, result, code=''):
        validate_event(event, result, code)
        with self.diagnostic_lock:
            self.diagnostic_pending.append({'event': event, 'result': result, 'code': code})
            self.diagnostic_pending = self.diagnostic_pending[-200:]
            self.flush_diagnostics()

    def flush_diagnostics(self):
        with self.diagnostic_lock:
            while self.url and self.diagnostic_pending:
                try: self.request('diagnostics', self.diagnostic_pending[0])
                except Exception: return  # Fixed events remain in memory until recovery.
                self.diagnostic_pending.pop(0)

    def start(self):
        self.secret = secrets.token_urlsafe(48)
        from windows_process import Job, resume_backend
        self.root.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(POLYU_NATIVE_APP="1", POLYU_APP_VERSION=VERSION,
                           POLYU_NATIVE_SECRET=self.secret, POLYU_CONSOLE_DATA_DIR=str(self.root),
                           POLYU_DOCUMENT_READER="builtin:windows", POLYU_READY_FILE=str(self.ready),
                           PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        environment.pop("POLYU_CONSOLE_PORT", None)
        (self.root / 'backend.log').unlink(missing_ok=True)
        try:
            self.process = subprocess.Popen(entry_command("--backend"), env=environment,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004)  # CREATE_SUSPENDED
            self.job = Job(self.process.pid)
            resume_backend(self.process.pid)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.process.poll() is not None: break
                try:
                    ready = json.loads(self.ready.read_text(encoding="utf-8"))
                    url = ready.get("url")
                    if ready.get("pid") == self.process.pid and console_url(url, url):
                        self.url = url
                        self.ready.unlink(missing_ok=True)
                        self.flush_diagnostics()
                        return
                except (OSError, ValueError): pass
                time.sleep(.1)
            raise RuntimeError("本机服务未能启动，请关闭应用后重试")
        except Exception as error:
            self.stop()
            self.record('backend.start', 'failure', error_code(error))
            raise

    def request(self, operation, payload):
        if not self.url or not self.process or self.process.poll() is not None:
            raise RuntimeError("本机服务已停止")
        if operation not in {"session", "logout", "diagnostics", "bootstrap"}: raise ValueError("Unknown native operation")
        request = urllib.request.Request(self.url + "api/native/" + operation,
            data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type":"application/json", "X-PolyU-Native":self.secret})
        try:
            with self.opener.open(request, timeout=3 if operation == 'diagnostics' else 30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            value = json.load(error)
            raise RuntimeError(value.get("error") or "Canvas 授权未完成") from None

    def bootstrap_url(self):
        ticket = self.request("bootstrap", {})["ticket"]
        return self.url + "?view=" + secrets.token_hex(8) + "#bootstrap=" + ticket

    def stop(self):
        self.url = None
        if self.job:
            self.job.close()  # Terminates backend and every parser/sync child first.
            self.job = None
        if self.process:
            try:
                if self.process.poll() is None: self.process.terminate()
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
            self.process = None
        if self.log:
            self.log.close()
            self.log = None
        self.ready.unlink(missing_ok=True)


def mark_logout(root):
    root = Path(root)
    marker = root / "logout-pending"
    if is_link(root) or is_link(marker): raise RuntimeError("账号清理路径无效，未写入链接目标")
    marker.write_text("pending\n", encoding="utf-8")
    return marker


def delete_account(root, backend):
    backend.stop()
    root = Path(root)
    if is_link(root): raise RuntimeError("账号目录路径无效，未删除链接目标")
    if not root.exists(): return
    marker = mark_logout(root)
    for attempt in range(12):
        try:
            # Retain the recovery marker until every account/cache file is gone.
            for child in root.iterdir():
                if child == marker: continue
                if child.is_dir() and not is_link(child): shutil.rmtree(child)
                elif getattr(child, "is_junction", lambda: False)(): child.rmdir()
                else: child.unlink()
            marker.unlink()
            root.rmdir()
            return
        except OSError:
            if root.exists(): mark_logout(root)
            if attempt == 11: raise RuntimeError("账号文件仍被占用；应用已关闭，请重新打开以完成退出清理") from None
            time.sleep(.25)


class Bridge:
    def __init__(self, app): self._app = app

    def native_action(self, action, target=None):
        app = self._app
        if not app.console or not console_url(app.console.get_current_url(), app.backend.url) or app.stopping:
            raise ValueError("仅允许控制台窗口调用本机操作")
        if action == "login": app.login()
        elif action == "logout": app.logout()
        elif action == "openSource":
            if not school_url(target): raise ValueError("来源地址必须属于学校 Canvas")
            webbrowser.open(target)
        elif action in {"dataFolder", "filesFolder"}:
            target = app.root / "account"
            if action == "filesFolder": target /= "documents/files"
            if target.exists(): os.startfile(display_path(target))
        else: raise ValueError("不支持的本机操作")
        return {"ok": True}


class Desktop:
    def __init__(self, root, webview):
        self.root, self.webview = Path(root), webview
        self.backend = Backend(root)
        self.console = self.login_window = None
        self.stopping = self.logged_out = False
        self.connecting = threading.Lock()
        self.login_completed = False

    def notice(self, message):
        if self.console and not self.stopping:
            self.console.run_js("window.polyuNativeNotice && window.polyuNativeNotice(" + json.dumps(message) + ");")

    def login(self):
        self.backend.record('login.open', 'attempt')
        if self.login_window:
            self.login_window.show()
            self.backend.record('login.open', 'success')
            return
        # School pages receive no Python API and no native secret.
        self.login_completed = False
        try:
            window = self.login_window = self.webview.create_window("PolyU Canvas · 学校登录", HOST,
                                                                   width=1000, height=760)
        except Exception as error:
            self.backend.record('login.open', 'failure', error_code(error))
            raise
        window.events.loaded += self._try_login
        window.events.closed += self._login_closed
        self.backend.record('login.open', 'success')

    def _login_closed(self):
        if not self.login_completed and not self.stopping: self.backend.record('login.close', 'cancelled')
        self.login_window = None

    def _try_login(self):
        window = self.login_window
        if self.stopping or not window or not school_url(window.get_current_url()): return
        if urlsplit(window.get_current_url()).path.startswith("/login"): return
        if not self.connecting.acquire(blocking=False): return
        try:
            cookies = export_cookies(window.get_cookies())
            if not cookies or self.stopping: return
            self.backend.record('login.export', 'attempt')
            # The backend verifies a signed-in Canvas page before persisting.
            result = self.backend.request("session", {"cookies":cookies})
            if not self.stopping:
                self.login_completed = True
                self.backend.record('login.export', 'success')
                self.notice(result.get("message") or "Canvas 已连接")
                window.destroy()
                self.console.load_url(self.backend.bootstrap_url())
        except Exception as error:
            if not self.stopping: self.backend.record('login.export', 'failure', error_code(error))
            if not self.stopping: self.notice(str(error))
        finally:
            self.connecting.release()

    def logout(self):
        if self.stopping: return
        self.backend.record('logout', 'attempt')
        if not self.console.create_confirmation_dialog("退出登录", "将删除本应用的账号资料、缓存与登录会话。是否继续？"):
            self.backend.record('logout', 'cancelled')
            return
        try: mark_logout(self.root)
        except Exception as error:
            self.backend.record('logout.cleanup', 'failure', error_code(error))
            raise
        self.stopping = self.logged_out = True
        # Deletion waits until WebView2 has closed; stop writers before closing it.
        self.backend.stop()
        self.backend.diagnostic_pending.clear()
        for window in list(self.webview.windows):
            try: window.clear_cookies()
            except Exception: pass
            window.destroy()

    def run(self):
        marker = self.root / "logout-pending"
        if marker.exists(): delete_account(self.root, self.backend)
        self.backend.start()
        if os.environ.pop('POLYU_LOGOUT_RESULT', '') == 'success':
            self.backend.record('logout', 'attempt')
            self.backend.record('logout.cleanup', 'success')
            self.backend.record('logout', 'success')
        self.console = self.webview.create_window(APP_NAME, self.backend.bootstrap_url(),
                    js_api=Bridge(self), width=1360, height=900, min_size=(850,620))
        self.console.events.closing += self._close_login
        try:
            self.webview.settings.update(ALLOW_FILE_URLS=False, ALLOW_DOWNLOADS=False,
                IGNORE_SSL_ERRORS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False,
                REMOTE_DEBUGGING_PORT=None)
            resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            icon = resources / "console/assets/polyu-favicon.ico"
            if not icon.is_file(): icon = resources / "学习控制台/assets/polyu-favicon.ico"
            self.webview.start(gui="edgechromium", private_mode=True, icon=str(icon),
                               storage_path=display_path(self.root / "webview"), debug=False)
        finally:
            self.stopping = True
            self.backend.stop()
        if self.logged_out:
            try: delete_account(self.root, self.backend)
            except Exception as error:
                self.backend.record('logout.cleanup', 'failure', error_code(error))
                raise

    def _close_login(self):
        self.stopping = True
        if self.login_window: self.login_window.destroy()


def main():
    if sys.platform != "win32": raise SystemExit("Windows 桌面外壳只能在 Windows 上启动")
    if sys.getwindowsversion().build < 19041: raise RuntimeError("需要 Windows 10 2004 或更新版本")
    windows_prerequisites()
    from windows_process import SingleInstance
    root = account_root()
    mutex = SingleInstance("Local\\PolyUStudyConsole-" + hashlib.sha256(str(root).encode()).hexdigest()[:24])
    try:
        import webview
        app = Desktop(root, webview)
        app.run()
    finally:
        mutex.close()
    if app.logged_out:
        environment = os.environ.copy()
        environment['POLYU_LOGOUT_RESULT'] = 'success'
        subprocess.Popen(entry_command(), close_fds=True, env=environment)
