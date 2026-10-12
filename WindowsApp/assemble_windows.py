"""Assemble a real Windows payload and NSIS installer without running Windows.

This is not a PyInstaller cross-build. Upstream Windows executables and wheels
remain Windows binaries; the native POSIX NSIS compiler uses matching upstream
Windows stubs. Native execution checks stay explicitly pending.
"""
from __future__ import annotations
import argparse
import base64
import concurrent.futures
import csv
import hashlib
import io
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "WindowsApp"
CACHE = ROOT / ".build/windows-assembly"
DOWNLOADS = CACHE / "downloads"
TOOLS = CACHE / "tools"
EPOCH = 1791244800  # 2026-10-06 00:00:00 UTC
sys.path.insert(0, str(ROOT))
from release_build import ASSETS, CONSOLE, CONSOLE_FILES, NAME, VERSION, source_hashes, verify_bundle

SHELL_FILES = ("main.py", "desktop.py", "windows_process.py", "native_verify.py", "gui_smoke.py")
SYSTEM_DLLS = {"advapi32.dll", "bcrypt.dll", "bcryptprimitives.dll", "cabinet.dll", "combase.dll", "comctl32.dll", "crypt32.dll", "dwmapi.dll", "gdi32.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll", "mscoree.dll", "msi.dll", "msvcrt.dll", "netapi32.dll", "ntdll.dll", "ole32.dll", "oleaut32.dll", "pathcch.dll", "powrprof.dll", "psapi.dll", "rpcrt4.dll", "secur32.dll", "setupapi.dll", "shell32.dll", "shlwapi.dll", "user32.dll", "userenv.dll", "version.dll", "winmm.dll", "ws2_32.dll", "wtsapi32.dll"}
SYSTEM_DLLS.add("propsys.dll")  # Windows Property System, used by upstream _wmi.
EXCLUDE_WHEEL_PATHS = ("webview/lib/runtimes/win-arm64/", "webview/lib/runtimes/win-x86/", "clr_loader/ffi/dlls/x86/")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_relative(name):
    selected = PurePosixPath(name)
    if selected.is_absolute() or any(part in {"..", ""} for part in selected.parts) or "\\" in name:
        raise RuntimeError("Unsafe archive member: " + name)
    return selected


def fetch(entry, enabled):
    name = entry["filename"]
    if Path(name).name != name:
        raise RuntimeError("Invalid download filename")
    target = DOWNLOADS / name
    if target.is_file() and sha256(target) == entry["sha256"]:
        return target
    if not enabled:
        raise RuntimeError("Missing/changed pinned cache file: " + name + "; use --download to retrieve it")
    partial = target.with_name(name + ".partial")
    with urllib.request.urlopen(entry["url"], timeout=120) as response, partial.open("wb") as output:
        while block := response.read(1024 * 1024):
            output.write(block)
    if sha256(partial) != entry["sha256"]:
        partial.unlink(missing_ok=True)
        raise RuntimeError("Download SHA256 mismatch: " + name)
    partial.replace(target)
    print("Verified", name, flush=True)
    return target


def extract_zip(path, destination):
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            safe_relative(member.filename)
        archive.extractall(destination)
        # NSIS embeds official plugin File timestamps as well as payload dates.
        for member in archive.infolist():
            if not member.is_dir():
                os.utime(destination / member.filename, (EPOCH, EPOCH))


