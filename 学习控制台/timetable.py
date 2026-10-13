"""Account-scoped timetable, read-only Canvas calendar and manual decisions."""
import copy
import datetime as dt
import hashlib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import academic_calendar
from native_canvas import HOST, save_private
from runtime_cache import read_json
from timetable_reader import KINDS, TYPE, kind

HK=ZoneInfo("Asia/Hong_Kong")
def stamp(): return dt.datetime.now(HK).isoformat()
def _hash(v): return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def _date(value):
    try:
        value=str(value); parsed=dt.date.fromisoformat(value)
        if value!=parsed.isoformat(): raise ValueError()
        return value
    except (ValueError,TypeError): raise ValueError("日期须为 YYYY-MM-DD")
def _time(value):
    value=str(value or "")
    if value and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",value): raise ValueError("时间须为 HH:MM")
    return value
def _text(value,n=160): return re.sub(r"[\x00-\x1f\x7f]"," ",str(value or "")).strip()[:n]

def owner(config): return str((config.get("user") or {}).get("id") or "")
def collect(api,profile,config,cancelled=lambda:None):
    """At most ten contexts per request; Canvas.get follows every Link page."""
    ids=[str(c["id"]) for c in config.get("courses",[]) if not c.get("inactive") and str(c.get("id","")).isdigit()]
    old=read_json(Path(profile)/"documents/calendar-events.json",{})
    events=[]; issues=[]; succeeded=[]
    today=dt.datetime.now(HK).date()
    start=(today-dt.timedelta(days=90)).isoformat()
    end=(today+dt.timedelta(days=365)).isoformat()
    for offset in range(0,len(ids),10):
        batch=ids[offset:offset+10]; cancelled()
        try:
            query=[("type","event"),("start_date",start),("end_date",end),("per_page","100")]
            query += [("context_codes[]","course_"+cid) for cid in batch]
            rows=api.get("/api/v1/calendar_events?"+urlencode(query))
            if not isinstance(rows,list): raise ValueError("日历响应格式不可读")
            for row in rows:
                if not isinstance(row,dict): continue
                cid=str(row.get("context_code") or "").removeprefix("course_")
                if cid not in batch or row.get("workflow_state")=="deleted": continue
                if row.get("assignment"): continue
                # Keep explicit class/exam times, never an assignment due_at.
                events.append({k:row.get(k) for k in ("id","title","start_at","end_at","all_day","location_name","location_address","context_code","html_url","description","child_events")})
            succeeded.extend(batch)
        except Exception:
            issues.extend({"courseId":cid,"message":"Canvas 课程日历暂不可用；文档识别继续，日历覆盖受限。"} for cid in batch)
    cancelled()
    if old.get("accountId")==owner(config):
        events += [e for e in old.get("events",[]) if str(e.get("context_code","")).removeprefix("course_") in ids and str(e.get("context_code","")).removeprefix("course_") not in succeeded]
    value={"schema":1,"accountId":owner(config),"checkedAt":stamp(),"start":start,"end":end,"events":events,"issues":issues}
    save_private(Path(profile)/"documents/calendar-events.json",value)
    return value

def _canvas_rules(value,courses):
    result=[]
    for row in value.get("events",[]):
        cid=str(row.get("context_code","")).removeprefix("course_")
        if cid not in courses: continue
        try:
            start=dt.datetime.fromisoformat(row["start_at"].replace("Z","+00:00"))
            if start.tzinfo is None: raise ValueError()
            start=start.astimezone(HK)
        except (ValueError,TypeError,KeyError,AttributeError): continue
        end=None
        try:
            end=dt.datetime.fromisoformat(row["end_at"].replace("Z","+00:00")).astimezone(HK)
        except (ValueError,TypeError,KeyError,AttributeError): pass
        title=_text(row.get("title")); issues=[]
        if not TYPE.search(title): issues.append("课项类型待确认")
        # Only the current student's explicit event is used; unspecific child
        # group events remain candidates and need a group decision.
        group=""
        if row.get("child_events"): issues.append("Canvas 分组安排待确认")
        url=HOST+"/calendar?event_id="+str(row.get("id") or "")
        rule={"id":"canvas:"+str(row.get("id")),"courseId":cid,"course":courses[cid].get("code") or cid,"title":title,"kind":kind(title),"group":group,"dates":[start.date().isoformat()],"repeat":None,"time":"" if row.get("all_day") else start.strftime("%H:%M"),"endTime":end.strftime("%H:%M") if end and end.date()==start.date() and not row.get("all_day") else "","location":_text(row.get("location_name")),"cancelled":False,"issues":issues,"confidence":1.0,"sources":[{"url":url,"title":title,"locator":"Canvas 课程日历","quote":title,"method":"Canvas 日历","confidence":1.0}]}
        rule["digest"]=_hash(rule);result.append(rule)
    return result

