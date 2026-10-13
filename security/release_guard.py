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
    checks={"mac":{"nativeGUI","backendRuntime","dmgReadOnlyScan","adHocSigned","nativeUpdates"},
            "windows":{"nativeGUI","backendRuntime","dpapi","installUninstall","nativeUpdates"}}[platform]
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

def independent_acceptance(encoded,head):
    """Fail closed on missing, unsigned or stale independent service acceptance."""
    import base64,json,uuid
    from datetime import datetime,timezone
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        envelope=json.loads(encoded);body=base64.b64decode(envelope["body"],validate=True)
        public=json.loads((ROOT/"updater-lock.json").read_text())["publicKey"]
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public,validate=True)).verify(base64.b64decode(envelope["signature"],validate=True),body)
        value=json.loads(body);now=datetime.now(timezone.utc);at=datetime.fromisoformat(value["checkedAt"]);age=(now-at).total_seconds()
        required={"authenticatedOwnerChecked","calendarServiceChecked","savedInterpretationChecked","accountFilesUnmodified"}
        if (value.get("schema")!=1 or value.get("sourceSHA")!=head or value.get("scope")!="independent-local-mac" or
            not 0<=age<=86400 or set(value.get("checks",{}))!=required or not all(value["checks"][k] is True for k in required)):
            raise ValueError("Missing acceptance")
        if set(value)!={"schema","sourceSHA","checkedAt","scope","receipt","checks"}:raise ValueError("Unexpected acceptance fields")
        uuid.UUID(value["receipt"])
        return True
    except Exception:
        raise RuntimeError("Publication requires a current signed independent real-service receipt") from None
