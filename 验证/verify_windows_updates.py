
"""Disposable Windows runner: real WinSparkle SDK and production install helper.
The old shell/writer drain is a fixture. Updated packages boot the real WebView2
shell. An 8-second failure timeout and PID recorder adapt the helper for tests.
"""
import base64,ctypes,functools,hashlib,importlib.util,json,os,pathlib,shutil,subprocess,sys,tempfile,threading,time,zipfile
from ctypes import wintypes
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
ROOT=pathlib.Path(__file__).resolve().parent.parent
def run(*args,**kw):return subprocess.run(list(map(str,args)),check=True,capture_output=True,**kw)
def child(case,scenario):
 case=pathlib.Path(case);settings=json.loads((case/"driver.json").read_text());target=pathlib.Path(settings["target"]);sys.path.insert(0,str(target/"WindowsApp"))
 module_spec=importlib.util.spec_from_file_location("test_updater",target/"WindowsApp/updater.py");module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
 module.LATEST=settings["feed"]
 body=(case/"public/updates.json").read_bytes();signature=(case/"public/updates.json.sig").read_bytes()
 module.public_read=lambda name:body if name=="updates.json" else signature
 finish=threading.Event();errors=[];notes=[];helpers=[]
 def progress(stage,extra=None):(case/"driver-progress.json").write_text(json.dumps({"stage":stage,"extra":extra},ensure_ascii=False))
 progress("before SDK initialization")
 class Backend:
  version="1.8.1"
  def request(self,*args):return {"ok":True}
  def stop(self):pass
 class Console:
  def evaluate_js(self,*args):return True
  def destroy(self):finish.set()
 class App:
  stopping=False;backend=Backend();console=Console()
  def notice(self,message):notes.append(message)
 app=App()
 original=subprocess.Popen
 def popen(args,*a,**kw):
  if any(str(arg).endswith("update_transaction.py") for arg in args):
   helper_file=pathlib.Path(next(str(arg) for arg in args if str(arg).endswith("update_transaction.py")))
   source=helper_file.read_text()
   if scenario=="rollback":source=source.replace("time.monotonic()+90","time.monotonic()+8")
   source=source.replace("        limit=time.monotonic()+","        pathlib.Path("+repr(str(case/"new-pid.json"))+").write_text(json.dumps({'pid':process.pid,'target':str(target)}))\n        limit=time.monotonic()+")
   source=source.replace('        subprocess.Popen([str(target/"runtime/pythonw.exe"),"-I","-B","-X","utf8",str(target/"WindowsApp/main.py")])','        restored_process=subprocess.Popen([str(target/"runtime/pythonw.exe"),"-I","-B","-X","utf8",str(target/"WindowsApp/main.py")])\n        pathlib.Path('+repr(str(case/"restored-pid.json"))+').write_text(json.dumps({"pid":restored_process.pid,"target":str(target)}))')
   helper_file.write_text(source)
   kw.setdefault("stdin",subprocess.DEVNULL)
   kw.setdefault("stdout",subprocess.DEVNULL)
   kw.setdefault("stderr",subprocess.DEVNULL)
   process=original(args,*a,**kw)
   helpers.append({"pid":process.pid,"stage":str(helper_file.parent)})
   (case/"helper.json").write_text(json.dumps(helpers[-1]))
   return process
  return original(args,*a,**kw)
 subprocess.Popen=popen
 updater=module.Updater(app)
 progress('SDK initialized')
 updater._call("win_sparkle_set_lang",[ctypes.c_char_p],b"en")
 callback=ctypes.CFUNCTYPE(None)(lambda:(errors.append(True),finish.set()))
 updater._call("win_sparkle_set_error_callback",[ctypes.CFUNCTYPE(None)],callback)
 user=ctypes.windll.user32
 user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
 user.GetWindowTextLengthW.argtypes=[wintypes.HWND];user.GetWindowTextLengthW.restype=ctypes.c_int
 user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
 user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM];user.PostMessageW.restype=wintypes.BOOL
 user.IsWindowEnabled.argtypes=[wintypes.HWND];user.IsWindowEnabled.restype=wintypes.BOOL
 user.IsWindowVisible.argtypes=[wintypes.HWND];user.IsWindowVisible.restype=wintypes.BOOL
 enum=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
 user.EnumWindows.argtypes=[enum,wintypes.LPARAM];user.EnumChildWindows.argtypes=[wintypes.HWND,enum,wintypes.LPARAM]
 def caption(hwnd):
  size=user.GetWindowTextLengthW(hwnd);text=ctypes.create_unicode_buffer(size+1);user.GetWindowTextW(hwnd,text,size+1);return text.value.replace("&","").strip().lower()
 def buttons(hwnd,extra):
  title=caption(hwnd)
  if title in {"install update","install","download update","download","install and relaunch","install and restart"} and user.IsWindowEnabled(hwnd) and user.IsWindowVisible(hwnd):
   progress("click SDK install",title);user.PostMessageW(hwnd,0xF5,0,0)
  return True
 button_callback=enum(buttons)
 def windows(hwnd,extra):
  pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
  if pid.value==os.getpid():user.EnumChildWindows(hwnd,button_callback,0)
  return True
 window_callback=enum(windows)
 updater.check()
 progress('checking native SDK update')
 deadline=time.monotonic()+65
 while not finish.is_set() and time.monotonic()<deadline:
  user.EnumWindows(window_callback,0);time.sleep(.2)
 assert finish.is_set(),{"scenario":scenario,"notes":notes}
 expected_failure=scenario in {"wrong-signature","corrupt","offline"}
 assert bool(helpers)!=expected_failure,{"scenario":scenario,"helpers":helpers,"errors":errors,"notes":notes}
 (case/"driver-result.json").write_text(json.dumps({"ok":True,"helperStarted":bool(helpers),"sdkRejected":bool(errors),"notesCount":len(notes)}))
 # The SDK's wxWidgets window/thread belong to this disposable process.
 os._exit(0)
