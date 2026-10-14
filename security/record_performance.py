"""Measure the clean final commit with disposable synthetic profiles only."""
import argparse,base64,datetime as dt,json,os,pathlib,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"security"))
from release_guard import source_sha
from release_build import source_hashes
from performance_receipt import canonical,validate,write_reports
from release_updates import run_sign
from prepare_updater import prepare

def main():
    p=argparse.ArgumentParser();p.add_argument("--baseline",type=pathlib.Path,required=True);p.add_argument("--baseline-root",type=pathlib.Path,required=True);p.add_argument("--candidate",type=pathlib.Path,required=True);a=p.parse_args()
    if sys.platform!="darwin":raise RuntimeError("Native Mac independent performance measurement required")
    head=source_sha();evidence=json.loads((ROOT/"Mac发布/build-evidence.json").read_text())
    if evidence.get("sourceHashes")!=source_hashes():raise RuntimeError("Build the current clean source before measuring")
    candidate=a.candidate.resolve()
    resources=candidate/"Contents/Resources"
    subprocess.run([sys.executable,"-B",str(ROOT/"验证/verify_mac_bundle.py")],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable,"-B",str(ROOT/"验证/measure_idle.py"),"--baseline",str(a.baseline),"--candidate",str(candidate)],cwd=ROOT,check=True)
    folder=ROOT/".security-local";folder.mkdir(exist_ok=True)
    reader=resources/"read-tools/DocumentReader"
    for label,root in (("baseline",a.baseline_root),("candidate",ROOT)):
        subprocess.run([sys.executable,"-B",str(ROOT/"验证/benchmark.py"),"--root",str(root),"--reader",str(reader),"--output",str(folder/("final-"+label+"-performance.json"))],cwd=ROOT,check=True)
    if source_sha()!=head:raise RuntimeError("Source moved during independent measurement")
    report={"measuredCandidateCommit":head,"sourceHashes":source_hashes(),"checkedAt":dt.datetime.now(dt.timezone.utc).isoformat(),
        "idleCPU":json.loads((ROOT/".build/idle-cpu-191.json").read_text()),
        "syntheticSynchronization":{label:json.loads((folder/("final-"+label+"-performance.json")).read_text()) for label in ("baseline","candidate")}}
    body=canonical(report);path=folder/"final-performance-receipt.json";path.write_bytes(body);path.chmod(0o600)
    envelope=json.dumps({"body":base64.b64encode(body).decode(),"signature":run_sign(prepare("mac")/"bin/sign_update",path)})
    validate(envelope,head)
    out=folder/"independent-performance.json";out.write_text(envelope);out.chmod(0o600)
    write_reports(folder,envelope,head)
    print(json.dumps({"ok":True,"sourceSHA":head,"containsRealAccountData":False,"envelopeBytes":len(envelope)}))
if __name__=="__main__":main()
