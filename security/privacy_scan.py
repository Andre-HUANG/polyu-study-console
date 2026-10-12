"""Redacted repository/staged/history/artifact privacy gate. Standard library only."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
GIT = os.environ.get("POLYU_GIT") or shutil.which("git") or "git"
EMAIL = re.compile(rb"[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_PATH = re.compile(rb"(?<![A-Za-z0-9_/:.])(?:/(?:Users|home)/[A-Za-z0-9_.\-]+/|[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.\-]+[\\/])")
CREDENTIALS = {
    "credential": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_\-]{30,}|AKIA[A-Z0-9]{16})"),
    "identity-number": re.compile(rb"(?<![A-Za-z0-9])[A-Z]{1,2}[0-9]{6}\([0-9A]\)(?![A-Za-z0-9])"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{32,}"),
    "session-value": re.compile(rb"(?i)[\"']?(?:_?canvas_session|access_token|refresh_token|session_token)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_./+\-=]{20,}[\"']"),
}
PRIVATE_NAMES = {"canvas-cookies.json", "login.json", "snapshot.json", "grading_schemes.json",
                 "canvas-course-grades.json", "canvas-grading-settings.json", "managed-deadlines.json",
                 "native-gui-evidence.json", "gui-smoke-evidence.json", "mac-smoke-evidence.json",
                 "release-artifacts-evidence.json", "test-evidence.json", "publication-checks.json"}
PRIVATE_DIRS = {"account", ".jiujiastudy", "documents", "raw", "diagnostics", "webview", "browser", ".security-local", ".build", "native-gui", "gui-component"}
ALLOWED_EMAIL_SUFFIX = (b"@users.noreply.github.com", b"@example.com", b"@example.org", b"@example.invalid")
PUBLIC_EMAILS = {b"noreply@github.com"}
OWNER_EMAILS = None
PUBLIC_CA_SHA256 = "bbc7e9c01d7551bb8a159b5dedd989b8ee3ce105aff522b68eb1b01bf854cab0"
WINDOWS_LIBRARY_FILES = json.loads((ROOT / "security/dependency-exceptions.json").read_text())["windowsLibraryFiles"]
VENDOR_SHA256 = "a02ac8b749df11924e0e054ab933f68c614261d6d29a749e673b2bc60a45b955"
# Reviewed old objects remain public until separately authorized history rewriting.
LEGACY_COMMITS = {
    "d176d8e791fb0a79c9ad8d8b00af488bdb8538f7", "8de6eb4736130c35b93e866b21b0cf329b636b43",
    "9cb23a9073de037221b94f2bbaa2c78e916fd8a3", "0f002f34e6fb37dc80fcdd93405cf446f6c1d810",
    "fb9799c58f3f057b39aa52f7206a514a20198ae4", "b381ab2889a0117b1770569986867d0a7ba94307"}



def git(*args):
    return subprocess.check_output([GIT, "-C", str(ROOT), *args], stderr=subprocess.PIPE)


def finding(path, rule, data=b"", match=None, classification="FAIL"):
    result = {"path": path, "rule": rule, "classification": classification}
    if match:
        result["line"] = data[:match.start()].count(b"\n") + 1
        result["fingerprint"] = hashlib.sha256(match.group()).hexdigest()[:16]
    return result


def scan_bytes(path, data, third_party=False):
    rows = []
    global OWNER_EMAILS
    if OWNER_EMAILS is None:
        try:
            OWNER_EMAILS = {x.lower() for x in git("log", "--all", "--format=%ae%n%ce").splitlines()
                            if x.lower().endswith(b"@gmail.com")}
        except (OSError, subprocess.CalledProcessError): OWNER_EMAILS = set()
    for rule, pattern in CREDENTIALS.items():
        for match in pattern.finditer(data):
            # A accidental token prefix inside Pillow's pinned embedded font base64.
            if (third_party and path.endswith("PIL/ImageFont.py") and rule == "credential" and
                hashlib.sha256(data).hexdigest() in {json.loads((ROOT / "MacApp/document-vendor-lock.json").read_text())["files"]["PIL/ImageFont.py"], WINDOWS_LIBRARY_FILES["PIL/ImageFont.py"]} and
                hashlib.sha256(match.group()).hexdigest().startswith("57a26f03da1274be")):
                continue
            rows.append(finding(path, rule, data, match))
    for match in EMAIL.finditer(data):
        address = match.group().lower()
        if address.endswith(ALLOWED_EMAIL_SUFFIX) or address in PUBLIC_EMAILS: continue
        if re.fullmatch(rb"icon_[0-9]+x[0-9]+@2x\.png", address): continue
        rows.append(finding(path, "email", data, match, "THIRD_PARTY" if third_party and address not in OWNER_EMAILS else "FAIL"))
    for match in USER_PATH.finditer(data):
        rows.append(finding(path, "local-user-path", data, match, "THIRD_PARTY" if third_party else "FAIL"))
    return rows


def risky_path(path):
    parts = PurePosixPath(path.replace("\\", "/")).parts
    return (any(p in PRIVATE_DIRS for p in parts) or any(p in PRIVATE_NAMES for p in parts) or
            any(p.startswith(("native-gui-", "gui-component-", ".ready-")) for p in parts) or
            path.lower().endswith((".pem", ".key", ".p12", ".pfx", ".keychain-db", ".log", ".pdb")))


def vendor(data):
    if hashlib.sha256(data).hexdigest() != VENDOR_SHA256:
        return [finding("MacApp/document-vendor.zip", "unapproved-dependency-archive")]
    import io
    lock = json.loads((ROOT / "MacApp/document-vendor-lock.json").read_text())["files"]
    rows = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if set(archive.namelist()) != set(lock) or len(archive.namelist()) != len(lock):
            return [finding("MacApp/document-vendor.zip", "dependency-inventory-mismatch")]
        for name, expected in lock.items():
            contents = archive.read(name)
            if hashlib.sha256(contents).hexdigest() != expected:
                rows.append(finding(name, "dependency-hash-mismatch"))
            rows += scan_bytes("MacApp/document-vendor.zip/" + name, contents, third_party=True)
    return rows


def files(staged=False):
    if staged:
        paths = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode().split("\0")
    else:
        paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    rows, count = [], 0
    for path in sorted(set(filter(None, paths))):
        if not staged and not (ROOT / path).is_file(): continue
        count += 1
        if risky_path(path): rows.append(finding(path, "private-generated-path"))
        if (ROOT / path).is_symlink(): rows.append(finding(path, "source-symlink")); continue
        data = git("show", ":" + path) if staged else (ROOT / path).read_bytes()
        if path == "MacApp/document-vendor.zip": rows += vendor(data); continue
        if len(data) > 5 * 1024 * 1024: rows.append(finding(path, "unexpected-large-file"))
        rows += scan_bytes(path, data)
    return rows, count


def history():
    rows, count, seen = [], 0, set()
    for line in git("log", "--all", "--format=%H%x00%ae%x00%ce").decode().splitlines():
        sha, author, committer = line.split("\0")
        for email in (author, committer):
            address = email.encode().lower()
            if address.endswith(ALLOWED_EMAIL_SUFFIX) or address in PUBLIC_EMAILS: continue
            rows.append(finding("commit:" + sha, "commit-email", classification="ACKNOWLEDGED_HISTORY" if sha in LEGACY_COMMITS else "FAIL"))
    for line in git("rev-list", "--objects", "--all").decode().splitlines():
        sha, _, path = line.partition(" ")
        if not path or sha in seen or git("cat-file", "-t", sha).strip() != b"blob": continue
        seen.add(sha); count += 1
        data = git("cat-file", "blob", sha)
        found = vendor(data) if path == "MacApp/document-vendor.zip" else scan_bytes(path, data)
        for row in found:
            row["object"] = sha
        rows += found
    return rows, count


def artifact(path):
    """Scan full extracted payload or ZIP; EXE/DMG require extraction/mounting."""
    rows, count = [], 0
    if path.is_dir():
        entries = ((str(p.relative_to(path)), p.read_bytes()) for p in path.rglob("*") if p.is_file() and not p.is_symlink())
    elif path.suffix.lower() == ".zip":
        archive = zipfile.ZipFile(path, metadata_encoding="utf-8")
        if archive.testzip(): raise ValueError("Corrupt ZIP")
        entries = ((n, archive.read(n)) for n in archive.namelist() if not n.endswith("/"))
    else:
        raise ValueError("Extract/mount EXE or DMG before content scanning; file existence is insufficient")
    inventory = []
    for name, contents in entries:
        count += 1
        parts = PurePosixPath(name).parts
        if PurePosixPath(name).is_absolute() or ".." in parts: rows.append(finding(name, "archive-path-traversal"))
        public_ca = (name.endswith("/pip/_vendor/certifi/cacert.pem") and
                     hashlib.sha256(contents).hexdigest() == PUBLIC_CA_SHA256)
        third_party = any(p in {"runtime", "Licenses"} for p in parts)
        library_suffix = "/Lib/site-packages/"
        windows_library = (name.split(library_suffix,1)[1] if library_suffix in name else None)
        known_webview = (third_party and windows_library is not None and windows_library.startswith("webview/") and
                         WINDOWS_LIBRARY_FILES.get(windows_library)==hashlib.sha256(contents).hexdigest())
        if risky_path(name) and not public_ca and not known_webview:
            rows.append(finding(name, "private-generated-path"))
        rows += scan_bytes(name, contents, third_party=third_party)
        inventory.append({"path": name, "sha256": hashlib.sha256(contents).hexdigest(), "bytes": len(contents)})
    if path.is_file(): archive.close()
    return rows, count, inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.artifact:
        rows, count, inventory = artifact(args.artifact)
    elif args.history:
        rows, count = history(); inventory = []
    else:
        rows, count = files(args.staged); inventory = []
    failed = [r for r in rows if r["classification"] == "FAIL"]
    report = {"ok": not failed, "scannedFilesOrObjects": count, "failureCount": len(failed), "findings": rows, "inventory": inventory}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": not failed, "scannedFilesOrObjects": count, "failureCount": len(failed), "failures": failed}, ensure_ascii=False))
    return bool(failed)


if __name__ == "__main__": sys.exit(main())
