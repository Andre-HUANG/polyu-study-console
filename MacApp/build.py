#!/usr/bin/env python3
"""Standalone Apple Silicon Mac bundle. Excludes all personal profile data."""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import platform
import tempfile
import zipfile as zip_module
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent / "document-vendor"))
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prepare_updater import prepare as prepare_updater, LOCK as UPDATER_LOCK
from release_build import CONSOLE_FILES, ASSETS, NAME, VERSION, BUILD, verify_bundle, source_hashes

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "MacApp"
RELEASE = ROOT / "Mac发布"
BUILD_ROOT = Path(tempfile.gettempdir()) / ("PolyUStudyConsole-" + VERSION + "-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16] + "-macos")
APP = BUILD_ROOT / (NAME + ".app")
CONTENTS = APP / "Contents"
RESOURCES = CONTENTS / "Resources"
WORK = BUILD_ROOT / "work"
RUNTIMES = {
    "arm64": "10cab8f6ed6202fdd81637aa6eda4af8d5b7eaa8fc42f9df3c6bea4923de0d93",
}


def run(*args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def main():
    if sys.platform != "darwin": raise SystemExit("Mac 安装包必须在 macOS 上构建")
    if BUILD_ROOT.is_symlink(): raise SystemExit("构建暂存目录不能是链接")
    RELEASE.mkdir(exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    if APP.exists():
        shutil.rmtree(APP)
    (CONTENTS / "MacOS").mkdir(parents=True)
    RESOURCES.mkdir()
    for arch, digest in RUNTIMES.items():
        archive = SOURCE / "runtime-cache" / f"python-{arch}.tar.gz"
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"Python {arch} 下载校验不通过，请重新下载。")
        destination = RESOURCES / "runtime" / arch
        destination.mkdir(parents=True)
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                if not member.name.startswith("python/"):
                    raise RuntimeError("Unexpected runtime archive path")
                member.name = member.name[len("python/"):]
                if not member.name:
                    continue
                # The official archive includes python3 -> python3.12 symlinks.
                bundle.extract(member, destination, filter="data")
        for disposable in (destination / "include", destination / "share", destination / "lib/python3.12/test",
                           destination / "lib/python3.12/idlelib", destination / "lib/python3.12/ensurepip"):
            if disposable.exists():
                shutil.rmtree(disposable)
        for cache in list(destination.rglob("__pycache__")):
            shutil.rmtree(cache)
        vendor_lock = json.loads((SOURCE / "document-vendor-lock.json").read_text())
        site_packages = destination / "lib/python3.12/site-packages"
        for relative, expected in vendor_lock["files"].items():
            source = SOURCE / "document-vendor" / relative
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise RuntimeError("Document dependency checksum mismatch: " + relative)
            target = site_packages / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    console = RESOURCES / "console"
    console.mkdir()
    for name in CONSOLE_FILES:
        shutil.copy2(ROOT / "学习控制台" / name, console / name)
    assets = console / "assets"
    assets.mkdir()
    for name in ASSETS:
        shutil.copy2(ROOT / "学习控制台/assets" / name, assets / name)
    licenses = RESOURCES / "Licenses"
    licenses.mkdir()
    shutil.copy2(SOURCE / "文档识别来源.md", licenses / "Document-reader-sources.md")
    shutil.copy2(SOURCE / "document-vendor-lock.json", licenses / "Document-dependency-lock.json")
    (licenses / "Runtime-source.txt").write_text(
        "CPython 3.12.15, python-build-standalone 20261001\n"
        "https://github.com/astral-sh/python-build-standalone/releases/tag/20261001\n"
        "Runtime license files are retained inside each runtime directory.\n", encoding="utf-8")
    updater_source = prepare_updater("mac")
    frameworks = CONTENTS / "Frameworks"
    frameworks.mkdir()
    shutil.copytree(updater_source / "Sparkle.framework", frameworks / "Sparkle.framework", symlinks=True)
    shutil.copy2(updater_source / "LICENSE", licenses / "Sparkle-LICENSE.txt")
    shutil.copy2(ROOT / "updater-lock.json", licenses / "Updater-dependency-lock.json")
    for arch in RUNTIMES:
        run("xcrun", "swiftc", "-parse-as-library", "-module-cache-path", WORK / "module-cache", "-O", "-file-prefix-map", str(ROOT) + "=/src/polyu-study-console", "-target", f"{arch}-apple-macos13.0", SOURCE / "App.swift", SOURCE / "CredentialVault.swift", SOURCE / "TestIsolation.swift", SOURCE / "NativeGUISmoke.swift", SOURCE / "SoftwareUpdate.swift", SOURCE / "UpdateRecovery.swift", "-F", frameworks, "-framework", "Sparkle", "-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks", "-o", WORK / f"PolyUConsole-{arch}")
    shutil.copy2(WORK / "PolyUConsole-arm64", CONTENTS / "MacOS/PolyUConsole")
    read_tools = RESOURCES / "read-tools"
    read_tools.mkdir()
    run("xcrun", "swiftc", "-module-cache-path", WORK / "module-cache", "-O", "-file-prefix-map", str(ROOT) + "=/src/polyu-study-console", "-target", "arm64-apple-macos13.0", SOURCE / "DocumentReader.swift", "-o", read_tools / "DocumentReader")
    run("xcrun", "swiftc", "-module-cache-path", WORK / "module-cache", "-O", "-file-prefix-map", str(ROOT) + "=/src/polyu-study-console", "-target", "arm64-apple-macos13.0", SOURCE / "SessionVault.swift", "-o", read_tools / "SessionVault")
    run("xcrun", "swiftc", "-module-cache-path", WORK / "module-cache", SOURCE / "Icon.swift", "-o", WORK / "make-icon")
    run(WORK / "make-icon", WORK / "AppIcon.iconset", assets / "polyu-emblem.jpg")
    with Image.open(WORK / "AppIcon.iconset/icon_512x512@2x.png") as icon:
        icon.save(RESOURCES / "AppIcon.icns", format="ICNS")
    metadata = {"CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleExecutable": "PolyUConsole",
                "CFBundleIdentifier": "local.polyu.studyconsole", "CFBundlePackageType": "APPL",
                "CFBundleVersion": BUILD, "CFBundleShortVersionString": VERSION, "CFBundleIconFile": "AppIcon",
                "LSMinimumSystemVersion": "13.0", "LSArchitecturePriority": ["arm64"],
                "LSApplicationCategoryType": "public.app-category.education",
                "NSHighResolutionCapable": True,
                "SUFeedURL": "https://github.com/Andre-HUANG/polyu-study-console/releases/latest/download/appcast-macos.xml",
                "SUPublicEDKey": UPDATER_LOCK["publicKey"], "SUEnableAutomaticChecks": True,
                "SUAutomaticallyUpdate": False, "SUScheduledCheckInterval": 86400,
                "SURequireSignedFeed": True, "SUVerifyUpdateBeforeExtraction": True,
                "SUSendProfileInfo": False,
                "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
                "NSHumanReadableCopyright": "PolyU student study console."}
    with (CONTENTS / "Info.plist").open("wb") as stream:
        plistlib.dump(metadata, stream)
    # Finder metadata copied from project files cannot be sealed by codesign.
    # Retain any quarantine attributes; remove only the two forbidden metadata types.
    for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
        subprocess.run(["xattr", "-dr", attribute, str(APP)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    # Sign leaf runtime binaries before sealing the application bundle.
    macho = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
    for file in sorted((RESOURCES / "runtime").rglob("*")):
        if file.is_file() and not file.is_symlink():
            with file.open("rb") as stream:
                header = stream.read(4)
            if header in macho:
                architectures = subprocess.check_output(["lipo", "-archs", str(file)], text=True).split()
                if "arm64" not in architectures:
                    raise RuntimeError("A bundled dependency lacks Apple Silicon support: " + str(file))
                if len(architectures) > 1:
                    thin = file.with_name(file.name + ".arm64")
                    run("lipo", file, "-thin", "arm64", "-output", thin)
                    os.replace(thin, file)
                run("codesign", "--force", "--sign", "-", file)
    # Keep only arm64, then seal nested Sparkle helpers from the inside out.
    for file in sorted(frameworks.rglob("*")):
        if file.is_file() and not file.is_symlink() and file.read_bytes()[:4] in macho:
            architectures = subprocess.check_output(["lipo", "-archs", str(file)], text=True).split()
            if "arm64" not in architectures: raise RuntimeError("Updater helper lacks arm64")
            if len(architectures)>1:
                thin=file.with_name(file.name+".arm64")
                run("lipo", file, "-thin", "arm64", "-output", thin);os.replace(thin,file)
            run("codesign", "--force", "--sign", "-", file)
    bundles=[p for p in frameworks.rglob("*") if p.is_dir() and not p.is_symlink() and p.suffix in {".app",".xpc",".framework"}]
    for bundle in sorted(bundles,key=lambda p:len(p.parts),reverse=True): run("codesign","--force","--sign","-",bundle)
    # Desktop file providers may reapply Finder flags during runtime signing.
    run("codesign", "--force", "--sign", "-", read_tools / "DocumentReader")
    run("codesign", "--force", "--sign", "-", read_tools / "SessionVault")
    subprocess.run(["xattr", "-d", "com.apple.FinderInfo", str(APP)], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)
    run("codesign", "--force", "--sign", "-", APP)
    run("codesign", "--verify", "--deep", "--strict", APP)
    package()


def package(create_archives=True):
    run("codesign", "--verify", "--deep", "--strict", APP)
    verify_bundle(RESOURCES)
    image = RELEASE / ("PolyUStudyConsole-" + VERSION + "-arm64.dmg")
    zipfile = RELEASE / ("PolyUStudyConsole-" + VERSION + "-arm64.zip")
    if create_archives:
        stage = WORK / "dmg-stage"
        if stage.exists(): shutil.rmtree(stage)
        stage.mkdir()
        shutil.copytree(APP, stage / APP.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        shutil.copy2(SOURCE / "安装与使用.txt", stage / "安装与使用.txt")
        shutil.copy2(SOURCE / "安装与使用.txt", RELEASE / "安装与使用.txt")
        # ZIP needs no disk-image service and remains available if DMG creation fails.
        if zipfile.exists(): zipfile.unlink()
        run("ditto", "--norsrc", "--noextattr", "--noqtn", "-c", "-k", "--keepParent", APP, zipfile)
        if image.exists(): image.unlink()
        run("hdiutil", "create", "-volname", NAME, "-srcfolder", stage, "-ov", "-format", "UDZO", image)
    run("hdiutil", "verify", image)
    with zip_module.ZipFile(zipfile) as archive:
        if archive.testzip() is not None: raise RuntimeError("ZIP 校验失败")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    zip_digest = hashlib.sha256(zipfile.read_bytes()).hexdigest()
    (RELEASE / "SHA256.txt").write_text(f"{digest}  {image.name}\n{zip_digest}  {zipfile.name}\n", encoding="utf-8")
    evidence = {"at": datetime.now(timezone.utc).isoformat(), "version": VERSION, "build": BUILD,
                "status": "candidate", "platform": platform.platform(), "buildPython": sys.version,
                "sdk": subprocess.check_output(["xcrun", "--show-sdk-version"], text=True).strip(),
                "sourceHashes": source_hashes(), "codesignDeepStrict": True, "dmgVerified": True, "zipVerified": True,
                "signatureType": "ad-hoc", "developerIDSigned": False,
                "appleNotarized": False, "guiLoginValidated": False, "realCanvasValidated": False,
                "productionNativeAppLaunched": False, "productionNativeLifecycleValidated": False,
                "windowsNativeValidated": False,
                "artifacts": {image.name: digest, zipfile.name: zip_digest},
                "artifactBytes": {image.name: image.stat().st_size, zipfile.name: zipfile.stat().st_size}}
    binary_digest = hashlib.sha256((CONTENTS / "MacOS/PolyUConsole").read_bytes()).hexdigest()
    evidence["candidateExecutableSHA256"] = binary_digest
    gui_file = ROOT / "验证/native-gui-evidence.json"
    if gui_file.is_file():
        gui = json.loads(gui_file.read_text(encoding="utf-8"))
        gui_inputs = ["MacApp/CredentialVault.swift", "学习控制台/assets/remember-login.js", "MacApp/App.swift", "MacApp/TestIsolation.swift", "MacApp/NativeGUISmoke.swift"]
        gui_inputs += ["学习控制台/" + name for name in CONSOLE_FILES]
        matches = all(gui.get("sourceHashes", {}).get(name) == evidence["sourceHashes"][name] for name in gui_inputs)
        if gui.get("ok") and matches and gui.get("candidateExecutableSHA256") == binary_digest:
            evidence["productionNativeAppLaunched"] = True
            evidence["productionNativeLifecycleValidated"] = True
            evidence["nativeGUIEvidence"] = "验证/native-gui-evidence.json"
            evidence["nativeGUIRuns"] = len(gui["runs"])
    (RELEASE / "build-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成：{image}\n完成：{APP}")


if __name__ == "__main__":
    if "--verify-release" in sys.argv:
        package(create_archives=False)
    elif "--package-only" in sys.argv:
        package()
    else:
        main()