def prepare_tools(lock):
    TOOLS.mkdir(parents=True, exist_ok=True)
    for entry in lock["tools"]:
        path = DOWNLOADS / entry["filename"]
        if path.suffix == ".whl":
            extract_zip(path, TOOLS / "python")
        elif path.suffix == ".zip":
            # Preserve the previously compiled POSIX binary during repeat builds.
            extract_zip(path, TOOLS)
        elif path.name.endswith(".tar.bz2"):
            with tarfile.open(path) as archive:
                archive.extractall(TOOLS, filter="data")
    sys.path.insert(0, str(TOOLS / "python"))
    compiler = TOOLS / ("nsis-3.13/makensis.exe" if sys.platform == "win32" else "nsis-3.13/makensis")
    compiler_environment = os.environ.copy()
    compiler_environment["NSISDIR"] = str(TOOLS / "nsis-3.13")
    actual = subprocess.check_output([str(compiler), "/VERSION" if sys.platform == "win32" else "-VERSION"], text=True, env=compiler_environment).strip() if compiler.is_file() else None
    if actual != "v3.13":
        environment = os.environ.copy()
        environment.update(PYTHONPATH=str(TOOLS / "python"), SOURCE_DATE_EPOCH=str(EPOCH))
        command = [sys.executable, "-B", "-m", "SCons", "-j", "4", "VERSION=3.13", "SKIPSTUBS=all", "SKIPPLUGINS=all", "SKIPUTILS=all", "SKIPMISC=all", "NSIS_CONFIG_CONST_DATA_PATH=no", "PREFIX=" + str(TOOLS / "nsis-3.13"), "CC=clang", "CXX=clang++", "install-compiler"]
        with (CACHE / "makensis-build.log").open("w") as log:
            subprocess.run(command, cwd=TOOLS / "nsis-3.13-src", env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    actual = subprocess.check_output([str(compiler), "/VERSION" if sys.platform == "win32" else "-VERSION"], text=True, env=compiler_environment).strip()
    if actual != "v3.13":
        raise RuntimeError("NSIS source/stub version mismatch: " + actual)
    return compiler


def install_runtime(lock, bundle):
    destination = bundle / "runtime"
    root_files = {"python.exe", "pythonw.exe", "python3.dll", "python312.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"}
    copied = []
    with tarfile.open(DOWNLOADS / lock["runtime"]["filename"]) as archive:
        for member in archive:
            relative = safe_relative(member.name)
            if not member.isfile() or relative.parts[0] != "python":
                continue
            parts = relative.parts[1:]
            include = len(parts) == 1 and parts[0] in root_files
            if parts and parts[0] == "DLLs":
                include = not parts[-1].startswith(("_test", "_ctypes_test", "_tkinter", "tcl", "tk"))
            elif len(parts) > 1 and parts[0] == "Lib":
                include = parts[1] not in {"site-packages", "test", "tkinter", "idlelib", "turtledemo", "ensurepip"}
            if not include or "__pycache__" in parts or parts[-1].endswith((".pyc", ".pdb")):
                continue
            path = destination.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
            copied.append(str(path.relative_to(bundle)))
    for name in root_files:
        if not (destination / name).is_file():
            raise RuntimeError("Incomplete upstream Windows runtime: " + name)
    # CPython documents that the DLL-named _pth overrides registry/env paths and
    # enables isolated mode. Site is needed for package metadata; user-site stays off.
    (destination / "python312._pth").write_text(".\nDLLs\nLib\nLib/site-packages\n../WindowsApp\n../console\nimport site\n", encoding="utf-8")
    return copied


def pure_proxy_wheel(lock):
    source = next(entry for entry in lock["sdists"] if entry["name"] == "proxy-tools")
    with tarfile.open(DOWNLOADS / source["filename"]) as archive:
        files = {"proxy_tools/__init__.py": archive.extractfile("proxy_tools-0.1.0/proxy_tools/__init__.py").read(),
                 "proxy_tools-0.1.0.dist-info/METADATA": archive.extractfile("proxy_tools-0.1.0/PKG-INFO").read(),
                 "proxy_tools-0.1.0.dist-info/licenses/README.rst": archive.extractfile("proxy_tools-0.1.0/README.rst").read(),
                 "proxy_tools-0.1.0.dist-info/licenses/LICENSE.txt": (DOWNLOADS / "proxy_tools-upstream-LICENSE.txt").read_bytes(),
                 "proxy_tools-0.1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: PolyU verified pure Python repack 1.6.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    records = io.StringIO(newline="")
    writer = csv.writer(records, lineterminator="\n")
    for name, data in sorted(files.items()):
        writer.writerow([name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode(), len(data)])
    writer.writerow(["proxy_tools-0.1.0.dist-info/RECORD", "", ""])
    files["proxy_tools-0.1.0.dist-info/RECORD"] = records.getvalue().encode()
    target = DOWNLOADS / "proxy_tools-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 6, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    entry = {"name": "proxy-tools", "version": "0.1.0", "filename": target.name, "sha256": sha256(target), "size": target.stat().st_size,
             "sourceSha256": source["sha256"], "build": "Deterministic pure-Python repack of the two upstream package/metadata files; stored ZIP with fixed POSIX metadata; no native compilation or source execution"}
    if lock.get("builtWheels") and lock["builtWheels"] != [entry]:
        raise RuntimeError("Locally built wheel no longer matches its lock")
    if not lock.get("builtWheels"):
        lock["builtWheels"] = [entry]
        (SOURCE / "runtime-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entry


def install_wheels(lock, bundle):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    site = bundle / "runtime/Lib/site-packages"
    pins = {entry["name"]: entry["version"] for entry in lock["wheels"] + lock["builtWheels"]}
    excluded, closure = [], {}
    for entry in lock["wheels"] + lock["builtWheels"]:
        with zipfile.ZipFile(DOWNLOADS / entry["filename"]) as archive:
            metadata = BytesParser().parsebytes(archive.read(next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))))
            if canonicalize_name(metadata["Name"]) != entry["name"] or metadata["Version"] != entry["version"]:
                raise RuntimeError("Wheel METADATA mismatch: " + entry["filename"])
            applicable = []
            for text in metadata.get_all("Requires-Dist", []):
                requirement = Requirement(text)
                if requirement.marker and not requirement.marker.evaluate(lock["resolvedEnvironment"]):
                    continue
                name = canonicalize_name(requirement.name)
                if name not in pins or pins[name] not in requirement.specifier:
                    raise RuntimeError("Unresolved actual Windows wheel dependency: " + text)
                applicable.append(text)
            closure[entry["name"]] = applicable
            for member in archive.infolist():
                name = member.filename
                relative = safe_relative(name)
                if member.is_dir():
                    continue
                if name.startswith(EXCLUDE_WHEEL_PATHS) or name == "webview/lib/WebBrowserInterop.x86.dll" or name.lower().endswith(".pdb"):
                    excluded.append(name)
                    continue
                parts = relative.parts
                destination = site.joinpath(*parts)
                if parts[0].endswith(".data"):
                    if parts[1] in {"purelib", "platlib"}:
                        destination = site.joinpath(*parts[2:])
                    elif parts[1] == "data":
                        destination = (bundle / "runtime").joinpath(*parts[2:])
                    elif parts[1] in {"scripts", "headers"}:
                        excluded.append(name)
                        continue
                    else:
                        raise RuntimeError("Unsupported wheel .data category: " + name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(member)
                if destination.exists() and destination.read_bytes() != data:
                    raise RuntimeError("Conflicting wheel file: " + name)
                destination.write_bytes(data)
    return {"actualWindowsMetadataClosure": closure, "excludedNonX64Files": excluded}


def preserve_licenses(lock, bundle):
    import zstandard
    licenses = bundle / "Licenses"
    destination = licenses / "Runtime"
    destination.mkdir(parents=True, exist_ok=True)
    with (DOWNLOADS / lock["runtimeLicenseArchive"]["filename"]).open("rb") as stream, zstandard.ZstdDecompressor().stream_reader(stream) as reader, tarfile.open(fileobj=reader, mode="r|") as archive:
        for member in archive:
            if member.isfile() and (member.name.startswith("python/licenses/") or member.name == "python/PYTHON.json"):
                relative = safe_relative(member.name)
                path = destination / relative.name
                path.write_bytes(archive.extractfile(member).read())
    (licenses / "NSIS").mkdir()
    shutil.copy2(TOOLS / "nsis-3.13/COPYING", licenses / "NSIS/COPYING")
    for entry in lock["licenseSources"]:
        path = DOWNLOADS / entry["filename"]
        if path.suffix in {".txt", ""}:
            shutil.copy2(path, licenses / path.name)
        elif path.suffix == ".nupkg":
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if "license" in name.lower() or "notice" in name.lower():
                        relative = safe_relative(name)
                        target = licenses / "WebView2" / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(name))
    shutil.copy2(SOURCE / "runtime-lock.json", licenses / "Runtime-dependency-lock.json")
    (licenses / "README.txt").write_text("CPython and its original runtime dependency notices are in Runtime/.\nPython wheels retain their upstream .dist-info licenses and metadata under runtime/Lib/site-packages/.\nWinRT namespace wheels share pywinrt-LICENSE. The upstream proxy_tools license omitted from its sdist is restored.\nMicrosoft runtime DLLs are retained only from the hash-pinned upstream Python/WinRT distributions.\nNSIS/COPYING covers the Windows installer/launcher stubs. Official PolyU asset sources remain console/assets/SOURCES.txt.\n", encoding="utf-8")


