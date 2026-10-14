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
        if operation not in {"session", "logout", "diagnostics", "bootstrap", "update/prepare"}: raise ValueError("Unknown native operation")
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


def mark_logout(root, keep_remembered_password=False):
    root = Path(root)
    marker = root / "logout-pending"
    if is_link(root) or is_link(marker): raise RuntimeError("账号清理路径无效，未写入链接目标")
    temporary = root / (".logout-" + secrets.token_hex(16))
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump({"version": 2, "keepRememberedPassword": bool(keep_remembered_password)}, stream)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally: temporary.unlink(missing_ok=True)
    return marker


def delete_account(root, backend):
    backend.stop()
    root = Path(root)
    if is_link(root): raise RuntimeError("账号目录路径无效，未删除链接目标")
    if not root.exists(): return
    keep = read_logout_policy(root)
    marker = mark_logout(root, keep)
    if not keep and (root / "credentials").exists(): RememberedCredentials(root).forget()
    for attempt in range(60):
        try:
            # Retain the recovery marker until every account/cache file is gone.
            for child in root.iterdir():
                if child == marker or (keep and child.name == "credentials"):
                    if child.name == "credentials" and is_link(child): raise RuntimeError("密码目录无效，未保留链接目标")
                    continue
                if child.is_dir() and not is_link(child): shutil.rmtree(child)
                elif getattr(child, "is_junction", lambda: False)(): child.rmdir()
                else: child.unlink()
            marker.unlink()
            if not keep: root.rmdir()
            return
        except OSError:
            if root.exists(): mark_logout(root, keep)
            if attempt == 59: raise RuntimeError("账号文件仍被占用；应用已关闭，请重新打开以完成退出清理") from None
            time.sleep(.25)


from credential_store import RememberedCredentials, LoginAttempt, CONSENT, FAILURE, read_logout_policy
from native_dialogs import confirm as native_confirm


