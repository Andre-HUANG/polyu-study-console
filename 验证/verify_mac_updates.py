
"""Native Sparkle download/verification/install/restart and production recovery.
No real profile or installed app is accessed. Unique temporary bundles and a
throwaway signing key are used. Only recovery storage/id and timeout are adapted.
"""
import base64,functools,hashlib,json,os,pathlib,plistlib,shutil,subprocess,sys,tempfile,threading,time,uuid
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from prepare_updater import prepare
from release_build import source_hashes
def run(*args):
 subprocess.run(list(map(str,args)),check=True,capture_output=True)
def main():
 if sys.platform!="darwin":raise SystemExit("Native Sparkle acceptance requires macOS")
 from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
 from cryptography.hazmat.primitives import serialization
 reports=[]
 sdk=prepare("mac")
 with tempfile.TemporaryDirectory(prefix="polyu-update-smoke-") as temporary:
  root=pathlib.Path(temporary).resolve()
  recovery=(ROOT/"MacApp/UpdateRecovery.swift").read_text()
  original='FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]\n            .appendingPathComponent("local.polyu.studyconsole/program-updates", isDirectory: true)'
  assert original in recovery
  recovery=recovery.replace(original,'testRoot.appendingPathComponent("program-updates", isDirectory: true)')
  recovery=recovery.replace('static let identifier = "local.polyu.studyconsole"','static let identifier = Bundle.main.bundleIdentifier!')
  recovery=recovery.replace("addingTimeInterval(120)","addingTimeInterval(8)")
  recovery=recovery.replace("} catch { return 1 } // Keep previous.app", '} catch { record("recoveryError",String(describing:error));return 1 } // Keep previous.app')
  (root/"Recovery.swift").write_text(recovery)
  run("xcrun","swiftc","-parse-as-library","-O","-module-cache-path",root/"modules","-target","arm64-apple-macos13.0",ROOT/"验证/NativeUpdateSmoke.swift",root/"Recovery.swift","-F",sdk,"-framework","Sparkle","-Xlinker","-rpath","-Xlinker","@executable_path/../Frameworks","-o",root/"Smoke")
  for scenario in ["upgrade","wrong-signature","corrupt","offline","rollback","abort-recovery"]:
   case=root/scenario;case.mkdir();public=case/"public";public.mkdir()
   class Handler(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
     if scenario=="offline":self.send_error(503);return
     super().do_GET()
   http=ThreadingHTTPServer(("127.0.0.1",0),functools.partial(Handler,directory=str(public)))
   worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
   key=Ed25519PrivateKey.generate()
   pub=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
   identity="local.polyu.updatesmoke."+uuid.uuid4().hex
   target=case/"installed"/"UpdaterSmoke.app"
   fresh=case/"new"/"UpdaterSmoke.app"
   for app,version,build in [(target,"1.8.2","182"),(fresh,"1.9.1","191")]:
    contents=app/"Contents";(contents/"MacOS").mkdir(parents=True);(contents/"Frameworks").mkdir()
    shutil.copy2(root/"Smoke",contents/"MacOS/PolyUConsole")
    shutil.copytree(sdk/"Sparkle.framework",contents/"Frameworks/Sparkle.framework",symlinks=True)
    info={"CFBundleIdentifier":identity,"CFBundleExecutable":"PolyUConsole","CFBundlePackageType":"APPL","CFBundleName":"UpdaterSmoke","CFBundleVersion":build,"CFBundleShortVersionString":version,"TestRoot":str(case),"TestCrash":version=="1.9.1" and scenario=="rollback","TestScenario":scenario,"LSMinimumSystemVersion":"13.0","NSAppTransportSecurity":{"NSAllowsLocalNetworking":True},"SUPublicEDKey":pub,"SUFeedURL":f"http://127.0.0.1:{http.server_port}/feed.xml","SUEnableAutomaticChecks":False,"SUAutomaticallyUpdate":False}
    (contents/"Info.plist").write_bytes(plistlib.dumps(info))
    run("codesign","--force","--deep","--sign","-",app)
    run("codesign","--verify","--deep","--strict",app)
   account=case/"account";account.mkdir();sentinel=account/"synthetic.txt";sentinel.write_text("untouched synthetic account")
   package=public/"update.zip";run("ditto","-c","-k","--sequesterRsrc","--keepParent",fresh,package)
   good=package.read_bytes()
   signature=base64.b64encode(key.sign(good)).decode()
   if scenario=="wrong-signature":signature=base64.b64encode(Ed25519PrivateKey.generate().sign(good)).decode()
   if scenario=="corrupt":package.write_bytes(good[:100])
   xml=f'<?xml version="1.0"?><rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><title>Isolated test</title><item><title>1.9.1</title><description>Synthetic update rehearsal</description><enclosure url="http://127.0.0.1:{http.server_port}/update.zip" length="{len(good)}" type="application/octet-stream" sparkle:version="191" sparkle:shortVersionString="1.9.1" sparkle:edSignature="{signature}"/></item></channel></rss>'.encode()
   feed=xml+b'<!-- sparkle-signatures:\nedSignature: '+base64.b64encode(key.sign(xml))+b'\nlength: '+str(len(xml)).encode()+b'\n-->\n'
   (public/"feed.xml").write_bytes(feed)
   process=None;watcher_pid=None
   try:
    process=subprocess.Popen(["open","-n","-W","-a",str(target),"--stdout",str(case/"out.txt"),"--stderr",str(case/"err.txt")])
    deadline=time.monotonic()+110
    old_file=case/"events-1.8.2.json";new_file=case/"events-1.9.1.json"
    while time.monotonic()<deadline:
     old=json.loads(old_file.read_text()) if old_file.exists() else {}
     new=json.loads(new_file.read_text()) if new_file.exists() else {}
     ready=case/"program-updates/pending.json"
     if scenario=="upgrade" and new.get("acknowledged") and not ready.exists():break
     if scenario=="rollback" and old.get("rolledBack"):break
     if scenario=="abort-recovery" and old.get("abortRecoveryCleared"):break
     if scenario not in {"upgrade","rollback"} and old.get("error"):break
     time.sleep(.2)
    else:raise AssertionError({"scenario":scenario,"old":old,"new":new,"stderr":(case/"err.txt").read_text()[-1500:] if (case/"err.txt").exists() else ""})
    current=plistlib.loads((target/"Contents/Info.plist").read_bytes())["CFBundleShortVersionString"]
    assert current==("1.9.1" if scenario=="upgrade" else "1.8.2")
    assert sentinel.read_text()=="untouched synthetic account"
    if scenario=="abort-recovery":assert old.get("abortRecoveryCleared") and not ready.exists()
    reports.append({"scenario":scenario,"ok":True,"installedVersion":current,"programAccountPreserved":True,"sdkDownloadValidated":scenario in {"upgrade","rollback"},"sourceRecoveryPrepared":bool(old.get("recoveryPrepared"))})
    print(json.dumps(reports[-1]),flush=True)
   finally:
    for name in ["events-1.8.2.json","events-1.9.1.json"]:
     p=case/name
     if p.is_file():
      pid=json.loads(p.read_text()).get("launch")
      if pid:
       try:os.kill(pid,15)
       except ProcessLookupError:pass
    if process and process.poll() is None:process.terminate()
    http.shutdown();http.server_close();worker.join()
 report={"ok":len(reports)==6,"platform":"mac","sourceHashes":source_hashes(),"scenarios":reports,"realAccountAccessed":False,"realCanvasValidated":False,"scope":"Locked Sparkle SDK in isolated native test apps; production UpdateRecovery source adapted only for temporary container/bundle identity and 8-second launch timeout. No real-account upgrade.","sdkVersion":"2.10.0"}
 (ROOT/"验证/mac-update-evidence.json").write_text(json.dumps(report,indent=2))
 print("Native update rehearsal passed.")
if __name__=="__main__":main()
