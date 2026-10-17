"""Shared release identity and a closed bundle file list for both platforms."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONSOLE = ROOT / "学习控制台"
RELEASE = json.loads((CONSOLE / "release.json").read_text(encoding="utf-8"))
NAME, VERSION, BUILD = RELEASE["name"], RELEASE["version"], RELEASE["build"]
BASELINE_VERSION, BASELINE_BUILD = RELEASE["baselineVersion"], RELEASE["baselineBuild"]
UPDATE_DESCRIPTION = RELEASE["updateDescription"]
if any(not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) for value in (VERSION, BASELINE_VERSION)):
    raise RuntimeError("Release and baseline versions must be numeric semantic versions")
if any(not isinstance(value, str) or not value.isascii() or not value.isdecimal() for value in (BUILD, BASELINE_BUILD)):
    raise RuntimeError("Release and baseline build numbers must be decimal strings")
if tuple(map(int, VERSION.split("."))) <= tuple(map(int, BASELINE_VERSION.split("."))) or int(BUILD) <= int(BASELINE_BUILD):
    raise RuntimeError("Release identity must advance beyond its acceptance baseline")
if not isinstance(UPDATE_DESCRIPTION, str) or not UPDATE_DESCRIPTION.strip():
    raise RuntimeError("Release update description is required")
CONSOLE_FILES = (
    "release.json", "app_config.py", "platform_support.py", "server.py", "local_access.py", "runtime_cache.py", "timetable.py", "timetable_reader.py", "academic-ui.js", "academic_calendar.py", "academic-calendar.json", "session_store.py", "grades.py", "assessment_types.py", "diagnostic_log.py",
    "native_canvas.py", "canvas_api.py", "canvas_sync.py", "canvas_web.py",
    "canvas_archive.py", "canvas_grading.py", "canvas_documents.py", "document_text.py",
    "pdf_layout.py", "windows_document.py", "assessment_reader.py", "course_evidence.py",
    "document_deadlines.py", "document_search.py", "freshness.py", "change_detection.py", "sync_support.py", "index.html", "app.js", "styles.css",
)
ASSETS = ("polyu-logo.png", "polyu-favicon.ico", "polyu-emblem.jpg", "SOURCES.txt", "remember-login.js")
FORBIDDEN = {".jiujiastudy", "救驾", "coach-tools", "plans", "reports", "grading_schemes.json",
    "canvas-course-grades.json", "canvas-grading-settings.json", "canvas-cookies.json", "login.json",
    "credentials", "consent.json", "protected.bin", "config.json", "state.json", "snapshot.json", "documents", "image-ocr", "managed-deadlines.json", "diagnostics", "events.json", "app.log", "backend.log"}


def verify_bundle(root):
    unexpected = [str(p.relative_to(root)) for p in root.rglob("*") if p.name in FORBIDDEN]
    if unexpected: raise RuntimeError("安装包含有不允许的数据路径：" + repr(unexpected))


def source_hashes():
    paths = [CONSOLE / n for n in CONSOLE_FILES] + [CONSOLE / "assets" / n for n in ASSETS]
    paths += [CONSOLE / "使用说明.md"]
    paths += [ROOT / "MacApp" / n for n in ("App.swift", "CredentialVault.swift", "SessionVault.swift", "TestIsolation.swift", "NativeGUISmoke.swift", "DocumentReader.swift", "Icon.swift", "build.py", "test_app.py", "document-vendor-lock.json", "安装与使用.txt")]
    paths += sorted((ROOT / "WindowsApp").glob("*.py"))
    paths += [ROOT / "updater-lock.json", ROOT / "prepare_updater.py", ROOT / "release_updates.py", ROOT / "MacApp/SoftwareUpdate.swift", ROOT / "MacApp/UpdateRecovery.swift"]
    paths += [ROOT / "build.py", ROOT / "release_build.py", ROOT / "WindowsApp/requirements.txt",
              ROOT / "WindowsApp/build.cmd", ROOT / "WindowsApp/PolyU.spec", ROOT / "WindowsApp/app.manifest",
              ROOT / "WindowsApp/runtime-lock.json", ROOT / "WindowsApp/build-tools-lock.json",
              ROOT / "WindowsApp/launcher.nsi", ROOT / "WindowsApp/installer.nsi",
              ROOT / "WindowsApp/安装与使用.txt", ROOT / "WindowsApp/原生构建与验收.md"]
    paths += [ROOT / "验证" / n for n in ("run_offline.py", "IsolationTests.swift", "run_native_gui.py", "verify_mac_bundle.py", "verify_release_artifacts.py", "benchmark.py", "measure_idle.py", "NativeUpdateSmoke.swift", "verify_mac_updates.py", "verify_windows_updates.py", "run_gui_component.py", "NativeDashboardSmoke.swift")]
    paths += [ROOT / "security/dependency-exceptions.json", ROOT / "security/tools-lock.json", ROOT / "security/signing-requirements.txt", ROOT / ".gitleaks.toml"]
    paths += sorted((ROOT / "security").glob("*.py")) + sorted((ROOT / ".github/workflows").glob("*.yml"))
    paths += [ROOT / "验证/verify_local_api.py", ROOT / "验证/verify_keychain.py"]
    paths += [ROOT / "验证" / name for name in ("verify_remember_password.py", "RememberPasswordChecks.swift", "test_remember_script.cjs", "test_v2_ui.cjs")]
    paths += sorted((ROOT / "MacApp").glob("test_*.py")) + sorted((ROOT / "WindowsApp").glob("test_*.py"))
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
