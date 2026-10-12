"""Install/uninstall only on a disposable GitHub runner; never a real user profile."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent


def main():
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Installer acceptance requires a disposable Windows CI runner")
    sys.path.insert(0,str(ROOT))
    from release_build import VERSION, NAME
    setup=ROOT/"Win发布"/("PolyUStudyConsole-"+VERSION+"-windows-x64-setup.exe")
    install=Path(os.environ["LOCALAPPDATA"])/"Programs/PolyUStudyConsole"
    profile=Path(os.environ["LOCALAPPDATA"])/NAME
    if install.exists() or profile.exists(): raise RuntimeError("Refuse to overwrite an existing installation/profile")
    manifest=json.loads((ROOT/".build/windows-assembly"/("PolyUStudyConsole-"+VERSION+"-windows-x64")/"payload-sha256.json").read_text(encoding="utf-8"))
    sentinel=profile/"account/documents/synthetic-sentinel.txt"
    sentinel.parent.mkdir(parents=True);sentinel.write_text("synthetic-owned-by-CI",encoding="utf-8")
    try:
        subprocess.run([str(setup),"/S"],check=True,timeout=180)
        for name,digest in manifest.items():
            if not (install/name).is_file():
                raise AssertionError({"missingPackageFile":name, "installedRootNames":sorted(p.name for p in install.iterdir())})
            assert hashlib.sha256((install/name).read_bytes()).hexdigest()==digest,name
        worker=[str(install/"runtime/python.exe"),"-I","-B","-X","utf8",str(install/"WindowsApp/main.py")]
        subprocess.run([*worker,"--platform-check"],check=True,timeout=180,capture_output=True)
        subprocess.run([*worker,"--gui-smoke"],check=True,timeout=180)
        subprocess.run([str(install/"Uninstall.exe"),"/S"],check=True,timeout=180)
        deadline=time.monotonic()+30
        while install.exists() and time.monotonic()<deadline:time.sleep(.2)
        assert not install.exists(),"Package files remain after uninstall"
        assert sentinel.read_text(encoding="utf-8")=="synthetic-owned-by-CI","Uninstaller removed personal profile"
    finally:
        # This path was absent before the test and contains only the CI-created sentinel.
        if profile.exists():shutil.rmtree(profile)
    manifest_file=ROOT/".build/windows-assembly"/("PolyUStudyConsole-"+VERSION+"-windows-x64")/"payload-sha256.json"
    report={"ok":True,"payloadManifestSHA256":hashlib.sha256(manifest_file.read_bytes()).hexdigest(),"setupSHA256":hashlib.sha256(setup.read_bytes()).hexdigest(),"installedPayloadHashesVerified":len(manifest),"installedRuntimeExecuted":True,"installedGUIExecuted":True,
            "nativeInstallUninstall":True,"uninstallPreservesProfile":True,"realAccountAccessed":False}
    (ROOT/"验证/windows-installer-evidence.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__=="__main__":main()