def build_rules(config,index,calendar=None):
    courses={str(c["id"]):c for c in config.get("courses",[]) if not c.get("inactive")}
    rows=[]
    for doc in index.get("records",[]):
        if str(doc.get("courseId")) in courses: rows+=doc.get("timetableRules") or []
    calendar=calendar or {}
    if calendar.get("accountId")==owner(config): rows+=_canvas_rules(calendar,courses)
    grouped={}
    for rule in rows:
        key=_hash([rule.get(k) for k in ("courseId","kind","group","dates","repeat","time","endTime","location","cancelled")])
        if key in grouped:
            target=grouped[key];target["sources"]+=rule.get("sources",[])
            target["sourceIds"]+= [rule["id"]]
            target["issues"]=list(dict.fromkeys(target.get("issues",[])+rule.get("issues",[])))
        else:
            grouped[key]=copy.deepcopy(rule);grouped[key]["sourceIds"]=[rule["id"]]
    # Reuse the established assessment evidence rules: an unresolved exam
    # date/time/venue must not become a confirmed class through a second parser.
    from course_evidence import consolidate
    evidence=index.get("evidence") or consolidate(index.get("records",[]))
    unresolved={p["id"]:p for p in evidence.get("pending",[]) if p.get("needsReview")}
    detail_conflicts=evidence.get("detailConflicts",[])
    specs={}
    for document in index.get("records",[]):
        for fact in document.get("facts") or []:
            if fact.get("kind") in {"midterm","final","test"}:
                specs[(fact["course"],fact["key"])]=fact
    for rule in grouped.values():
        if rule.get("kind")=="exam":
            for (course,key),spec in specs.items():
                if rule.get("course")!=course or not re.search(spec.get("pattern","(?!)"),rule.get("title",""),re.I):continue
                identifier=hashlib.sha256((course+":"+key).encode()).hexdigest()[:16]
                conflict=unresolved.get(identifier)
                messages=[conflict["reason"]] if conflict else []
                sources=list(conflict.get("sources",[])) if conflict else []
                for detail in detail_conflicts:
                    if detail.get("course")==course and detail.get("title")==spec["title"]:
                        messages.append("来源中的"+("时间" if detail["field"]=="time" else "地点")+"不一致")
                        sources.extend(detail.get("sources",[]))
                if messages:
                    rule["issues"]=list(dict.fromkeys(rule.get("issues",[])+messages))
                    known={(s.get("documentId"),s.get("locator"),s.get("quote")) for s in rule.get("sources",[])}
                    rule["sources"].extend(s for s in sources if (s.get("documentId"),s.get("locator"),s.get("quote")) not in known)
        confidence=max((source.get("confidence",0) for source in rule.get("sources",[])),default=rule.get("confidence",0))
        rule["confidence"]=confidence
        if confidence>=.75:rule["issues"]=[issue for issue in rule.get("issues",[]) if issue!="OCR 行置信度不足"]
        rule["digest"]=_hash([rule["digest"], rule["sources"]])
    value={"schema":1,"accountId":owner(config),"rules":list(grouped.values()),"issues":calendar.get("issues",[]),"checkedAt":calendar.get("checkedAt"),"coverage":{"start":calendar.get("start"),"end":calendar.get("end")}}
    return value

def rebuild(profile,config,index):
    value=build_rules(config,index,read_json(Path(profile)/"documents/calendar-events.json",{}))
    target=Path(profile)/"documents/timetable.json"
    if read_json(target,{})!=value: save_private(target,value)
    return value

def manual(profile,config):
    value=read_json(Path(profile)/"timetable-manual.json",{})
    return value if value.get("accountId")==owner(config) else {"schema":1,"accountId":owner(config),"entries":[],"decisions":{},"groups":{}}