def audit_private_paths(bundle):
    configuration = bundle / "runtime/python312._pth"
    lines = configuration.read_text(encoding="utf-8").splitlines()
    if lines != [".", "DLLs", "Lib", "Lib/site-packages", "../WindowsApp", "../console", "import site"]:
        raise RuntimeError("Private Python path configuration changed")
    paths = [(configuration.parent / line).resolve() for line in lines if not line.startswith("import ")]
    if not all(path.is_dir() and path.is_relative_to(bundle.resolve()) for path in paths):
        raise RuntimeError("Private Python path escaped/missed the assembled layout")
    required = ["desktop", "windows_process", "native_verify", "server", "platform_support", "app_config", "pdf_layout", "webview", "pythonnet", "clr", "clr_loader", "pdfplumber", "pdfminer", "PIL", "cffi", "pycparser", "tzdata", "striprtf", "proxy_tools", "winrt.runtime", "winrt.windows.foundation", "winrt.windows.foundation.collections", "winrt.windows.storage", "winrt.windows.storage.streams", "winrt.windows.graphics.imaging", "winrt.windows.media.ocr", "winrt.windows.globalization"]
    resolution = {}
    for name in required:
        relative = Path(*name.split("."))
        candidates = [candidate for root in paths for candidate in (root / relative.with_suffix(".py"), root / relative / "__init__.py") if candidate.is_file()]
        if not candidates:
            raise RuntimeError("Required module missing from private paths: " + name)
        resolution[name] = str(candidates[0].relative_to(bundle))
    for root in (bundle / "WindowsApp", bundle / "console"):
        for path in root.glob("*.py"):
            compile(path.read_bytes(), str(path), "exec")
    return {"status": "passed_static_only", "directories": [str(path.relative_to(bundle)) for path in paths], "requiredModules": resolution, "nativeImportsValidated": False}


