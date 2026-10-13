"""Unified fail-closed preflight/build/validate/publish entry point. No force pushes."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from release_build import VERSION
from release_updates import generate as generate_updates, verify as verify_updates, SIGNED as SIGNED_UPDATES
from github_api import request as github_request, upload as github_upload
from release_guard import source_sha, accepted, trusted_run, independent_acceptance
from performance_receipt import write_reports as write_performance
GIT=os.environ.get("POLYU_GIT","git")
REPO="Andre-HUANG/polyu-study-console"
REPORT_FILES=("performance-"+VERSION+".md","performance-"+VERSION+".json","validation-"+VERSION+".md")


def run(*args, capture=False):
    return subprocess.check_output(args,cwd=ROOT,text=True).strip() if capture else subprocess.run(args,cwd=ROOT,check=True)


def collect(source, destination):
    expected={"PolyUStudyConsole-"+VERSION+s for s in ["-arm64.dmg","-arm64.zip","-windows-x64-setup.exe","-windows-x64.zip"]}
    reports=[]
    head=source_sha()
    for platform in ["mac","windows"]:
        candidates=list(source.rglob(platform+"-validation.json"))
        if len(candidates)!=1:raise RuntimeError("Missing/duplicate platform acceptance evidence")
        report=json.loads(candidates[0].read_text())
        if report.get("version")!=VERSION:raise RuntimeError("Wrong candidate version")
        accepted(report,platform,head)
        reports.append(report)
    if len({r["sourceSHA"] for r in reports})!=1:raise RuntimeError("Platforms were built from different commits")
    actual={}
    for report in reports:
        for name,details in report["artifacts"].items():
            if name not in expected or name in actual:raise RuntimeError("Unexpected or duplicate release asset")
            platform_suffixes={"mac":("-arm64.dmg","-arm64.zip"),"windows":("-windows-x64-setup.exe","-windows-x64.zip")}
            if not name.endswith(platform_suffixes[report["platform"]]):raise RuntimeError("Asset assigned to the wrong platform")
            matches=list(source.rglob(name))
            if len(matches)!=1:raise RuntimeError("Missing/duplicate release asset")
            path=matches[0]
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest!=details["sha256"] or path.stat().st_size!=details["bytes"]:raise RuntimeError("Release digest/size mismatch")
            actual[name]=digest
    if set(actual)!=expected:raise RuntimeError("Both platforms' full installation assets are required")
    destination.mkdir(parents=True,exist_ok=True)
    for name in sorted(actual):
        path=next(source.rglob(name))
        if path.resolve()!=(destination/name).resolve():shutil.copyfile(path,destination/name)
    (destination/"SHA256SUMS.txt").write_text("".join(actual[n]+"  "+n+"\n" for n in sorted(actual)),encoding="utf-8")
    (destination/"validation.json").write_text(json.dumps({"version":VERSION,"sourceSHA":reports[0]["sourceSHA"],"platforms":reports,"artifacts":actual},indent=2),encoding="utf-8")
    for name in REPORT_FILES:shutil.copyfile(ROOT/"release"/name,destination/name)
    generate_updates(destination,reports[0]["sourceSHA"])
    return reports[0]["sourceSHA"]


def main():
    parser=argparse.ArgumentParser()
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check",action="store_true");action.add_argument("--build",choices=["mac","windows"])
    action.add_argument("--validate",type=Path);action.add_argument("--publish",type=Path);action.add_argument("--check-run")
    parser.add_argument("--output",type=Path,default=ROOT/".security-local/release")
    args=parser.parse_args()
    if args.check or args.build:
        if args.build == "windows" and sys.platform != "win32":
            raise RuntimeError("Use WindowsApp/assemble_windows.py for static macOS assembly; release acceptance needs Windows")
        if args.build == "windows":
            run(sys.executable,"-B","WindowsApp/assemble_windows.py","--download")
            bundle=ROOT/".build/windows-assembly"/("PolyUStudyConsole-"+VERSION+"-windows-x64")
            run(str(bundle/"runtime/python.exe"),"-I","-B","-X","utf8","security/check.py")
        else: run(sys.executable,"-B","security/check.py")
        if not args.build:return
        script="MacApp/build.py" if args.build=="mac" else "WindowsApp/assemble_windows.py"
        command=[sys.executable,"-B",script]
        if args.build=="windows":command+=["--download"]
        if args.build != "windows": run(*command)
        if args.build=="mac":
            run(sys.executable,"-B","验证/verify_mac_bundle.py");run(sys.executable,"-B","验证/run_native_gui.py")
            run(sys.executable,"-B","MacApp/build.py","--verify-release")
        else:
            if sys.platform!="win32":raise RuntimeError("Windows binary assembly is static only; native release validation must run on Windows")
            bundle=ROOT/".build/windows-assembly"/("PolyUStudyConsole-"+VERSION+"-windows-x64")
            worker=[str(bundle/"runtime/python.exe"),"-I","-B","-X","utf8",str(bundle/"WindowsApp/main.py")]
            native=run(*worker,"--platform-check",capture=True)
            (ROOT/"验证/windows-native-evidence.json").write_text(native,encoding="utf-8")
            os.environ["POLYU_GUI_EVIDENCE"]=str(ROOT/"验证/windows-gui-evidence.json")
            run(*worker,"--gui-smoke");run(sys.executable,"-B","WindowsApp/installer_verify.py")
        run(sys.executable,"-B","security/validate_platform.py",args.build)
    elif args.validate:
        collect(args.validate,args.output)
    elif args.check_run:
        if not re.fullmatch(r"[0-9]+",args.check_run):raise RuntimeError("Invalid run ID")
        data=github_request("/actions/runs/"+args.check_run)
        trusted_run(data,source_sha())
    else:
        source=args.publish
        report=json.loads((source/"validation.json").read_text())
        head=source_sha()
        validation_run=os.environ.get("VALIDATION_RUN","")
        if not re.fullmatch(r"[0-9]+",validation_run):raise RuntimeError("Publication requires the trusted validation run ID")
        trusted_run(github_request("/actions/runs/"+validation_run),head)
        if report["sourceSHA"]!=head or report["version"]!=VERSION:raise RuntimeError("Source changed since validation")
        expected={"PolyUStudyConsole-"+VERSION+s for s in ["-arm64.dmg","-arm64.zip","-windows-x64-setup.exe","-windows-x64.zip"]}
        if set(report["artifacts"])!=expected:raise RuntimeError("Incomplete release payload")
        for platform in ("mac","windows"):
            matches=[r for r in report.get("platforms",[]) if r.get("platform")==platform]
            if len(matches)!=1:raise RuntimeError("Missing platform acceptance")
            accepted(matches[0],platform,head)
        sums="".join(report["artifacts"][n]+"  "+n+"\n" for n in sorted(expected))
        if (source/"SHA256SUMS.txt").read_text(encoding="utf-8")!=sums:raise RuntimeError("Checksum manifest differs")
        for name,digest in report["artifacts"].items():
            if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:raise RuntimeError("Changed release payload")
        for name in REPORT_FILES:
            if (source/name).read_bytes()!=(ROOT/"release"/name).read_bytes():raise RuntimeError("Changed release report")
        verify_updates(source)
        if os.environ.get("PUBLISH_RELEASE")=="true":
            independent_acceptance(os.environ.get("POLYU_RELEASE_ACCEPTANCE",""),head)
            write_performance(source,os.environ.get("POLYU_RELEASE_PERFORMANCE",""),head)
        tag="v"+VERSION
        if run(GIT,"ls-remote","--tags","https://github.com/"+REPO+".git","refs/tags/"+tag,capture=True):
            raise RuntimeError("Version tag already exists; never replace an existing version")
        releases=github_request("/releases?per_page=100")
        if any(r["tag_name"]==tag for r in releases):raise RuntimeError("Version Release already exists")
        notes=(ROOT/"release"/(tag+".md")).read_text(encoding="utf-8")
        release=github_request("/releases","POST",{"tag_name":tag,"target_commitish":head,
            "name":"PolyU Study Console "+VERSION,"body":notes,"draft":True,"prerelease":False})
        # A partial upload stays Draft. Published old assets are never overwritten.
        for name in sorted(expected|{"SHA256SUMS.txt"}|set(SIGNED_UPDATES)|set(REPORT_FILES)):
            result=github_upload(release["id"],source/name)
            expected_digest=hashlib.sha256((source/name).read_bytes()).hexdigest()
            if result.get("digest")!="sha256:"+expected_digest:raise RuntimeError("Uploaded asset digest mismatch")
        if os.environ.get("PUBLISH_RELEASE")=="true":
            release=github_request("/releases/"+str(release["id"]),"PATCH",{"draft":False})
        print(json.dumps({"release":release["html_url"],"draft":release["draft"],"sourceSHA":head}))

if __name__=="__main__":main()
