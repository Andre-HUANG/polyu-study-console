"""WinSparkle 0.9.4 with fixed GitHub source and a program-only install helper."""
import base64
import ctypes
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from urllib.parse import urlsplit
from update_transaction import digest, safe_program

REPOSITORY="Andre-HUANG/polyu-study-console"
LATEST="https://github.com/"+REPOSITORY+"/releases/latest/download/"

class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,url):
        value=urlsplit(url)
        if value.scheme!="https" or value.hostname not in {"github.com","release-assets.githubusercontent.com","objects.githubusercontent.com"} or value.username or value.password or value.port not in (None,443): raise ValueError("更新跳转地址无效")
        return super().redirect_request(req,fp,code,msg,headers,url)

def public_read(name):
    if name not in {"updates.json","updates.json.sig"}: raise ValueError("更新源无效")
    with urllib.request.build_opener(PublicRedirect()).open(urllib.request.Request(LATEST+name,headers={"User-Agent":"PolyUStudyConsole updater","Accept":"application/json"},method="GET"),timeout=25) as response:
        body=response.read(1024*1024+1)
        if len(body)>1024*1024: raise ValueError("更新清单过大")
        return body

def version_tuple(version):
    if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}",str(version)): raise ValueError("版本格式无效")
    return tuple(map(int,version.split(".")))

def verify_metadata(body,signature,public_key,current=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key,validate=True)).verify(base64.b64decode(signature.strip(),validate=True),body)
    value=json.loads(body)
    if value.get("schema")!=1 or value.get("repository")!=REPOSITORY or value.get("draft") is not False or value.get("prerelease") is not False: raise ValueError("仅接受正式发布更新")
    version=version_tuple(value.get("version"))
    if current and version<=version_tuple(current): raise ValueError("更新版本须高于当前版本")
    if type(value.get("build")) is not int or value["build"]<=0: raise ValueError("构建号无效")
    prefix="https://github.com/"+REPOSITORY+"/releases/download/v"+value["version"]+"/"
    for name,item in value.get("artifacts",{}).items():
        if pathlib.PurePosixPath(name).name!=name or item.get("url")!=prefix+name or not re.fullmatch(r"[0-9a-f]{64}",str(item.get("sha256"))) or type(item.get("bytes")) is not int or not 0<item["bytes"]<=1024*1024*1024: raise ValueError("更新资产无效")
    return value

class Updater:
    def __init__(self,app):
        self.app=app
        self.bundle=pathlib.Path(__file__).resolve().parent.parent
        lock=json.loads((self.bundle/"updater-lock.json").read_text())
        self.lock=lock;self.pending=None;self.installing=False;self.ready=False
        self.mode="installed" if (self.bundle/"Uninstall.exe").is_file() or (self.bundle/"uninstall.exe").is_file() else "portable"
        self.dll=ctypes.CDLL(str(self.bundle/"updater/WinSparkle.dll"))
        self.callbacks=[]
        self._call("win_sparkle_set_app_details",[ctypes.c_wchar_p]*3,"PolyUStudyConsole","PolyU study控制台",app.backend.version if hasattr(app.backend,"version") else self.current_version())
        feed=LATEST+("appcast-windows-setup.xml" if self.mode=="installed" else "appcast-windows-portable.xml")
        self._call("win_sparkle_set_appcast_url",[ctypes.c_char_p],feed.encode())
        self._call("win_sparkle_set_eddsa_public_key",[ctypes.c_char_p],lock["publicKey"].encode())
        self._call("win_sparkle_set_automatic_check_for_updates",[ctypes.c_int],1)
        self._call("win_sparkle_set_update_check_interval",[ctypes.c_int],86400)
        self._callback("win_sparkle_set_can_shutdown_callback",ctypes.CFUNCTYPE(ctypes.c_int),self.can_shutdown)
        self._callback("win_sparkle_set_user_run_installer_callback",ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_wchar_p),self.install)
        self._callback("win_sparkle_set_shutdown_request_callback",ctypes.CFUNCTYPE(None),self.shutdown)
        self.dll.win_sparkle_init()
    def current_version(self):
        return json.loads((self.bundle/"console/release.json").read_text())["version"]
    def _call(self,name,types,*args):
        function=getattr(self.dll,name);function.argtypes=types;function.restype=None;function(*args)
    def _callback(self,name,prototype,function):
        callback=prototype(function);self.callbacks.append(callback);self._call(name,[prototype],callback)
    def check(self):
        if not self.installing: self.dll.win_sparkle_check_update_with_ui()
    def can_shutdown(self):
        try:
            if self.app.console.evaluate_js("window.polyuPrepareUpdate?.() ?? false") is not True:
                self.app.notice("请先保存或关闭课程表和安排表单，再安装更新。");return 0
            return 1
        except Exception: return 0
    def install(self,path):
        prepared=False
        try:
            self.installing=True
            body=public_read("updates.json");signature=public_read("updates.json.sig").decode()
            metadata=verify_metadata(body,signature,self.lock["publicKey"],self.current_version())
            suffix="windows-x64-setup.exe" if self.mode=="installed" else "windows-x64.zip"
            name="PolyUStudyConsole-"+metadata["version"]+"-"+suffix
            asset=metadata["artifacts"].get(name)
            if not asset or asset.get("platform")!="windows" or asset.get("architecture")!="x64" or digest(path)!=asset["sha256"] or pathlib.Path(path).stat().st_size!=asset["bytes"]: raise ValueError("版本、平台、架构或下载校验失败")
            target=safe_program(self.bundle)
            stage=pathlib.Path(tempfile.mkdtemp(prefix="PolyU-program-update-"))
            shutil.copytree(self.bundle/"runtime",stage/"runtime")
            for module in ("updater.py","update_transaction.py"): shutil.copy2(self.bundle/"WindowsApp"/module,stage/module)
            shutil.copy2(self.bundle/"updater-lock.json",stage/"updater-lock.json")
            (stage/"updates.json").write_bytes(body);(stage/"updates.json.sig").write_text(signature)
            payload=stage/name;shutil.copy2(path,payload)
            spec={"target":str(target),"mode":self.mode,"pid":os.getpid(),"payload":name,"asset":name}
            (stage/"spec.json").write_text(json.dumps(spec))
            self.pending=stage
            # Wait with no repeated full-index reads; the backend holds its
            # mutation lock throughout scanning/recognition.
            while not self.app.stopping:
                try: self.app.backend.request("update/prepare",{});prepared=True;break
                except Exception:
                    self.app.notice("更新包已校验；正在等待当前同步与识别安全完成。")
                    threading.Event().wait(2)
            if self.app.stopping: raise RuntimeError("更新已取消")
            subprocess.Popen([str(stage/"runtime/pythonw.exe"),"-I","-B","-X","utf8",str(stage/"update_transaction.py"),str(stage/"spec.json")],creationflags=0x08000000)
            self.ready=True
            return 1
        except Exception as error:
            self.installing=False
            if prepared and not self.ready and not self.app.stopping:
                try:
                    self.app.backend.stop();self.app.backend.start()
                    self.app.console.load_url(self.app.backend.bootstrap_url())
                except Exception: pass
            self.app.notice("软件更新未完成："+str(error));return -1
    def shutdown(self):
        if self.ready:
            self.app.stopping=True;self.app.backend.stop();self.app.console.destroy()
    def close(self):
        self.dll.win_sparkle_cleanup()
