"""Bind all four release assets to signed native feeds and Ed25519 metadata."""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from email.utils import formatdate
from prepare_updater import prepare,LOCK
from release_build import VERSION,BUILD
REPOSITORY=LOCK["repository"]
NS="http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle",NS)
FEEDS=("appcast-macos.xml","appcast-windows-setup.xml","appcast-windows-portable.xml")
SIGNED=("updates.json","updates.json.sig",*FEEDS)

def run_sign(tool,path,only=True):
    command=[str(tool),"--disable-signing-warning"]
    seed=os.environ.get("POLYU_UPDATER_ED25519_KEY")
    if seed: command+=["--ed-key-file","-"]
    else: command+=["--account",LOCK["keyAccount"]]
    if only:command+=["-p"]
    command+=[str(path)]
    result=subprocess.run(command,input=seed or None,capture_output=True,text=True,check=True)
    return result.stdout.strip()

def generate(folder,source_sha):
    folder=pathlib.Path(folder)
    names=["PolyUStudyConsole-"+VERSION+s for s in ("-arm64.dmg","-arm64.zip","-windows-x64-setup.exe","-windows-x64.zip")]
    data={"schema":1,"repository":REPOSITORY,"version":VERSION,"build":int(BUILD),"sourceSHA":source_sha,"draft":False,"prerelease":False,"artifacts":{}}
    prefix="https://github.com/"+REPOSITORY+"/releases/download/v"+VERSION+"/"
    for name in names:
        file=folder/name
        data["artifacts"][name]={"sha256":hashlib.sha256(file.read_bytes()).hexdigest(),"bytes":file.stat().st_size,"url":prefix+name,"platform":"macos" if name.endswith(("-arm64.zip","-arm64.dmg")) else "windows","architecture":"arm64" if "-arm64" in name else "x64"}
    body=json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()
    (folder/"updates.json").write_bytes(body)
    return data

def sign(folder):
    folder=pathlib.Path(folder);tool=prepare("mac")/"bin/sign_update"
    manifest=json.loads((folder/"updates.json").read_text())
    signature=run_sign(tool,folder/"updates.json")
    (folder/"updates.json.sig").write_text(signature+"\n")
    kinds=[("appcast-macos.xml","-arm64.zip","macos"),("appcast-windows-setup.xml","-windows-x64-setup.exe","windows"),("appcast-windows-portable.xml","-windows-x64.zip","windows")]
    for name,suffix,platform in kinds:
        asset_name="PolyUStudyConsole-"+VERSION+suffix;asset=manifest["artifacts"][asset_name]
        asset_signature=run_sign(tool,folder/asset_name)
        rss=ET.Element("rss",{"version":"2.0"})
        channel=ET.SubElement(rss,"channel");ET.SubElement(channel,"title").text="PolyU Study Console"
        item=ET.SubElement(channel,"item");ET.SubElement(item,"title").text="PolyU Study Console "+VERSION
        ET.SubElement(item,"pubDate").text=formatdate(usegmt=True)
        ET.SubElement(item,"description").text="1.9.1：经用户明确同意后在本机记住学校账号密码；重新登录时填入，仍由用户手动提交并完成学校验证。"
        ET.SubElement(item,"{"+NS+"}minimumSystemVersion").text="13.0" if platform=="macos" else "10.0.19041"
        attrs={"url":asset["url"],"length":str(asset["bytes"]),"type":"application/octet-stream","{"+NS+"}version":BUILD if platform=="macos" else VERSION,"{"+NS+"}shortVersionString":VERSION,"{"+NS+"}os":platform,"{"+NS+"}edSignature":asset_signature}
        ET.SubElement(item,"enclosure",attrs)
        ET.ElementTree(rss).write(folder/name,encoding="utf-8",xml_declaration=True)
        run_sign(tool,folder/name,only=False)
    verify(folder)