def validate_rule(payload,config):
    courses={str(c["id"]):c for c in config.get("courses",[]) if not c.get("inactive")}
    cid=str(payload.get("courseId",""))
    if cid not in courses: raise ValueError("请选择已同步课程")
    event_kind=payload.get("kind")
    if event_kind not in KINDS: raise ValueError("课项类型无效")
    title=_text(payload.get("title"))
    if not title: raise ValueError("请填写课项名称")
    dates=payload.get("dates") or ([payload["date"]] if payload.get("date") else [])
    if not isinstance(dates,list) or len(dates)>366: raise ValueError("日期列表无效")
    dates=sorted({_date(d) for d in dates})
    repeat=payload.get("repeat")
    if repeat:
        if not isinstance(repeat,dict): raise ValueError("重复规则无效")
        start,end=_date(repeat.get("start")),_date(repeat.get("end"))
        if not 0<= (dt.date.fromisoformat(end)-dt.date.fromisoformat(start)).days<=730: raise ValueError("重复范围须在两年内")
        days=repeat.get("weekdays")
        if not isinstance(days,list) or not days or any(type(v) is not int or not 0<=v<=6 for v in days): raise ValueError("请选择星期")
        parity=repeat.get("parity","all")
        if parity not in {"all","odd","even"}: raise ValueError("单双周设置无效")
        numbers=repeat.get("weeks") or []
        if not isinstance(numbers,list) or any(type(v) is not int or not 1<=v<=53 for v in numbers): raise ValueError("教学周须在 1–53 范围")
        basis=repeat.get("basis")
        semester=repeat.get("semester")
        anchor=_date(repeat["anchor"]) if repeat.get("anchor") else None
        if parity!="all" or numbers:
            if not (basis=="anchor" and anchor or basis=="semester" and type(semester) is int and 1<=semester<=3): raise ValueError("请明确周次基准：第 1 周日期或官方学期教学周")
        repeat={"start":start,"end":end,"weekdays":sorted(set(days)),"parity":parity,"weeks":sorted(set(numbers)),"basis":basis,"anchor":anchor,"semester":semester}
    if not dates and not repeat: raise ValueError("请填写日期或重复安排")
    start_time,end_time=_time(payload.get("time")),_time(payload.get("endTime"))
    if end_time and (not start_time or end_time<=start_time): raise ValueError("结束时间须晚于开始时间；跨日课项请拆为两项")
    exceptions=payload.get("exceptions") or []
    if not isinstance(exceptions,list) or len(exceptions)>366: raise ValueError("停课日期无效")
    return {"courseId":cid,"course":courses[cid].get("code") or cid,"title":title,"kind":event_kind,"group":_text(payload.get("group"),40),"dates":dates,"repeat":repeat,"time":start_time,"endTime":end_time,"location":_text(payload.get("location")),"exceptions":sorted({_date(d) for d in exceptions}),"cancelled":bool(payload.get("cancelled")),"makeup":bool(payload.get("makeup")),"issues":[],"sources":[],"confidence":1.0}

def mutate(profile,config,payload):
    value=manual(profile,config);action=payload.get("action")
    if action=="save":
        rule=validate_rule(payload,config)
        identifier=str(payload.get("id") or "")
        if identifier and not any(e["id"]==identifier for e in value["entries"]): raise ValueError("手动安排不存在")
        rule["id"]=identifier or "manual:"+uuid.uuid4().hex
        rule["manual"]=True;rule["digest"]=_hash(rule)
        value["entries"]=[e for e in value["entries"] if e["id"]!=rule["id"]]+[rule]
    elif action=="delete":
        value["entries"]=[e for e in value["entries"] if e["id"]!=payload.get("id")]
    elif action=="group":
        cid=str(payload.get("courseId"))
        if cid not in {str(c["id"]) for c in config.get("courses",[])}: raise ValueError("课程不存在")
        value["groups"][cid]=_text(payload.get("group"),40)
    elif action in {"confirm","hide","reset"}:
        generated=read_json(Path(profile)/"documents/timetable.json",{})
        rule=next((e for e in generated.get("rules",[]) if e["id"]==payload.get("id") or payload.get("id") in e.get("sourceIds",[])),None)
        if not rule: raise ValueError("候选课项已变化，请刷新")
        if payload.get("digest")!=rule["digest"]:raise ValueError("来源已更新，请刷新并重新核对后再保存。")
        if action=="reset":
            for identifier in rule.get("sourceIds",[rule["id"]]): value["decisions"].pop(identifier,None)
        else:
            override=validate_rule(payload,config) if action=="confirm" else None
            if override is not None:
                override["sources"]=copy.deepcopy(rule.get("sources",[]))
                override["confidence"]=rule.get("confidence",0)
            for identifier in rule.get("sourceIds",[rule["id"]]):
                value["decisions"][identifier]={"action":action,"digest":rule["digest"],"override":override,"title":rule["title"],"ruleId":rule["id"],"sourceIds":rule.get("sourceIds",[rule["id"]])}
    else: raise ValueError("操作无效")
    save_private(Path(profile)/"timetable-manual.json",value)
    return "课程表已保存。"