class Bridge:
    def __init__(self, app): self._app = app

    def native_action(self, action, target=None):
        app = self._app
        if not app.console or not console_url(app.console.get_current_url(), app.backend.url) or app.stopping:
            raise ValueError("仅允许控制台窗口调用本机操作")
        if action == "rememberStatus": app.publish_credentials()
        elif action == "rememberEnable": app.enable_credentials()
        elif action == "rememberForget": app.forget_credentials()
        elif action == "login": app.login()
        elif action == "logout": app.logout()
        elif action == "softwareUpdate":
            if not app.updater: raise ValueError("更新组件未启动")
            app.updater.check()
        elif action == "academicCalendar":
            webbrowser.open("https://www.polyu.edu.hk/ar/students-in-taught-programmes/academic-calendar/?sc_lang=en")
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
        self.updater = None
        self.native_hidden = False
        self.power_handler = None
        self.root, self.webview = Path(root), webview
        self.backend = Backend(root)
        self.console = self.login_window = None
        self.stopping = self.logged_out = False
        self.connecting = threading.Lock()
        self.login_completed = False
        self.credentials = RememberedCredentials(self.root)
        self.login_attempt = None
        self.credential_bridge = None
        self.pending_credential_notice = None

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
        if self.login_attempt: self.login_attempt.close()
        self.login_attempt = LoginAttempt()
        try:
            window = self.login_window = self.webview.create_window("PolyU Canvas · 学校登录", HOST,
                                                                   width=1000, height=760)
        except Exception as error:
            self.backend.record('login.open', 'failure', error_code(error))
            raise
        window.events.loaded += self._try_login
        window.events.closed += self._login_closed
        def closing():
            if self.login_window is window and self.credential_bridge:
                self.credential_bridge.close()
        window.events.closing += closing
        # create_window can finish showing a child before it returns. Waiting on
        # the latched event covers both an already-shown and a future window.
        threading.Thread(target=self._attach_credential_bridge,
                         args=(window, self.login_attempt), daemon=True).start()
        self.backend.record('login.open', 'success')

    def _login_closed(self):
        if self.credential_bridge: self.credential_bridge.close()
        self.credential_bridge = None
        if self.login_attempt: self.login_attempt.close()
        self.login_attempt = None
        if not self.login_completed and not self.stopping: self.backend.record('login.close', 'cancelled')
        self.login_window = None

    def _try_login(self):
        window = self.login_window
        attempt = self.login_attempt
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
                self.backend.record('login.export', 'success')
                self._save_login_credential(result, window, attempt)
                if self.stopping or self.login_window is not window: return
                self.login_completed = True
                self.notice(result.get("message") or "Canvas 已连接")
                window.destroy()
                self.console.load_url(self.backend.bootstrap_url())
        except Exception as error:
            if attempt: attempt.candidate = None
            if not self.stopping: self.backend.record('login.export', 'failure', error_code(error))
            if not self.stopping: self.notice(str(error))
        finally:
            self.connecting.release()


    def account_owner(self):
        try:
            value = json.loads((self.root / "account/config.json").read_text(encoding="utf-8"))
            return str(value["user"]["id"])
        except (ValueError, OSError, KeyError, TypeError): return None

    def publish_credentials(self):
        if not self.console or self.stopping: return
        value = self.credentials.status(self.account_owner())
        self.console.run_js("window.polyuRememberStatus?.(" + json.dumps(value, ensure_ascii=True) + ");")
        if self.pending_credential_notice:
            self.notice(self.pending_credential_notice)
            self.pending_credential_notice = None

    def enable_credentials(self):
        try:
            accepted, _ = native_confirm(self.console, "在本机记住账号和密码？", CONSENT)
            if accepted and not self.stopping:
                self.credentials.enable()
                if self.credential_bridge: self.credential_bridge.loaded()
        except Exception: self.notice(FAILURE)
        self.publish_credentials()

    def forget_credentials(self):
        if self.login_attempt: self.login_attempt.close()
        if self.login_window: self.login_window.destroy()
        try:
            self.credentials.forget()
            self.notice("已删除本机保存的密码并关闭记住密码。")
        except Exception: self.notice(FAILURE)
        self.publish_credentials()

    def _attach_credential_bridge(self, window, attempt):
        def current():
            return (not self.stopping and self.login_window is window and
                    self.login_attempt is attempt and attempt.active)
        if not current(): return
        try:
            if not window.events.shown.wait(20): raise RuntimeError("School window unavailable")
            if not current(): return
            from System import Action
            from login_credentials import SchoolCredentialBridge
            resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            attached = []
            def attach():
                if not current() or self.credential_bridge is not None: return
                bridge = SchoolCredentialBridge(self, window, resources)
                self.credential_bridge = bridge
                attached.append(bridge)
            window.native.Invoke(Action(attach))
            if attached: attached[0].loaded()
        except Exception:
            if current():
                attempt.close()
                self.notice("本机记住密码暂不可用，请在学校页面手动输入。")

    def _save_login_credential(self, result, window, attempt):
        if (not attempt or not attempt.active or self.login_attempt is not attempt or self.stopping or
            self.login_window is not window or not attempt.candidate or not self.credentials.status()["enabled"]): return
        user_id = result.get("verifiedUserId")
        if not isinstance(user_id, str) or not user_id.isdigit():
            attempt.candidate = None; return
        try:
            previous = self.credentials.record()
            if previous and previous["verifiedUserId"] != user_id:
                accepted, _ = native_confirm(self.console, "替换本机记住的学校账号？", "这次登录的是另一账号。是否用本次成功登录的账号密码替换已保存的密码？", ok="替换已记住的密码")
                if not accepted: return
            if attempt.active and self.login_attempt is attempt and not self.stopping and self.login_window is window:
                username, password = attempt.candidate
                self.credentials.save_verified(username, password, user_id)
        except Exception: self.pending_credential_notice = "Canvas 已连接，但密码未能保存。" + FAILURE
        finally: attempt.candidate = None

    def logout(self):
        if self.stopping: return
        self.backend.record('logout', 'attempt')
        accepted, keep = native_confirm(self.console, "退出登录", "将删除本应用的学习资料、缓存和登录会话。未勾选保留时，也会删除已记住的密码。", ok="退出并删除", checkbox=self.credentials.file.exists())
        if not accepted:
            self.backend.record('logout', 'cancelled')
            return
        try: mark_logout(self.root, keep)
        except Exception as error:
            self.backend.record('logout.cleanup', 'failure', error_code(error))
            raise
        self.stopping = self.logged_out = True
        if self.login_attempt: self.login_attempt.close()
        # Deletion waits until WebView2 has closed; stop writers before closing it.
        self.backend.stop()
        self.backend.diagnostic_pending.clear()
        for window in list(self.webview.windows):
            try: window.clear_cookies()
            except Exception: pass
            window.destroy()

    def runtime_state(self, hidden=None):
        if hidden is not None: self.native_hidden=bool(hidden)
        low_power=False
        try:
            import ctypes
            class Power(ctypes.Structure):
                _fields_=[("ac",ctypes.c_ubyte),("flags",ctypes.c_ubyte),("percent",ctypes.c_ubyte),("saver",ctypes.c_ubyte),("life",ctypes.c_ulong),("full",ctypes.c_ulong)]
            power=Power()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(power)): low_power=power.saver==1
        except Exception: pass
        if self.console:
            try:self.console.evaluate_js("window.polyuRuntimeState?.("+json.dumps({"hidden":self.native_hidden,"lowPower":low_power})+")")
            except Exception:pass

    def initialize_power_events(self):
        self.runtime_state()
        if self.power_handler: return
        try:
            from Microsoft.Win32 import SystemEvents
            self.power_handler=lambda sender,event:self.runtime_state()
            SystemEvents.PowerModeChanged+=self.power_handler
        except Exception: self.power_handler=None

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
        def initialize_update():
            if self.stopping: return
            self.initialize_power_events()
            try:
                from updater import Updater
                if not self.updater: self.updater = Updater(self)
            except Exception: self.notice("更新组件暂不可用，请重新安装完整的 1.9.1。")
            acknowledgement = os.environ.pop("POLYU_UPDATE_ACK", "")
            if acknowledgement:
                target=Path(acknowledgement)
                if target.name=="new-version-ready" and target.parent.name.startswith("PolyU-program-update-"):
                    deadline=time.monotonic()+30
                    while time.monotonic()<deadline and not self.stopping:
                        if self.console.evaluate_js("typeof state !== 'undefined' && state.data?.version") == VERSION:
                            target.write_text(VERSION);break
                        threading.Event().wait(.25)
        self.console.events.loaded += initialize_update
        self.console.events.closing += self._close_login
        self.console.events.loaded += self.publish_credentials
        self.console.events.minimized += lambda: self.runtime_state(True)
        self.console.events.restored += lambda: self.runtime_state(False)
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
            if self.power_handler:
                try:
                    from Microsoft.Win32 import SystemEvents
                    SystemEvents.PowerModeChanged-=self.power_handler
                except Exception: pass
                self.power_handler=None
            if self.updater: self.updater.close()
            self.backend.stop()
        if self.logged_out:
            try: delete_account(self.root, self.backend)
            except Exception as error:
                self.backend.record('logout.cleanup', 'failure', error_code(error))
                raise

    def _close_login(self):
        self.stopping = True
        if self.login_attempt: self.login_attempt.close()
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