def audit_webview_sdk(lock, bundle):
    entry = next(entry for entry in lock["licenseSources"] if entry["filename"].endswith(".nupkg"))
    relatives = ["webview/lib/Microsoft.Web.WebView2.Core.dll", "webview/lib/Microsoft.Web.WebView2.WinForms.dll", "webview/lib/runtimes/win-x64/native/WebView2Loader.dll"]
    matches = {}
    with zipfile.ZipFile(DOWNLOADS / entry["filename"]) as archive:
        for relative in relatives:
            path = bundle / "runtime/Lib/site-packages" / relative
            digest = sha256(path)
            identical = [name for name in archive.namelist() if name.endswith("/" + path.name) and hashlib.sha256(archive.read(name)).hexdigest() == digest]
            if not identical:
                raise RuntimeError("Bundled WebView2 DLL differs from its official SDK: " + relative)
            matches[relative] = {"sha256": digest, "identicalOfficialSdkFiles": identical}
    return {"status": "passed_static_only", "officialSdkVersion": "1.0.3856.49", "officialNugetSha512": entry["sha512"], "dllMatches": matches}


def audit_pe(bundle):
    import pefile
    binaries = sorted(path for path in bundle.rglob("*") if path.suffix.lower() in {".exe", ".dll", ".pyd"})
    names = {path.name.lower() for path in binaries}
    records, unresolved, layout = [], [], []
    for path in binaries:
        pe = pefile.PE(str(path), fast_load=False)
        managed = pe.OPTIONAL_HEADER.DATA_DIRECTORY[14]
        flags = struct.unpack("<I", pe.get_data(managed.VirtualAddress + 16, 4))[0] if managed.VirtualAddress else 0
        any_cpu = bool(flags & 1 and not flags & 2 and not flags & 0x20000)
        bootstrap = path.name == "PolyUStudyConsole.exe"
        if pe.FILE_HEADER.Machine != 0x8664 and not (pe.FILE_HEADER.Machine == 0x14c and (any_cpu or bootstrap)):
            raise RuntimeError("Non-x64 native payload: " + str(path.relative_to(bundle)))
        dependencies = sorted({entry.dll.decode("ascii").lower() for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])})
        missing = [name for name in dependencies if name not in names and name not in SYSTEM_DLLS and not name.startswith(("api-ms-", "ext-ms-"))]
        unresolved.extend({"file": str(path.relative_to(bundle)), "dll": name} for name in missing)
        # CPython 3.12's extension loader searches DLL_LOAD_DIR and DEFAULT_DIRS:
        # the extension/DLL directory and the application executable directory.
        # Check these concrete locations rather than accepting a same-named file
        # elsewhere in the bundle. Managed assemblies are loaded by explicit path.
        local_dirs = [path.parent, bundle / "runtime"]
        scoped = {}
        for name in dependencies:
            if name in SYSTEM_DLLS or name.startswith(("api-ms-", "ext-ms-")):
                continue
            targets = [candidate for folder in local_dirs for candidate in folder.iterdir()
                       if candidate.is_file() and candidate.name.lower() == name]
            if not targets:
                raise RuntimeError("Static DLL exists outside its actual loader directories: " + str(path.relative_to(bundle)) + ": " + name)
            scoped[name] = str(targets[0].relative_to(bundle))
        layout.append({"file": str(path.relative_to(bundle)), "searchedBundledDirectories": [str(folder.relative_to(bundle)) for folder in local_dirs], "resolvedBundledImports": scoped})
        records.append({"file": str(path.relative_to(bundle)), "machine": hex(pe.FILE_HEADER.Machine), "architecture": "AnyCPU managed IL" if any_cpu and pe.FILE_HEADER.Machine == 0x14c else "x86 bootstrap" if bootstrap else "x64", "subsystem": pe.OPTIONAL_HEADER.Subsystem, "clrFlags": hex(flags), "imports": dependencies, "sha256": sha256(path)})
    if unresolved:
        raise RuntimeError("Unresolved static DLL imports: " + json.dumps(unresolved))
    if any(path.suffix in {".dylib", ".so"} for path in bundle.rglob("*")):
        raise RuntimeError("Host binaries leaked into Windows payload")
    executables = {record["file"]: record for record in records}
    if executables["runtime/python.exe"]["subsystem"] != 3 or executables["runtime/pythonw.exe"]["subsystem"] != 2:
        raise RuntimeError("Worker and GUI Python executable subsystem mismatch")
    return {"status": "passed_static_only", "nativePayloadArchitecture": "x64", "bootstrapArchitecture": "x86", "managedAnyCPUAllowed": True, "unresolvedDllImports": unresolved, "binaryCount": len(records), "binaries": records}, {"status": "passed_static_only", "nativeDllLoadValidated": False, "resolution": layout, "explicitLoadPaths": {"clr_loader": "runtime/Lib/site-packages/clr_loader/ffi/dlls/amd64/ClrLoader.dll", "pythonnet": "runtime/Lib/site-packages/pythonnet/runtime/Python.Runtime.dll", "pywebviewManaged": ["runtime/Lib/site-packages/webview/lib/Microsoft.Web.WebView2.Core.dll", "runtime/Lib/site-packages/webview/lib/Microsoft.Web.WebView2.WinForms.dll"], "pywebviewNativePath": "runtime/Lib/site-packages/webview/lib/runtimes/win-x64/native", "winrt": "runtime/Lib/site-packages/winrt (all WinRT PYD and upstream msvcp140.dll together)"}, "upstreamBasis": "CPython v3.12.15 dynload_win.c LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|DEFAULT_DIRS; exact pinned clr_loader/pythonnet/pywebview/winrt wheel source"}


