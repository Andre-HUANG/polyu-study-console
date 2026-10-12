"""Shared release identity and a closed bundle file list for both platforms."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONSOLE = ROOT / "学习控制台"
RELEASE = json.loads((CONSOLE / "release.json").read_text(encoding="utf-8"))
NAME, VERSION, BUILD = RELEASE["name"], RELEASE["version"], RELEASE["build"]
CONSOLE_FILES = (
    "release.json", "app_config.py", "platform_support.py", "server.py", "local_access.py", "session_store.py", "grades.py", "assessment_types.py", "diagnostic_log.py",
    "native_canvas.py", "canvas_api.py", "canvas_sync.py", "canvas_web.py",
    "canvas_archive.py", "canvas_grading.py", "canvas_documents.py", "document_text.py",
    "pdf_layout.py", "windows_document.py", "assessment_reader.py", "course_evidence.py",
    "document_deadlines.py", "document_search.py", "freshness.py", "change_detection.py", "sync_support.py", "index.html", "app.js", "styles.css",
)
ASSETS = ("polyu-logo.png", "polyu-favicon.ico", "polyu-emblem.jpg", "SOURCES.txt")
FORBIDDEN = {".jiujiastudy", "救驾", "coach-tools", "plans", "reports", "grading_schemes.json",
    "canvas-course-grades.json", "canvas-grading-settings.json", "canvas-cookies.json", "login.json",
    "config.json", "state.json", "snapshot.json", "documents", "image-ocr", "managed-deadlines.json", "diagnostics", "events.json", "app.log", "backend.log"}


def verify_bundle(root):
    unexpected = [str(p.relative_to(root)) for p in root.rglob("*") if p.name in FORBIDDEN]
    if unexpected: raise RuntimeError("安装包含有不允许的数据路径：" + repr(unexpected))


def source_hashes():
    paths = [CONSOLE / n for n in CONSOLE_FILES] + [CONSOLE / "assets" / n for n in ASSETS]
    paths += [CONSOLE / "使用说明.md"]
    paths += [ROOT / "MacApp" / n for n in ("App.swift", "SessionVault.swift", "TestIsolation.swift", "NativeGUISmoke.swift", "DocumentReader.swift", "Icon.swift", "build.py", "test_app.py", "document-vendor-lock.json", "安装与使用.txt")]
    paths += sorted((ROOT / "WindowsApp").glob("*.py"))
    paths += [ROOT / "build.py", ROOT / "release_build.py", ROOT / "WindowsApp/requirements.txt",
              ROOT / "WindowsApp/build.cmd", ROOT / "WindowsApp/PolyU.spec", ROOT / "WindowsApp/app.manifest",
              ROOT / "WindowsApp/runtime-lock.json", ROOT / "WindowsApp/build-tools-lock.json",
              ROOT / "WindowsApp/launcher.nsi", ROOT / "WindowsApp/installer.nsi",
              ROOT / "WindowsApp/安装与使用.txt", ROOT / "WindowsApp/原生构建与验收.md"]
    paths += [ROOT / "验证" / n for n in ("run_offline.py", "IsolationTests.swift", "run_native_gui.py", "verify_mac_bundle.py", "verify_release_artifacts.py", "benchmark.py", "run_gui_component.py", "NativeDashboardSmoke.swift")]
    paths += sorted((ROOT / "security").glob("*.py")) + sorted((ROOT / ".github/workflows").glob("*.yml"))
    paths += [ROOT / "验证/verify_local_api.py", ROOT / "验证/verify_keychain.py"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