def main():
 if sys.platform!="win32" or os.environ.get("GITHUB_ACTIONS")!="true":raise SystemExit("Native update checks require a disposable Windows CI runner")
 if len(sys.argv)>1:
  assert sys.argv[1]=="--driver";child(sys.argv[2],sys.argv[3]);return
 sys.path.insert(0,str(ROOT));from release_build import VERSION,NAME,source_hashes
 from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
 from cryptography.hazmat.primitives import serialization
 bundle=ROOT/".build/windows-assembly"/("PolyUStudyConsole-"+VERSION+"-windows-x64")
 setup=ROOT/"Win发布"/("PolyUStudyConsole-"+VERSION+"-windows-x64-setup.exe")
 portable=ROOT/"Win发布"/("PolyUStudyConsole-"+VERSION+"-windows-x64.zip")
 results=[]
 real_install=pathlib.Path(os.environ["LOCALAPPDATA"])/"Programs/PolyUStudyConsole";real_profile=pathlib.Path(os.environ["LOCALAPPDATA"])/NAME
 assert not real_install.exists() and not real_profile.exists(),"Refuse existing program/account"
 with tempfile.TemporaryDirectory(prefix="polyu-windows-updates-") as temporary:
  root=pathlib.Path(temporary)
  for mode,scenario in [("portable","upgrade"),("portable","wrong-signature"),("portable","corrupt"),("portable","offline"),("portable","rollback"),("installed","upgrade")]:
   case=root/(mode+"-"+scenario);case.mkdir();public=case/"public";public.mkdir()
   if mode=="installed":
    run(setup,"/S",timeout=180);target=real_install;profile=real_profile
   else:target=case/"program";shutil.copytree(bundle,target);profile=case/"local"/NAME
   release=target/"console/release.json";value=json.loads(release.read_text());value["version"]="1.8.1";value["build"]="181";release.write_text(json.dumps(value))
   key=Ed25519PrivateKey.generate();pub=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
   lock_path=target/"updater-lock.json";lock=json.loads(lock_path.read_text());lock["publicKey"]=pub;lock_path.write_text(json.dumps(lock))
   (target/"obsolete-program.py").write_text("# Synthetic old-only packaged module")
   old_manifest={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob("*") if p.is_file() and p.name not in {"payload-sha256.json","Uninstall.exe"}}
   (target/"payload-sha256.json").write_text(json.dumps(old_manifest))
   sentinel=profile/"account/documents/update-sentinel.txt";sentinel.parent.mkdir(parents=True,exist_ok=True);sentinel.write_text("synthetic-update-preserved")
   name="PolyUStudyConsole-"+VERSION+("-windows-x64-setup.exe" if mode=="installed" else "-windows-x64.zip")
   package=public/name
   if scenario=="rollback":
    fresh=case/"fresh"/bundle.name;fresh.parent.mkdir();shutil.copytree(bundle,fresh)
    (fresh/"WindowsApp/main.py").write_text("raise SystemExit(7)\n")
    manifest={p.relative_to(fresh).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in fresh.rglob("*") if p.is_file() and p.name!="payload-sha256.json"}
    (fresh/"payload-sha256.json").write_text(json.dumps(manifest))
    with zipfile.ZipFile(package,"w",zipfile.ZIP_DEFLATED) as z:
     for p in fresh.rglob("*"):
      if p.is_file():z.write(p,p.relative_to(fresh.parent).as_posix())
   else:shutil.copy2(setup if mode=="installed" else portable,package)
   good=package.read_bytes()
   metadata={"schema":1,"repository":"Andre-HUANG/polyu-study-console","version":VERSION,"build":182,"sourceSHA":"a"*40,"draft":False,"prerelease":False,"artifacts":{name:{"sha256":hashlib.sha256(good).hexdigest(),"bytes":len(good),"url":"https://github.com/Andre-HUANG/polyu-study-console/releases/download/v"+VERSION+"/"+name,"platform":"windows","architecture":"x64"}}}
   body=json.dumps(metadata,sort_keys=True).encode();(public/"updates.json").write_bytes(body);(public/"updates.json.sig").write_bytes(base64.b64encode(key.sign(body)))
   signature=base64.b64encode(key.sign(good)).decode()
   if scenario=="wrong-signature":signature=base64.b64encode(Ed25519PrivateKey.generate().sign(good)).decode()
   if scenario=="corrupt":package.write_bytes(good[:100])
   class Handler(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
     if scenario=="offline":self.send_error(503);return
     super().do_GET()
   http=ThreadingHTTPServer(("127.0.0.1",0),functools.partial(Handler,directory=str(public)));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
   feed=f"http://127.0.0.1:{http.server_port}/"
   xml=f'<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><title>Isolated updater</title><item><title>1.8.2</title><description>Synthetic rehearsal</description><enclosure url="{feed+name}" length="{len(good)}" type="application/octet-stream" sparkle:version="{VERSION}" sparkle:shortVersionString="{VERSION}" sparkle:edSignature="{signature}"/></item></channel></rss>'
   (public/("appcast-windows-setup.xml" if mode=="installed" else "appcast-windows-portable.xml")).write_text(xml)
   (case/"driver.json").write_text(json.dumps({"target":str(target),"feed":feed}))
   env=os.environ.copy()
   if mode=="portable":env["LOCALAPPDATA"]=str(case/"local")
   process=None;helper=None;newpid=None
   try:
    process=subprocess.Popen([sys.executable,"-I","-B","-X","utf8",str(__file__),"--driver",str(case),scenario],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:stdout,stderr=process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
     progress=case/"driver-progress.json"
     raise AssertionError({"scenario":scenario,"driverStage":json.loads(progress.read_text()) if progress.exists() else None,"helperStarted":(case/"helper.json").exists()}) from None
    assert process.returncode==0,{"scenario":scenario,"stderr":stderr.decode(errors="replace")[-1800:]}
    report=json.loads((case/"driver-result.json").read_text())
    helper_file=case/"helper.json"
    if helper_file.exists():
     helper=json.loads(helper_file.read_text());stage=pathlib.Path(helper["stage"]);deadline=time.monotonic()+120
     while time.monotonic()<deadline:
      try:current=json.loads((target/"console/release.json").read_text())["version"]
      except (OSError,ValueError):time.sleep(.2);continue
      if scenario=="upgrade" and (stage/"new-version-ready").exists() and not target.with_name(target.name+".previous-"+VERSION).exists():break
      if scenario=="rollback" and current=="1.8.1" and not target.with_name(target.name+".previous-"+VERSION).exists():break
      time.sleep(.2)
     else:raise AssertionError({"scenario":scenario,"currentVersion":current,"stage":str(stage)})
     p=case/"new-pid.json"
     if p.exists():newpid=json.loads(p.read_text())["pid"]
     # Both successful and failed launches are owned by this isolated transaction.
     if newpid:subprocess.run(["taskkill","/PID",str(newpid),"/T","/F"],capture_output=True)
     restored=case/"restored-pid.json"
     if restored.exists():subprocess.run(["taskkill","/PID",str(json.loads(restored.read_text())["pid"]),"/T","/F"],capture_output=True)
    if helper and scenario=="upgrade":
     cleanup_deadline=time.monotonic()+20
     while stage.exists() and time.monotonic()<cleanup_deadline:time.sleep(.2)
     assert not stage.exists(),"Program update staging was not released"
     report["updateStageRemoved"]=True
    current=json.loads((target/"console/release.json").read_text())["version"]
    assert current==(VERSION if scenario=="upgrade" else "1.8.1")
    assert sentinel.read_text()=="synthetic-update-preserved"
    assert (target/"obsolete-program.py").exists() == (scenario!="upgrade")
    report["oldOnlyProgramFilesRemoved"]=scenario=="upgrade"
    result={"mode":mode,"scenario":scenario,"ok":True,"versionAfter":current,"accountPreserved":True,"nativeSDK":True,"realUpdatedShellBoot":scenario=="upgrade",**report};results.append(result);print(json.dumps(result),flush=True)
   finally:
    if process and process.poll() is None:process.kill();process.wait()
    if helper:
     subprocess.run(["taskkill","/PID",str(helper["pid"]),"/T","/F"],capture_output=True)
    http.shutdown();http.server_close();thread.join()
    if mode=="installed":
     if (real_install/"Uninstall.exe").exists():run(real_install/"Uninstall.exe","/S",timeout=180)
     limit=time.monotonic()+20
     while real_install.exists() and time.monotonic()<limit:time.sleep(.2)
     assert not real_install.exists()
     if real_profile.exists():shutil.rmtree(real_profile)
 report={"ok":len(results)==6,"platform":"windows","sourceHashes":source_hashes(),"payloadManifestSHA256":hashlib.sha256((bundle/"payload-sha256.json").read_bytes()).hexdigest(),"sdkVersion":"0.9.4","scenarios":results,"realAccountAccessed":False,"scope":"Real WinSparkle UI/download/signature checks and production program helper on a disposable runner. Old shell/writer drain is a fixture; successful updates boot the actual WebView2 shell. Helper only adapts launch timeout to 8 seconds and records owned PID. Failure tests are isolated.","actualUserUpgradeValidated":False}
 (ROOT/"验证/windows-update-evidence.json").write_text(json.dumps(report,indent=2));print("Windows native updates passed.")
if __name__=="__main__":main()
