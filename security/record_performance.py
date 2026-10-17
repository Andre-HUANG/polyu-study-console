"""Measure the clean final commit with disposable synthetic profiles only."""
import argparse,base64,datetime as dt,hashlib,json,os,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"security"))
from release_guard import source_sha
from release_build import source_hashes, VERSION, BUILD, BASELINE_VERSION, CONSOLE_FILES, ASSETS
from performance_receipt import canonical,validate,write_reports
from release_updates import run_sign
from prepare_updater import prepare

def main():
    p=argparse.ArgumentParser();p.add_argument("--baseline",type=pathlib.Path,required=True);p.add_argument("--baseline-root",type=pathlib.Path,required=True);p.add_argument("--candidate",type=pathlib.Path,required=True);a=p.parse_args()
    if sys.platform!="darwin":raise RuntimeError("Native Mac independent performance measurement required")
    head=source_sha();evidence=json.loads((ROOT/"Mac发布/build-evidence.json").read_text())
    if evidence.get("sourceHashes")!=source_hashes():raise RuntimeError("Build the current clean source before measuring")
    candidate=a.candidate.resolve()
    import plistlib
    for path,expected in ((a.baseline,BASELINE_VERSION),(candidate,VERSION)):
        if plistlib.loads((path/"Contents/Info.plist").read_bytes()).get("CFBundleShortVersionString") != expected:
            raise RuntimeError("Performance application version differs from expected release")
    if json.loads((a.baseline_root/"学习控制台/release.json").read_text())["version"] != BASELINE_VERSION:
        raise RuntimeError("Performance baseline source version differs from expected release")
    resources=candidate/"Contents/Resources"
    if hashlib.sha256((candidate/"Contents/MacOS/PolyUConsole").read_bytes()).hexdigest() != evidence.get("candidateExecutableSHA256"):
        raise RuntimeError("Measured candidate executable differs from the current built source")
    for name in CONSOLE_FILES:
        if (resources/"console"/name).read_bytes() != (ROOT/"学习控制台"/name).read_bytes():
            raise RuntimeError("Measured candidate dashboard differs from the current source")
    for name in ASSETS:
        if (resources/"console/assets"/name).read_bytes() != (ROOT/"学习控制台/assets"/name).read_bytes():
            raise RuntimeError("Measured candidate asset differs from the current source")
    subprocess.run([sys.executable,"-B",str(ROOT/"验证/verify_mac_bundle.py")],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable,"-B",str(ROOT/"验证/measure_idle.py"),"--baseline",str(a.baseline),"--candidate",str(candidate)],cwd=ROOT,check=True)
    folder=ROOT/".security-local";folder.mkdir(exist_ok=True)
    reader=resources/"read-tools/DocumentReader"
    for label,root in (("baseline",a.baseline_root),("candidate",ROOT)):
        subprocess.run([sys.executable,"-B",str(ROOT/"验证/benchmark.py"),"--root",str(root),"--reader",str(reader),"--output",str(folder/("final-"+label+"-performance.json"))],cwd=ROOT,check=True)
    if source_sha()!=head:raise RuntimeError("Source moved during independent measurement")
    report={"measuredCandidateCommit":head,"sourceHashes":source_hashes(),"checkedAt":dt.datetime.now(dt.timezone.utc).isoformat(),
        "idleCPU":json.loads((ROOT/(".build/idle-cpu-"+BUILD+".json")).read_text()),
        "syntheticSynchronization":{label:json.loads((folder/("final-"+label+"-performance.json")).read_text()) for label in ("baseline","candidate")}}
    body=canonical(report);path=folder/"final-performance-receipt.json";path.write_bytes(body);path.chmod(0o600)
    envelope=json.dumps({"body":base64.b64encode(body).decode(),"signature":run_sign(prepare("mac")/"bin/sign_update",path)})
    validate(envelope,head)
    out=folder/"independent-performance.json";out.write_text(envelope);out.chmod(0o600)
    write_reports(folder,envelope,head)
    print(json.dumps({"ok":True,"sourceSHA":head,"containsRealAccountData":False,"envelopeBytes":len(envelope)}))
if __name__=="__main__":main()
