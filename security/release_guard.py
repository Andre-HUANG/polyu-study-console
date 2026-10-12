"""Bind acceptance reports and publication to a clean, trusted source commit."""
import os
import re
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def source_sha():
    git=os.environ.get("POLYU_GIT","git")
    head=subprocess.check_output([git,"rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}",head):raise RuntimeError("Invalid source commit")
    if subprocess.check_output([git,"status","--porcelain","--untracked-files=normal"],cwd=ROOT):
        raise RuntimeError("Commit source changes before final binary validation")
    advertised=os.environ.get("POLYU_SOURCE_SHA") or os.environ.get("GITHUB_SHA")
    if advertised and advertised!=head:raise RuntimeError("CI checkout differs from the declared source")
    return head

def accepted(report,platform,head):
    checks={"mac":{"nativeGUI","backendRuntime","dmgReadOnlyScan","adHocSigned"},
            "windows":{"nativeGUI","backendRuntime","dpapi","installUninstall"}}[platform]
    if (report.get("ok") is not True or report.get("platform")!=platform or
        report.get("sourceSHA")!=head or report.get("sourceTreeClean") is not True or
        report.get("archivePrivacyScan") is not True or
        not all(report.get("platformChecks",{}).get(c) is True for c in checks)):
        raise RuntimeError("Missing or stale native platform acceptance")

def trusted_run(data,head):
    if (data.get("status")!="completed" or data.get("conclusion")!="success" or
        data.get("head_branch")!="main" or data.get("head_sha")!=head or
        data.get("event") not in {"push","workflow_dispatch"} or
        data.get("path")!=".github/workflows/ci.yml"):
        raise RuntimeError("Require a successful trusted main validation at this commit")
