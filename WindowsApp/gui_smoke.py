"""Production Windows shell in a synthetic profile, no Canvas login."""
import json
import hashlib
import os
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    if sys.platform != "win32": raise SystemExit("Requires Windows WebView2")
    import webview
    from desktop import Desktop, APP_NAME, delete_account
    results, errors = [], []
    with tempfile.TemporaryDirectory(prefix="polyu-webview2-smoke-") as temporary:
        root = Path(temporary) / "synthetic-app"
        app = Desktop(root, webview)
        create = webview.create_window
        credential_checks = {}

        def make(*args, **kwargs):
            if args[0] != APP_NAME and len(args) > 1 and args[1] == 'https://canvas.polyu.edu.hk':
                args = (args[0], 'about:blank', *args[2:])
            window = create(*args, **kwargs)
            if args[0] == APP_NAME:
                def loaded():
                    try:
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            ready = window.evaluate_js("document.getElementById('metricCourses')?.textContent === '0'")
                            if ready: break
                            time.sleep(.1)
                        assert ready, "Native dashboard failed to render"
                        assert "bootstrap=" not in window.get_current_url()
                        window.evaluate_js("window.openSettings()")
                        assert window.evaluate_js("document.getElementById('settingsDialog').open")
                        assert window.evaluate_js("document.getElementById('rememberPasswordToggle')?.checked === false && document.getElementById('rememberPasswordStatus')?.textContent === '未开启' && document.getElementById('forgetPasswordButton')?.hidden === true")
                        results.extend(["authenticatedEmptyDashboard", "bootstrapFragmentRemoved", "settingsOpened", "rememberPasswordDefaultOff"])
                        from credential_gui_smoke import verify
                        credential_checks.update(verify(app))
                        results.append("nativeRememberedPasswordSyntheticFlow")
                    except Exception as error:
                        errors.append({"type":type(error).__name__, "stage":getattr(app, "_credential_smoke_stage", "dashboard")})
                    finally:
                        window.destroy()
                window.events.loaded += loaded
            return window

        webview.create_window = make
        try: app.run()
        finally: webview.create_window = create
        assert not errors, errors
        assert results and app.backend.process is None and app.backend.job is None
        delete_account(root, app.backend)
        assert not root.exists()
    manifest_digest=hashlib.sha256((Path(sys.executable).resolve().parent.parent/"payload-sha256.json").read_bytes()).hexdigest()
    report = {"ok":True, "payloadManifestSHA256":manifest_digest, "checks":results, "productionWindowsShell":True,
        "webview2NativeGUI":True, "backendClosed":True, "syntheticProfileDeleted":True,
        "realCanvasValidated":False, "realAccountAccessed":False, "rememberPassword":credential_checks}
    target = os.environ.get("POLYU_GUI_EVIDENCE")
    if target: Path(target).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__": main()