def compile_nsis(compiler, script, defines, log_name):
    environment = os.environ.copy()
    environment.update(NSISDIR=str(TOOLS / "nsis-3.13"), SOURCE_DATE_EPOCH=str(EPOCH))
    prefix = "/" if sys.platform == "win32" else "-"
    command = [str(compiler), prefix + "NOCD", prefix + "V3", *[f"{prefix}D{name}={value}" for name, value in defines.items()], str(script)]
    with (CACHE / log_name).open("w") as log:
        subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)


def nsis_quote(value):
    return str(value).replace("$", "$$").replace('"', '$\\"')


def installer_payload(bundle):
    install, uninstall, directories = [], [], set()
    files = sorted(path for path in bundle.rglob("*") if path.is_file())
    previous = None
    for path in files:
        relative = path.relative_to(bundle)
        folder = relative.parent
        if folder != previous:
            install.append('SetOutPath "$INSTDIR' + ("\\" + nsis_quote(str(folder).replace("/", "\\")) if folder != Path(".") else "") + '"')
            previous = folder
        install.append('File "' + nsis_quote(path) + '"')
        uninstall.append('Delete "$INSTDIR\\' + nsis_quote(str(relative).replace("/", "\\")) + '"')
        for parent in relative.parents:
            if parent != Path("."):
                directories.add(parent)
    for folder in sorted(directories, key=lambda value: (-len(value.parts), str(value))):
        uninstall.append('RMDir "$INSTDIR\\' + nsis_quote(str(folder).replace("/", "\\")) + '"')
    install_path, uninstall_path = CACHE / "payload-install.nsh", CACHE / "payload-uninstall.nsh"
    install_path.write_text("\n".join(install) + "\n", encoding="utf-8")
    uninstall_path.write_text("\n".join(uninstall) + "\n", encoding="utf-8")
    return install_path, uninstall_path


