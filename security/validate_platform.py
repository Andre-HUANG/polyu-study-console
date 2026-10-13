"""Validate archives and actual platform evidence before CI uploads public candidates."""
import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"security"))
from release_build import VERSION, NAME, CONSOLE_FILES, ASSETS, CONSOLE, verify_bundle
from privacy_scan import artifact
from release_guard import source_sha
from release_build import source_hashes


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def evidence(name):
    value=json.loads((ROOT/"验证"/name).read_text(encoding="utf-8"))
    if not value.get("ok") and name not in {"mac-smoke-evidence.json"}:raise RuntimeError("Native evidence failed: "+name)
    return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument("platform",choices=["mac","windows"]);args=parser.parse_args()
    head=source_sha()
    hashes=source_hashes()
    folder=ROOT/("Mac发布" if args.platform=="mac" else "Win发布")
    suffixes=["-arm64.dmg","-arm64.zip"] if args.platform=="mac" else ["-windows-x64-setup.exe","-windows-x64.zip"]
    files=[folder/("PolyUStudyConsole-"+VERSION+suffix) for suffix in suffixes]
    for file in files:
        if not file.is_file() or file.stat().st_size<1000000:raise RuntimeError("Missing/substantially incomplete archive")
    zipped=next(f for f in files if f.suffix==".zip")
    rows,count,inventory=artifact(zipped)
    failures=[r for r in rows if r["classification"]=="FAIL"]
    if failures:raise RuntimeError("Archive privacy gate failed: "+json.dumps(failures))
    with zipfile.ZipFile(zipped,metadata_encoding="utf-8") as archive:
        prefix=NAME+".app/Contents/Resources/console/" if args.platform=="mac" else "PolyUStudyConsole-"+VERSION+"-windows-x64/console/"
        names={n[len(prefix):] for n in archive.namelist() if n.startswith(prefix) and not n.endswith("/")}
        if names!=set(CONSOLE_FILES)|{"assets/"+n for n in ASSETS}:raise RuntimeError("Packaged console allowlist mismatch")
        for name in CONSOLE_FILES:
            if archive.read(prefix+name)!=(CONSOLE/name).read_bytes():raise RuntimeError("Packaged source differs: "+name)
        for name in ASSETS:
            if archive.read(prefix+"assets/"+name)!=(CONSOLE/"assets"/name).read_bytes():raise RuntimeError("Official asset differs")
        if args.platform=="mac":
            info=plistlib.loads(archive.read(NAME+".app/Contents/Info.plist"))
            assert info["CFBundleShortVersionString"]==VERSION and info["LSMinimumSystemVersion"]=="13.0"
        else:
            bundle=prefix.removesuffix("console/")
            manifest=json.loads(archive.read(bundle+"payload-sha256.json"))
            assert json.loads(archive.read(bundle+"build-evidence.json"))["sourceHashes"]==hashes
            packaged_files={n[len(bundle):] for n in archive.namelist() if n.startswith(bundle) and not n.endswith("/")}
            if packaged_files!=set(manifest)|{"payload-sha256.json"}:raise RuntimeError("Windows payload inventory differs")
            manifest_digest=hashlib.sha256(archive.read(bundle+"payload-sha256.json")).hexdigest()
            for name,digest in manifest.items():
                if hashlib.sha256(archive.read(bundle+name)).hexdigest()!=digest:raise RuntimeError("Payload digest mismatch")
    if args.platform=="mac":
        if sys.platform!="darwin":raise RuntimeError("Mac content/native validation needs macOS")
        smoke=evidence("mac-smoke-evidence.json");gui=evidence("native-gui-evidence.json")
        assert smoke["sourceHashes"]==hashes and gui["sourceHashes"]==hashes,"Native evidence is stale"
        assert smoke["loopbackEmptyFramework"] and smoke["appAndReaderArchitecture"]=="arm64"
        assert gui["productionNativeLifecycleValidated"] and gui["productionNativeAppLaunched"]
        with tempfile.TemporaryDirectory(prefix="polyu-dmg-audit-") as temporary:
            mount=Path(temporary)/"volume";mount.mkdir()
            subprocess.run(["hdiutil","attach","-readonly","-nobrowse","-mountpoint",str(mount),str(files[0])],check=True,capture_output=True)
            try:
                app=mount/(NAME+".app")
                subprocess.run(["codesign","--verify","--deep","--strict",str(app)],check=True,capture_output=True)
                assert sha(app/"Contents/MacOS/PolyUConsole")==gui["candidateExecutableSHA256"]
                verify_bundle(app/"Contents/Resources")
                found,_,_=artifact(app)
                assert not [r for r in found if r["classification"]=="FAIL"]
                for name in CONSOLE_FILES:assert (app/"Contents/Resources/console"/name).read_bytes()==(CONSOLE/name).read_bytes()
            finally:subprocess.run(["hdiutil","detach",str(mount)],check=True,capture_output=True)
        update=evidence("mac-update-evidence.json")
        assert update["sourceHashes"]==hashes and len(update["scenarios"])==5
        platform_checks={"nativeUpdates":True,"nativeGUI":True,"backendRuntime":True,"dmgReadOnlyScan":True,
                         "adHocSigned":True,"appleNotarized":False}
    else:
        if sys.platform!="win32":raise RuntimeError("Windows release validation requires Windows")
        native=evidence("windows-native-evidence.json");gui=evidence("windows-gui-evidence.json");installer=evidence("windows-installer-evidence.json")
        assert native["payloadManifestSHA256"]==manifest_digest and gui["payloadManifestSHA256"]==manifest_digest
        assert installer["payloadManifestSHA256"]==manifest_digest and installer["setupSHA256"]==sha(files[0])
        assert native["dpapiRoundTrip"] and native["ownedBackendStopped"] and gui["webview2NativeGUI"]
        assert installer["nativeInstallUninstall"] and installer["uninstallPreservesProfile"] and installer["installedGUIExecuted"] and installer["installedInventoryVerified"]
        update=evidence("windows-update-evidence.json")
        assert update["sourceHashes"]==hashes and update["payloadManifestSHA256"]==manifest_digest and len(update["scenarios"])==6
        platform_checks={"nativeUpdates":True,"nativeGUI":True,"backendRuntime":True,"dpapi":True,"installUninstall":True,"authenticodeSigned":False}
    report={"ok":True,"platform":args.platform,"version":VERSION,"sourceSHA":head,"sourceTreeClean":True,
            "artifacts":{f.name:{"sha256":sha(f),"bytes":f.stat().st_size} for f in files},
            "fileCount":count,"archivePrivacyScan":True,"platformChecks":platform_checks,"realCanvasValidated":False}
    output=ROOT/".security-local"/(args.platform+"-validation.json");output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":main()
