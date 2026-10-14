"""Dedicated WebView2 messages. School credentials never enter pywebview's generic bridge."""
import json
from pathlib import Path
from credential_store import sso_url


def script(resources, attempt, enabled):
    source = (resources / "console/assets/remember-login.js")
    if not source.exists(): source = resources / "学习控制台/assets/remember-login.js"
    config = {"platform": "windows", "nonce": attempt.nonce, "enabled": enabled}
    return source.read_text(encoding="utf-8").replace("__POLYU_REMEMBER_CONFIG__", json.dumps(config))


class SchoolCredentialBridge:
    def __init__(self, app, window, resources):
        self.app, self.window, self.resources = app, window, resources
        self.attempt = app.login_attempt
        self.closed = False
        self.browser = window.native.browser
        self.control = self.browser.webview
        self.control.WebMessageReceived -= self.browser.on_script_notify
        self.control.WebMessageReceived += self.receive
        self.control.CoreWebView2InitializationCompleted += self.initialized
        self.control.NavigationCompleted += self.navigation_completed
        if self.control.CoreWebView2 is not None: self.initialized(None, None)
        window.events.loaded += self.loaded

    def initialized(self, sender, event):
        if self.control.CoreWebView2 is not None:
            settings = self.control.CoreWebView2.Settings
            settings.IsPasswordAutosaveEnabled = False
            settings.IsGeneralAutofillEnabled = False

    def navigation_completed(self, sender, event):
        if not event.IsSuccess: self.attempt.candidate = None

    def loaded(self):
        if not self.attempt.active or self.app.stopping or self.app.login_attempt is not self.attempt: return
        status = self.app.credentials.status(self.app.account_owner())
        if not status["enabled"] or not sso_url(self.window.get_current_url()): return
        self.window.run_js(script(self.resources, self.attempt, True))

    def receive(self, sender, event):
        # Do not delegate, log exceptions, or include the event body in an error.
        if self.app.stopping or self.app.login_window is not self.window or self.app.login_attempt is not self.attempt: return
        try:
            raw = event.get_WebMessageAsJson()
            if len(raw) > 32768: return
            source = str(event.Source)
            if not sso_url(source): return
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("nonce") != self.attempt.nonce or message.get("type") != "polyuRemember": return
            status = self.app.credentials.status(self.app.account_owner())
            if not self.attempt.receive(source, message, status["enabled"]): return
            if message["kind"] != "ready" or self.attempt.filled: return
            record = self.app.credentials.record()
            if not record or (self.app.account_owner() and str(self.app.account_owner()) != record["verifiedUserId"]): return
            if not sso_url(str(self.control.Source)): return
            value = {"nonce": self.attempt.nonce, "documentId": self.attempt.document,
                     "username": record["username"], "password": record["password"]}
            encoded = json.dumps(value, ensure_ascii=True)
            # The origin guard also runs at execution time, after possible navigation.
            guarded = "(() => { if (window !== window.top || location.origin !== 'https://adfs.polyu.edu.hk' || location.pathname !== '/adfs/ls/') return; window.polyuRememberFill?.(" + encoded + "); })();"
            self.control.CoreWebView2.ExecuteScriptAsync(guarded)
            self.attempt.filled = True
        except Exception:
            self.attempt.candidate = None
            self.app.notice("本机记住密码暂不可用，请在学校页面手动输入。")

    def close(self):
        self.attempt.close()
        if self.closed: return
        self.closed = True
        try: self.window.events.loaded -= self.loaded
        except Exception: pass
        # pywebview closes child forms without waiting for the shared browser.
        # Release this login controller on its owning UI thread before teardown.
        def dispose():
            for event, handler in (("WebMessageReceived", self.receive),
                                   ("CoreWebView2InitializationCompleted", self.initialized),
                                   ("NavigationCompleted", self.navigation_completed)):
                try:
                    hook = getattr(self.control, event)
                    hook -= handler
                    setattr(self.control, event, hook)
                except Exception: pass
            if not self.control.IsDisposed:
                if self.control.CoreWebView2 is not None: self.control.CoreWebView2.Stop()
                self.control.Dispose()
        try:
            from System import Action
            self.window.native.Invoke(Action(dispose))
        except Exception: pass  # The owning form may already have disposed it.