def _matches(rule,date,calendar):
    if date in rule.get("exceptions",[]): return False
    if date in rule.get("dates",[]): return True
    repeat=rule.get("repeat")
    if not repeat or not repeat.get("start") or not repeat.get("end"): return False
    if not repeat["start"]<=date<=repeat["end"]: return False
    current=dt.date.fromisoformat(date)
    if current.weekday() not in repeat.get("weekdays",[]): return False
    parity=repeat.get("parity","all");numbers=repeat.get("weeks") or []
    if parity=="all" and not numbers: return True
    n=None
    if repeat.get("basis")=="anchor" and repeat.get("anchor"):
        n=(current-dt.date.fromisoformat(repeat["anchor"])).days//7+1
    elif repeat.get("basis")=="semester":
        year=academic_calendar.selected_year(calendar,current)
        n=next((w["number"] for w in (year or {}).get("weeks",[]) if w["start"]<=date<=w["end"] and w["semester"]==repeat.get("semester")),None)
    return bool(n and (not numbers or n in numbers) and (parity=="all" or n%2==(1 if parity=="odd" else 0)))

def query(profile,config,date,view="day",course=None):
    date=_date(date)
    if view not in {"day","week"}: raise ValueError("视图无效")
    generated=read_json(Path(profile)/"documents/timetable.json",{})
    if generated.get("accountId")!=owner(config): generated={}
    saved=manual(profile,config)
    calendar=academic_calendar.load(profile)
    pending=[]; active=[]; conflicts=[]; used_decisions=set()
    for original in generated.get("rules",[]):
        rule=copy.deepcopy(original);decision=next((saved["decisions"][i] for i in rule.get("sourceIds",[rule["id"]]) if i in saved["decisions"]),None)
        if decision:
            used_decisions.update(i for i in rule.get("sourceIds",[rule["id"]]) if i in saved["decisions"])
            changed=decision["digest"]!=rule["digest"]
            if changed: conflicts.append({"id":rule["id"],"title":rule["title"],"message":"来源已修订；保留你的决定，请重新核对。"})
            if decision["action"]=="hide": continue
            if decision.get("override"):
                fields={"courseId","course","title","kind","group","dates","repeat","time","endTime","location","exceptions","cancelled","makeup","issues"}
                rule.update({key:value for key,value in decision["override"].items() if key in fields});rule["manualDecision"]=True
        group=saved["groups"].get(str(rule.get("courseId")))
        if group and rule.get("group"):
            if group!=rule["group"]: continue
            rule["issues"]=[i for i in rule.get("issues",[]) if i!="请选择所属班组"]
        if rule.get("issues"):
            pending.append(rule)
        else: active.append(rule)
    orphaned=set()
    for identifier,decision in saved["decisions"].items():
        if identifier in used_decisions: continue
        original_id=decision.get("ruleId",identifier)
        if original_id in orphaned: continue
        orphaned.add(original_id)
        conflicts.append({"id":original_id,"title":decision.get("title","原课项"),"message":"来源课项已撤回或结构变化；你的决定保留，请重新核对。"})
        if decision.get("override"):
            active.append({**copy.deepcopy(decision["override"]),"id":original_id,"manualDecision":True})
    active+=copy.deepcopy(saved["entries"])
    selected=dt.date.fromisoformat(date)
    start=selected-dt.timedelta(days=selected.weekday()) if view=="week" else selected
    days=[(start+dt.timedelta(days=i)).isoformat() for i in range(7 if view=="week" else 1)]
    occurrences=[]
    cancellations=[r for r in active if r.get("cancelled")]
    for day in days:
        for rule in active:
            if rule.get("cancelled") or course and str(rule.get("courseId"))!=str(course) or not _matches(rule,day,calendar): continue
            cancelled=any(c["courseId"]==rule["courseId"] and (not c.get("group") or c.get("group")==rule.get("group")) and _matches(c,day,calendar) for c in cancellations)
            occurrences.append({**rule,"date":day,"cancelled":cancelled,"calendarHints":academic_calendar.hints(day,profile),"overlaps":[]})
    occurrences.sort(key=lambda r:(r["date"],r.get("time") or "99:99",r["course"],r["title"]))
    for i,a in enumerate(occurrences):
        if a.get("cancelled") or not a.get("time") or not a.get("endTime"): continue
        for b in occurrences[i+1:]:
            if not b.get("cancelled") and a["date"]==b["date"] and b.get("time") and b.get("endTime") and a["time"]<b["endTime"] and b["time"]<a["endTime"]:
                a["overlaps"].append(b["id"]);b["overlaps"].append(a["id"])
    if course: pending=[r for r in pending if str(r.get("courseId"))==str(course)]
    return {"date":date,"view":view,"days":days,"events":occurrences,"pending":pending,"conflicts":conflicts,"groups":saved["groups"],"coverage":generated.get("coverage",{}),"issues":generated.get("issues",[]),"checkedAt":generated.get("checkedAt")}
