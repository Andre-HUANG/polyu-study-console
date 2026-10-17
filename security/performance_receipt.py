"""Signed independent performance evidence for the exact published commit."""
import base64,datetime as dt,json,math,statistics
from pathlib import Path
from release_build import VERSION, BASELINE_VERSION
ROOT=Path(__file__).resolve().parent.parent

def canonical(value):return json.dumps(value,sort_keys=True,separators=(",",":")).encode()

def validate(encoded,head):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from release_build import source_hashes
    try:
        envelope=json.loads(encoded);body=base64.b64decode(envelope["body"],validate=True)
        key=json.loads((ROOT/"updater-lock.json").read_text())["publicKey"]
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key,validate=True)).verify(base64.b64decode(envelope["signature"],validate=True),body)
        report=json.loads(body);age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(report["checkedAt"])).total_seconds()
        if report["measuredCandidateCommit"]!=head or report["sourceHashes"]!=source_hashes() or not 0<=age<=86400:raise ValueError()
        if set(report)!={"measuredCandidateCommit","sourceHashes","checkedAt","idleCPU","syntheticSynchronization"}:raise ValueError()
        idle=report["idleCPU"]
        if idle["realAccountAccessed"] is not False or idle["actualBatteryEnergyMeasured"] is not False:raise ValueError()
        expected={(t,v,s) for t in range(1,4) for v in (BASELINE_VERSION,VERSION) for s in ("visible","hidden")}
        rows=idle["trials"]
        if len(rows)!=12 or {(r["trial"],r["version"],r["state"]) for r in rows}!=expected:raise ValueError()
        for r in rows:
            if not all(type(r[k]) in (int,float) and math.isfinite(r[k]) and r[k]>=0 for k in ("seconds","cpuSeconds","wakeups")):raise ValueError()
            if r["seconds"]<(59 if r["state"]=="visible" else 29):raise ValueError()
        for label in ("baseline","candidate"):
            sync=report["syntheticSynchronization"][label]
            if sync["version"]!=(BASELINE_VERSION if label=="baseline" else VERSION):raise ValueError()
            trials=sync["trials"]
            if len(trials)!=6 or {(r["trial"],r["condition"]) for r in trials}!={(t,c) for t in range(1,4) for c in ("cold","warm")}:raise ValueError()
            if any(r["files"]!=46 or r["pages"]!=102 for r in trials):raise ValueError()
            for r in trials:
                if type(r["indexWrites"]) is not int or r["indexWrites"] < 0:raise ValueError()
                if not all(type(r[k]) in (int,float) and math.isfinite(r[k]) and r[k]>=0 for k in ("totalSeconds","importSeconds")):raise ValueError()
        if any(r["indexWrites"]>3 for r in report["syntheticSynchronization"]["candidate"]["trials"] if r["condition"]=="warm"):raise ValueError()
        return report
    except Exception:
        raise RuntimeError("Publication requires signed performance measurements of the exact release commit") from None

def markdown(report):
    rows=report["idleCPU"]["trials"]
    med=lambda version,state,key:statistics.median(r[key] for r in rows if r["version"]==version and r["state"]==state)
    lines=["# "+VERSION+" 最终性能实测","", "测量源码："+report["measuredCandidateCommit"],"检查时间："+report["checkedAt"],"",
        "同一台 Apple Silicon Mac，"+BASELINE_VERSION+" / "+VERSION+" 交替各三轮；使用独立的空白合成账号和唯一应用标识。包含原生窗口、后端及确认的子进程；不计无法确认归属的独立 WebKit XPC 进程，前台每轮 60 秒、隐藏每轮 30 秒。","",
        "| 状态 | "+BASELINE_VERSION+" CPU 秒中位数 | "+VERSION+" CPU 秒中位数 | 变化 |","|---|---:|---:|---:|"]
    for state,label in (("visible","请求前台"),("hidden","隐藏")):
        a,b=med(BASELINE_VERSION,state,"cpuSeconds"),med(VERSION,state,"cpuSeconds")
        reduction=100*(1-b/a) if a else 0
        lines.append("| "+label+" | "+format(a,".6f")+" | "+format(b,".6f")+" | "+("降低 " if reduction>=0 else "增加 ")+format(abs(reduction),".1f")+"% |")
    lines+=["","结果包含操作系统后台负载及窗口遮挡的影响。CPU 时间和唤醒次数不能直接换算电池耗电，未使用物理电量计。本次只报告实际观测变化，不将合成样本外推为所有账号或设备的表现。完整逐轮数值见同名 JSON。","",
        "合成同步使用 46 个文件、102 页、本地真实解析和哈希，HTTP RTT 模拟为 40 ms；每轮新建应用缓存，再测无变化复用。没有访问真实 Canvas 账号。","",
        "| 热缓存复用 | "+BASELINE_VERSION+" 中位数 | "+VERSION+" 中位数 |","|---|---:|---:|"]
    for key,label in (("totalSeconds","总时间（秒）"),("importSeconds","识别时间（秒）"),("indexWrites","索引写入次数")):
        a,b=(statistics.median(r[key] for r in report["syntheticSynchronization"][v]["trials"] if r["condition"]=="warm") for v in ("baseline","candidate"))
        lines.append("| "+label+" | "+format(a,".4f")+" | "+format(b,".4f")+" |")
    lines+=["","合成复用验收：每轮索引写入不超过 3 次。该测量证明缓存复用边界，不替代真实学校会话验收。",""]
    return "\n".join(lines)

def write_reports(folder,encoded,head):
    report=validate(encoded,head);folder=Path(folder)
    (folder/("performance-"+VERSION+".json")).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (folder/("performance-"+VERSION+".md")).write_text(markdown(report),encoding="utf-8")
