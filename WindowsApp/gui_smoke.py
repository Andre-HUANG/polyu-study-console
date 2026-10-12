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

        def make(*args, **kwargs):
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
                        results.extend(["authenticatedEmptyDashboard", "bootstrapFragmentRemoved", "settingsOpened"])
                    except Exception as error:
                        errors.append(type(error).__name__)
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
        "realCanvasValidated":False, "realAccountAccessed":False}
    target = os.environ.get("POLYU_GUI_EVIDENCE")
    if target: Path(target).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__": main()
