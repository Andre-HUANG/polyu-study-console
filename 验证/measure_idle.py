
"""Repeated full native-app idle CPU sampling using disposable offline profiles."""
import argparse,ctypes,hashlib,json,os,pathlib,platform,plistlib,signal,statistics,subprocess,sys,tempfile,time,uuid
ROOT=pathlib.Path(__file__).resolve().parent.parent
class Usage(ctypes.Structure):
 _fields_=[("uuid",ctypes.c_ubyte*16)]+[(name,ctypes.c_uint64) for name in ("user","system","idleWakeups","interruptWakeups","pageins","wired","resident","footprint","start","exit")]
class Timebase(ctypes.Structure):_fields_=[("numer",ctypes.c_uint32),("denom",ctypes.c_uint32)]
timebase=Timebase();ctypes.CDLL("/usr/lib/libSystem.B.dylib").mach_timebase_info(ctypes.byref(timebase))
CPU_SECONDS_PER_TICK=timebase.numer/timebase.denom/1e9
lib=ctypes.CDLL("/usr/lib/libproc.dylib")
lib.proc_pid_rusage.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.POINTER(Usage)]
def sample(pids):
 value={}
 for pid in pids:
  row=Usage()
  if lib.proc_pid_rusage(pid,0,ctypes.byref(row))==0:value[pid]={"cpu":(row.user+row.system)*CPU_SECONDS_PER_TICK,"wakeups":row.idleWakeups+row.interruptWakeups,"footprint":row.footprint}
 return value
def processes():
 rows={}
 for line in subprocess.check_output(["ps","-axo","pid=,ppid=,command="],text=True).splitlines():
  parts=line.strip().split(None,2)
  if len(parts)==3:rows[int(parts[0])]=(int(parts[1]),parts[2])
 return rows
def pause(duration):
 limit=time.monotonic()+duration
 while time.monotonic()<limit:time.sleep(min(1,limit-time.monotonic()))
