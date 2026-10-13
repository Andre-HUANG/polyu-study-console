"""Read-only local service acceptance; private details never enter the receipt."""
import base64,datetime as dt,json,os,pathlib,sys,uuid
from urllib.parse import urlencode
ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"学习控制台"));sys.path.insert(0,str(ROOT/"security"))
from release_guard import source_sha,independent_acceptance
from release_updates import run_sign
from prepare_updater import prepare
from native_canvas import HOST,session_class,normalized_cookies
from canvas_web import authenticated_user
from session_store import read_session
from timetable_reader import rules_from

def main():
    if sys.platform!="darwin":raise SystemExit("Independent live acceptance requires the authorized Mac")
    head=source_sha();profile=pathlib.Path.home()/"Library/Application Support/PolyU study控制台/account"
    before={str(p.relative_to(profile)):(p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns) for p in profile.rglob("*") if p.is_file()}
    config=json.loads((profile/"config.json").read_text());cookie=profile/"browser/canvas-cookies.json";saved=json.loads(cookie.read_text())
    if isinstance(saved,list):rows=saved
    else:
        os.environ["POLYU_SESSION_VAULT"]="/Applications/PolyU study控制台.app/Contents/Resources/read-tools/SessionVault"
        rows=read_session(cookie,saved)
    api=session_class()(HOST,str(profile),cookies=normalized_cookies(rows),persist=False,timeout=25,retries=1)
    checks={};details={}
    try:
        user=authenticated_user(api)
        assert str(user["id"])==str(config["user"]["id"])
        checks["authenticatedOwnerChecked"]=True
        from zoneinfo import ZoneInfo
        today=dt.datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
        ids=[str(c["id"]) for c in config.get("courses",[]) if not c.get("inactive")]
        errors=[];events=[]
        for offset in range(0,len(ids),10):
            args=[("type","event"),("per_page","100"),("start_date",(today-dt.timedelta(days=90)).isoformat()),("end_date",(today+dt.timedelta(days=365)).isoformat())]+[("context_codes[]","course_"+c) for c in ids[offset:offset+10]]
            try:events+=api.get("/api/v1/calendar_events?"+urlencode(args))
            except Exception as e:errors.append(type(e).__name__)
        checks["calendarServiceChecked"]=True
        index=json.loads((profile/"documents/index.json").read_text())
        assert index.get("accountId")==str(config["user"]["id"])
        interpreted=rules=0
        for document in index.get("records",[]):
            cid,fid=document.get("courseId"),document.get("fileId");path=profile/"documents/cache"/(str(cid)+"-"+str(fid)+".json")
            if not fid or not path.is_file():continue
            data=json.loads(path.read_text())
            if data.get("accountId")!=str(config["user"]["id"]):continue
            found=rules_from(document,data.get("pages",[]),config);interpreted+=1;rules+=len(found)
        checks["savedInterpretationChecked"]=True
        details={"courseCount":len(ids),"calendarEntries":len(events),"calendarFailureTypes":errors,"cachedFilesRead":interpreted,"scheduleRowsInterpreted":rules}
    finally:api.close()
    after={str(p.relative_to(profile)):(p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns) for p in profile.rglob("*") if p.is_file()}
    checks["accountFilesUnmodified"]=before==after
    if not all(checks.values()):raise RuntimeError("Acceptance overlapped account writes; retry after synchronization")
    value={"schema":1,"sourceSHA":head,"checkedAt":dt.datetime.now(dt.timezone.utc).isoformat(),"scope":"independent-local-mac","receipt":str(uuid.uuid4()),"checks":checks}
    folder=ROOT/".security-local";folder.mkdir(exist_ok=True)
    body=json.dumps(value,sort_keys=True,separators=(",",":")).encode()
    path=folder/"independent-receipt.json";path.write_bytes(body);path.chmod(0o600)
    signature=run_sign(prepare("mac")/"bin/sign_update",path)
    encoded=json.dumps({"body":base64.b64encode(body).decode(),"signature":signature})
    independent_acceptance(encoded,head)
    output=folder/"independent-acceptance.json";output.write_text(encoded);output.chmod(0o600)
    private=folder/"independent-service-details.json";private.write_text(json.dumps({"receipt":value["receipt"],**details}));private.chmod(0o600)
    print(json.dumps({"ok":True,"receiptContainsAccountDetails":False,"accountFilesWritten":False,"sourceSHA":head}))

if __name__=="__main__":main()
