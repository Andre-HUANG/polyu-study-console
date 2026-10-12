"""Test only a dedicated disposable synthetic keychain; preserve other search-list entries."""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "学习控制台"))
import session_store
from native_canvas import write_private, load_cookies, save_private


def main():
    if sys.platform != "darwin": raise SystemExit("Requires macOS")
    with tempfile.TemporaryDirectory(prefix="polyu-keychain-") as directory:
        root = Path(directory)
        helper = root / "SessionVault"
        subprocess.run(["xcrun", "swiftc", "-O", "-module-cache-path", str(root/"cache"),
                        str(ROOT/"MacApp/SessionVault.swift"), "-o", str(helper)], check=True, capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(helper)], check=True, capture_output=True)
        keychain = root / "synthetic.keychain-db"
        before = subprocess.check_output(["security", "list-keychains", "-d", "user"])
        subprocess.run(["security", "create-keychain", "-p", secrets.token_urlsafe(32), str(keychain)], check=True, capture_output=True)
        previous = os.environ.copy()
        try:
            os.environ.update(POLYU_SESSION_VAULT=str(helper), POLYU_TEST_ISOLATION="1", POLYU_TEST_KEYCHAIN=str(keychain))
            path = root / "account/browser/canvas-cookies.json"
            rows = [{"name": "synthetic-session", "value": "synthetic-only", "domain": "canvas.polyu.edu.hk", "expires": -1}]
            write_private(path, rows)
            assert load_cookies(path) == rows
            marker = json.loads(path.read_text())
            assert marker["protection"] == session_store.PROTECTION and "synthetic-only" not in path.read_text()
            assert load_cookies(path) == rows
            second = root / "reinstalled/SessionVault"; second.parent.mkdir(); shutil.copyfile(helper, second); second.chmod(0o700)
            os.environ["POLYU_SESSION_VAULT"] = str(second)
            assert load_cookies(path) == rows, "Unchanged signed helper must work after reinstall"
            subprocess.run(["security", "lock-keychain", str(keychain)], check=True, capture_output=True)
            original = path.read_bytes()
            try:
                save_private(path, [{"name": "synthetic-new", "value": "synthetic-new"}])
                raise AssertionError("Locked keychain accepted a write")
            except RuntimeError: pass
            assert path.read_bytes() == original, "Failure must preserve prior marker"
        finally:
            os.environ.clear(); os.environ.update(previous)
            subprocess.run(["security", "delete-keychain", str(keychain)], check=True, capture_output=True)
            after = subprocess.check_output(["security", "list-keychains", "-d", "user"])
            assert before == after, "Existing keychain search list changed"
    report = {"ok": True, "macosKeychainRoundTrip": True, "legacyMigrationReadBack": True,
              "unchangedHelperReinstall": True, "lockedKeychainFailsClosed": True,
              "existingKeychainSearchListPreserved": True, "realAccountAccessed": False,
              "changedAdHocSigningIdentity": "NOT TESTED"}
    (ROOT/"验证/keychain-evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__": main()