def main():
 p=argparse.ArgumentParser();p.add_argument("--baseline",type=pathlib.Path,required=True);p.add_argument("--candidate",type=pathlib.Path,required=True);p.add_argument("--trials",type=int,default=3);p.add_argument("--seconds",type=int,default=60);a=p.parse_args()
 if sys.platform!="darwin":raise SystemExit("Native Mac CPU measurement only")
 rows=[]
 with tempfile.TemporaryDirectory(prefix="polyu-cpu-controller-") as temporary:
  work=pathlib.Path(temporary)
  source=work/"Visibility.swift"
  source.write_text('import AppKit\nlet pid=pid_t(CommandLine.arguments[1])!\nif let app=NSRunningApplication(processIdentifier:pid) { if CommandLine.arguments[2]=="hide" { app.hide() } else { app.unhide();app.activate(options:[.activateAllWindows,.activateIgnoringOtherApps]) } }')
  subprocess.run(["xcrun","swiftc","-module-cache-path",str(work/"modules"),str(source),"-o",str(work/"visibility")],check=True,capture_output=True)
  measurement_apps={}
  for label,original in [("1.8.1",a.baseline),("1.8.2",a.candidate)]:
   copy=work/("CPU-"+label+".app")
   subprocess.run(["ditto","--norsrc","--noextattr",str(original.resolve()),str(copy)],check=True,capture_output=True)
   metadata=copy/"Contents/Info.plist"
   info=plistlib.loads(metadata.read_bytes())
   info["CFBundleIdentifier"]="local.polyu.cpu."+label.replace(".","")+"."+uuid.uuid4().hex
   info["CFBundleDisplayName"]="PolyU CPU test "+label
   metadata.write_bytes(plistlib.dumps(info))
   subprocess.run(["codesign","--force","--sign","-",str(copy)],check=True,capture_output=True)
   measurement_apps[label]=copy
  for trial in range(a.trials):
   for label,app in measurement_apps.items():
    app=app.resolve();exe=app/"Contents/MacOS/PolyUConsole";assert exe.is_file()
    with tempfile.TemporaryDirectory(prefix="polyu-native-smoke-") as isolated:
     root=pathlib.Path(isolated);root.chmod(0o700);nonce=str(uuid.uuid4())
     marker=root/".polyu-isolated-test.json";marker.write_text(json.dumps({"nonce":nonce}));marker.chmod(0o600)
     (root/"control").mkdir(mode=0o700)
     account=root/"app-data/account";account.mkdir(parents=True,mode=0o700);(account.parent).chmod(0o700)
     for name,value in {"app-settings.json":{"autoSync":False,"syncIntervalSeconds":3600},"config.json":{"user":{"id":"cpu-synthetic"},"courses":[]},"login.json":{"user_id":"cpu-synthetic","transport":"webkit"}}.items():
      target=account/name;target.write_text(json.dumps(value));target.chmod(0o600)
     before=processes()
     launch=subprocess.Popen([str(exe),"--isolated-test",str(root),"--isolation-nonce",nonce],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
     backend=None;native=None;pids=[]
     try:
      deadline=time.monotonic()+25
      while time.monotonic()<deadline:
       ready=list((root/"app-data").glob(".ready-*.json"))
       if ready:
        try:backend=json.loads(ready[0].read_text())["pid"]
        except FileNotFoundError:pass
       if not backend:
        backend=next((pid for pid,(parent,command) in processes().items() if parent==launch.pid and "/runtime/arm64/" in command),None)
       if backend:break
       pause(.2)
      assert backend,"Isolated native app did not start"
      pause(12)
      after=processes();native=launch.pid
      pids=[native,backend]+[pid for pid,(ppid,cmd) in after.items() if pid not in before and "/WebKit.framework/" in cmd]
      for state,duration in [("visible",a.seconds),("hidden",30)]:
       subprocess.run([str(work/"visibility"),str(native),"hide" if state=="hidden" else "show"],check=True)
       pause(2)
       start=sample(pids);at=time.monotonic();pause(duration);end=sample(pids);elapsed=time.monotonic()-at
       common=set(start)&set(end)
       if native not in common or backend not in common:raise RuntimeError("Measurement interrupted: required app/backend exited; discard this trial")
       cpu=sum(end[pid]["cpu"]-start[pid]["cpu"] for pid in common)
       row={"trial":trial+1,"version":label,"state":state,"seconds":elapsed,"cpuSeconds":cpu,"averagePercentOfOneCore":cpu/elapsed*100,"wakeups":sum(end[pid]["wakeups"]-start[pid]["wakeups"] for pid in common),"processesSampled":len(common),"backendCPUSeconds":end.get(backend,{"cpu":0})["cpu"]-start.get(backend,{"cpu":0})["cpu"]}
       rows.append(row);print(json.dumps(row),flush=True)
     finally:
      if launch.poll() is None:launch.terminate()
      try:launch.wait(timeout=10)
      except subprocess.TimeoutExpired:launch.kill();launch.wait()
      if backend:
       try:os.killpg(backend,signal.SIGTERM)
       except ProcessLookupError:pass
  medians={label:{state:statistics.median(r["cpuSeconds"] for r in rows if r["version"]==label and r["state"]==state) for state in ["visible","hidden"]} for label in ["1.8.1","1.8.2"]}
  reduction={state:1-medians["1.8.2"][state]/medians["1.8.1"][state] if medians["1.8.1"][state] else None for state in ["visible","hidden"]}
 report={"machTimebase":{"numer":timebase.numer,"denom":timebase.denom},"platform":platform.platform(),"trials":rows,"mediansCPUSeconds":medians,"reduction":reduction,"scope":"Full native AppKit app, Python backend and newly created WebKit processes. Owned program copies use unique bundle IDs to avoid installed-app collisions; isolated offline synthetic account. Visibility requested via app-owned NSRunningApplication. Same host, sequential alternating versions; OS background workload not controlled. CPU seconds and wakeups, no physical energy meter.","realAccountAccessed":False,"actualBatteryEnergyMeasured":False,"binaries":{label:hashlib.sha256((app/"Contents/MacOS/PolyUConsole").read_bytes()).hexdigest() for label,app in [("1.8.1",a.baseline),("1.8.2",a.candidate)]}}
 out=ROOT/".build/idle-cpu-182.json";out.write_text(json.dumps(report,indent=2));print("CPU comparison written: "+str(out),flush=True)
if __name__=="__main__":main()