def deterministic_zip(bundle, output):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(path for path in bundle.rglob("*") if path.is_file()):
            info = zipfile.ZipInfo(str(path.relative_to(bundle.parent)).replace("\\", "/"), (2026, 10, 6, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="retrieve missing files from the official URLs in the SHA256 locks")
    arguments = parser.parse_args()
    if VERSION != "1.8.1":
        raise SystemExit("This assembly script is locked to 1.8.1")
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    lock = json.loads((SOURCE / "runtime-lock.json").read_text(encoding="utf-8"))
    tools = json.loads((SOURCE / "build-tools-lock.json").read_text(encoding="utf-8"))
    if sys.platform == "win32":
        tools["tools"] = [entry for entry in tools["tools"] if "macosx" not in entry["filename"]] + tools["windowsTools"]
    entries = [lock["runtime"], lock["runtimeLicenseArchive"], *lock["wheels"], *lock["sdists"], *lock["licenseSources"], *tools["tools"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda entry: fetch(entry, arguments.download), entries))
    compiler = prepare_tools(tools)
    bundle = CACHE / ("PolyUStudyConsole-" + VERSION + "-windows-x64")
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir()
    install_runtime(lock, bundle)
    pure_proxy_wheel(lock)
    dependency_audit = install_wheels(lock, bundle)
    console = bundle / "console"
    console.mkdir()
    for name in CONSOLE_FILES:
        shutil.copy2(CONSOLE / name, console / name)
    (console / "assets").mkdir()
    for name in ASSETS:
        shutil.copy2(CONSOLE / "assets" / name, console / "assets" / name)
    (bundle / "WindowsApp").mkdir()
    for name in SHELL_FILES:
        shutil.copy2(SOURCE / name, bundle / "WindowsApp" / name)
    preserve_licenses(lock, bundle)
    private_paths = audit_private_paths(bundle)
    sdk_audit = audit_webview_sdk(lock, bundle)
    for name in ("安装与使用.txt", "原生构建与验收.md"):
        shutil.copy2(SOURCE / name, bundle / name)
    (bundle / "Windows原生自检.cmd").write_text('@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n"runtime\\python.exe" -I -B -X utf8 "WindowsApp\\main.py" --platform-check\r\nif errorlevel 1 echo Native check failed. See the error above.\r\npause\r\n', encoding="utf-8", newline="")
    icon = console / "assets/polyu-favicon.ico"
    compile_nsis(compiler, SOURCE / "launcher.nsi", {"LAUNCHER_OUT": bundle / "PolyUStudyConsole.exe", "APP_ICON": icon}, "launcher-build.log")
    verify_bundle(bundle)
    if {path.name for path in console.iterdir() if path.is_file()} != set(CONSOLE_FILES):
        raise RuntimeError("Console file allowlist mismatch")
    if {path.name for path in (console / "assets").iterdir()} != set(ASSETS):
        raise RuntimeError("Official asset allowlist mismatch")
    pe_audit, dll_layout = audit_pe(bundle)
    evidence = {"version": VERSION, "releaseStatus": "candidate", "buildMethod": "Hash-verified Windows CPython/PyPI binary assembly with NSIS 3.13 and matching official stubs", "builderPlatform": platform.platform(), "builderPython": sys.version,
                "runtime": lock["runtime"], "runtimeVersion": "3.12.15+20261001", "runtimeTarget": lock["target"], "sourceHashes": source_hashes(), "packages": {entry["name"]: entry["version"] for entry in lock["wheels"] + lock["builtWheels"]},
                "downloadLocks": {"runtimeLockSha256": sha256(SOURCE / "runtime-lock.json"), "buildToolsLockSha256": sha256(SOURCE / "build-tools-lock.json")},
                "consoleAllowlist": list(CONSOLE_FILES), "assetAllowlist": list(ASSETS), "shellAllowlist": list(SHELL_FILES), "dependencyAudit": dependency_audit, "staticPEAudit": pe_audit, "staticDllLayoutAudit": dll_layout, "staticPrivatePathAudit": private_paths, "staticWebViewSdkAudit": sdk_audit,
                "pythonIsolation": {"configuration": "runtime/python312._pth", "arguments": ["-I", "-B", "-X", "utf8"], "guiExecutable": "runtime/pythonw.exe", "workerExecutable": "runtime/python.exe", "nativeValidated": False},
                "nativeSmoke": {"status": "not_run", "ok": None, "reason": "Assembly performs static checks; subsequent native acceptance is recorded separately."}, "installerNativeValidated": False, "guiLoginValidated": False, "realCanvasValidated": False, "authenticodeSigned": False,
                "externalSystemPrerequisites": ["Windows 10 build 19041 or newer, native AMD/Intel x64", ".NET Framework 4.8 or newer", "Microsoft Edge WebView2 Runtime", "Windows OCR language support"],
                "installerScope": "Current user; private Programs/PolyUStudyConsole directory; uninstall deletes exact package file allowlist and HKCU registration, preserves separate account profile."}
    (bundle / "build-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {path.relative_to(bundle).as_posix(): sha256(path) for path in sorted(bundle.rglob("*")) if path.is_file()}
    (bundle / "payload-sha256.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # NSIS stores each File's mtime even with SOURCE_DATE_EPOCH. Normalize only
    # the newly assembled payload, leaving original source/cache files untouched.
    for path in bundle.rglob("*"):
        if path.is_file():
            os.utime(path, (EPOCH, EPOCH))
    install, uninstall = installer_payload(bundle)
    output = ROOT / "Win发布"
    output.mkdir(exist_ok=True)
    installer = output / ("PolyUStudyConsole-" + VERSION + "-windows-x64-setup.exe")
    compile_nsis(compiler, SOURCE / "installer.nsi", {"INSTALLER_OUT": installer, "APP_ICON": icon, "PAYLOAD_INSTALL": install, "PAYLOAD_UNINSTALL": uninstall}, "installer-build.log")
    import pefile
    setup = pefile.PE(str(installer))
    if setup.FILE_HEADER.Machine != 0x14c or setup.OPTIONAL_HEADER.Subsystem != 2:
        raise RuntimeError("Unexpected NSIS Windows GUI bootstrap architecture")
    archive = output / ("PolyUStudyConsole-" + VERSION + "-windows-x64.zip")
    deterministic_zip(bundle, archive)
    evidence["artifacts"] = {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in (archive, installer)}
    evidence["payloadFileCount"] = len(manifest) + 1
    evidence["payloadBytes"] = sum(path.stat().st_size for path in bundle.rglob("*") if path.is_file())
    evidence["installerPE"] = {"machine": "0x14c", "architecture": "x86 Unicode NSIS bootstrap installing native x64 payload", "subsystem": 2}
    (output / "build-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "SHA256.txt").write_text("".join(sha256(path) + "  " + path.name + "\n" for path in (archive, installer)), encoding="utf-8")
    print(json.dumps({"status": "candidate_built", "artifacts": evidence["artifacts"], "payloadFiles": evidence["payloadFileCount"], "payloadBytes": evidence["payloadBytes"], "nativeSmoke": evidence["nativeSmoke"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