def verify(folder):
    folder=pathlib.Path(folder);data=json.loads((folder/"updates.json").read_text())
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    public=Ed25519PublicKey.from_public_bytes(base64.b64decode(LOCK["publicKey"],validate=True))
    public.verify(base64.b64decode((folder/"updates.json.sig").read_text().strip(),validate=True),(folder/"updates.json").read_bytes())
    if data.get("version")!=VERSION or data.get("build")!=int(BUILD) or data.get("repository")!=REPOSITORY or data.get("draft") is not False or data.get("prerelease") is not False or not re.fullmatch(r"[0-9a-f]{40}",str(data.get("sourceSHA"))):raise ValueError("签名清单版本不匹配")
    expected_names={"PolyUStudyConsole-"+VERSION+suffix for suffix in ("-arm64.dmg","-arm64.zip","-windows-x64-setup.exe","-windows-x64.zip")}
    if set(data.get("artifacts",{}))!=expected_names:raise ValueError("签名更新资产不完整")
    for name,asset in data["artifacts"].items():
        if asset.get("url")!="https://github.com/"+REPOSITORY+"/releases/download/v"+VERSION+"/"+name:raise ValueError("签名资产 URL 无效")
        file=folder/name
        if file.stat().st_size!=asset["bytes"] or hashlib.sha256(file.read_bytes()).hexdigest()!=asset["sha256"]:raise ValueError("签名清单资产已变化")
    for name in FEEDS:
        # WinSparkle validates enclosures; additionally the downloaded manifest
        # is signed and binds version/platform/architecture and SHA256.
        body=(folder/name).read_bytes()
        signature=re.search(rb"<!-- sparkle-signatures:\s*edSignature: ([A-Za-z0-9+/=]+)\s*length: (\d+)\s*-->\s*$",body)
        if not signature or int(signature[2])!=signature.start():raise ValueError("更新源 XML 签名格式无效")
        public.verify(base64.b64decode(signature[1],validate=True),body[:signature.start()])
        xml=ET.fromstring(body)
        enclosure=xml.find(".//enclosure")
        expected_suffix={"appcast-macos.xml":"-arm64.zip","appcast-windows-setup.xml":"-windows-x64-setup.exe","appcast-windows-portable.xml":"-windows-x64.zip"}[name]
        expected_name="PolyUStudyConsole-"+VERSION+expected_suffix
        if enclosure is None or pathlib.PurePosixPath(enclosure.attrib.get("url","")).name!=expected_name:raise ValueError("更新源平台或包类型无效")
        expected_platform="macos" if name=="appcast-macos.xml" else "windows"
        expected_architecture="arm64" if expected_platform=="macos" else "x64"
        if data["artifacts"][expected_name]["platform"]!=expected_platform or data["artifacts"][expected_name]["architecture"]!=expected_architecture:raise ValueError("更新源架构无效")
        asset=next((a for a in data["artifacts"].values() if a["url"]==enclosure.attrib["url"]),None)
        if not asset:raise ValueError("更新源资产不属于正式版本")
        file=folder/pathlib.PurePosixPath(enclosure.attrib["url"]).name
        public.verify(base64.b64decode(enclosure.attrib["{"+NS+"}edSignature"],validate=True),file.read_bytes())
        if (enclosure.attrib.get("{"+NS+"}shortVersionString")!=VERSION or
            enclosure.attrib.get("{"+NS+"}version")!=(BUILD if expected_platform=="macos" else VERSION) or
            enclosure.attrib.get("{"+NS+"}os")!=expected_platform or
            enclosure.attrib.get("length")!=str(asset["bytes"])):raise ValueError("更新源版本或平台无效")
    return True

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("folder",type=pathlib.Path);parser.add_argument("--source-sha");parser.add_argument("--sign",action="store_true");parser.add_argument("--verify",action="store_true");args=parser.parse_args()
    if args.source_sha:generate(args.folder,args.source_sha)
    if args.sign:sign(args.folder)
    if args.verify:verify(args.folder)
    print("Signed release metadata and platform feeds verified." if args.sign or args.verify else "Release metadata generated.")
