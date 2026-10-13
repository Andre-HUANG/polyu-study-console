"""Official, public PolyU academic calendar. Never uses the Canvas transport."""
import calendar
import copy
import datetime as dt
import hashlib
import io
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
from native_canvas import save_private
from runtime_cache import read_json

SOURCE = "https://www.polyu.edu.hk/ar/students-in-taught-programmes/academic-calendar/?sc_lang=en"
PDF = "https://www.polyu.edu.hk/ar/docdrive/polyu-students/AC.pdf"
HK = ZoneInfo("Asia/Hong_Kong")
MONTHS = {v.lower(): n for n in range(1,13) for v in (calendar.month_abbr[n], calendar.month_name[n])}
MONTHS["sept"] = 9
LOCK = threading.RLock()
MEMORY = {}
REVISION = 0
MAX_BYTES = 4 * 1024 * 1024
CHECK_DAYS = 7

def today(): return dt.datetime.now(HK).date()
def stamp(): return dt.datetime.now(dt.timezone.utc).isoformat()

def _date(day, month, year):
    month = MONTHS[month.lower()]
    return dt.date(year if month >= 8 else year+1, month, int(day))

DATE_SPAN = re.compile(r"(?P<d>\d{1,2})(?:\s*[-–]\s*(?P<d2>\d{1,2}))?\s*(?P<m>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?:\s*[-–]\s*(?P<d3>\d{1,2})\s*(?P<m2>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?))?\s*:", re.I)

def _events(text, year, holiday=False, tentative_year=None):
    text = re.sub(r"\s+", " ", text or "")
    matches = list(DATE_SPAN.finditer(text))
    result = []
    for i,m in enumerate(matches):
        title = text[m.end(): matches[i+1].start() if i+1 < len(matches) else len(text)].strip()
        start = _date(m["d"], m["m"], year)
        end = _date(m["d3"] or m["d2"] or m["d"], m["m2"] or m["m"], year)
        if end < start or not title: raise ValueError("校历日期范围无法核对")
        kind = "holiday" if holiday else "suspension" if "suspend" in title.lower() else "teaching" if "teaching" in title.lower() else "exam_period" if "examination period" in title.lower() else "add_drop" if "add/drop" in title.lower() else "revision" if "revision" in title.lower() else "assessment" if "result" in title.lower() else "event"
        scope = "evening" if "evening" in title.lower() and not re.search(r"day[- ]time|all day",title,re.I) else "all" if holiday or "suspend" in title.lower() else None
        identifier = hashlib.sha256((start.isoformat()+end.isoformat()+title).encode()).hexdigest()[:20]
        result.append({"id":identifier,"start":start.isoformat(),"end":end.isoformat(),"title":title,"kind":kind,"scope":scope,"tentative":holiday and start.year == tentative_year,"source":PDF})
    return result

def parse_pdf(data):
    import pdfplumber
    years = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            match = re.search(r"Academic Calendar\s+(20\d\d)/(\d\d)\s*\(by Semester Week\)",text,re.I)
            if not match: continue
            year = int(match[1])
            tables = page.extract_tables()
            grid = next((t for t in tables if t and "Sem. Week" in t[0] and "Events" in t[0]), None)
            if not grid: raise ValueError("校历教学周表不可读")
            header = grid[0]; event_col=header.index("Events"); holiday_col=header.index("General Holidays"); sem_col=header.index("Sem. Week")
            tentative = re.search(r"tentative for\s+(20\d\d)",text,re.I)
            events = []
            events=_events("\n".join(row[event_col] or "" for row in grid[1:]),year)
            holidays=_events("\n".join(row[holiday_col] or "" for row in grid[1:]),year,True,int(tentative[1]) if tentative else None)
            # Merged PDF cells can span many months. Preserve published order
            # and use the preceding dated event to resolve the final August.
            for series in (events,holidays):
                previous_year=year
                for event in series:
                    for field in ("start","end"):
                        date=dt.date.fromisoformat(event[field])
                        if date.year<previous_year and date.month>=8:
                            event[field]=date.replace(year=previous_year).isoformat()
                    previous_year=dt.date.fromisoformat(event["start"]).year
                    event["id"]=hashlib.sha256((event["start"]+event["end"]+event["title"]).encode()).hexdigest()[:20]
            events += holidays
            events = list({e["id"]:e for e in events}.values())
            starts = sorted((e for e in events if re.search(r"teaching commences",e["title"],re.I)),key=lambda e:e["start"])
            ends = [e["end"] for e in events if "academic year" in e["title"].lower() and "ends" in e["title"].lower()]
            if len(starts)!=3 or not ends or max(ends)<starts[0]["start"]: raise ValueError("校历学期边界未完整读取")
            origin = dt.date.fromisoformat(starts[0]["start"])
            weeks=[]
            for row in grid[1:]:
                if not str(row[1] or "").isdigit() or not str(row[sem_col] or "").isdigit(): continue
                start = origin+dt.timedelta(weeks=int(row[1])-1)
                semester = max((i+1 for i,e in enumerate(starts) if e["start"]<=start.isoformat()),default=1)
                weeks.append({"start":start.isoformat(),"end":(start+dt.timedelta(days=6)).isoformat(),"number":int(row[sem_col]),"semester":semester})
            updated = re.search(r"Updated on\s+(\d{1,2}\s+\w+\s+20\d\d)",text,re.I)
            years.append({"year":match[1]+"/"+match[2],"start":origin.isoformat(),"end":max(ends),"events":events,"weeks":weeks,"updatedAt":updated[1] if updated else None})
    if not years: raise ValueError("官方校历格式已变化，保留此前有效版本")
    return {"schema":1,"source":SOURCE,"pdfSource":PDF,"pdfSha256":hashlib.sha256(data).hexdigest(),"checkedAt":stamp(),"years":years}

class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, url):
        from urllib.parse import urlsplit
        target=urlsplit(url)
        if target.scheme!="https" or target.hostname!="www.polyu.edu.hk" or not target.path.startswith("/ar/") or target.username or target.password or target.port not in (None,443):
            raise ValueError("校历跳转地址不受支持")
        return super().redirect_request(req,fp,code,msg,headers,url)

def _fetch(url, etag=None):
    headers={"User-Agent":"PolyUStudyConsole public academic calendar","Accept":"text/html,application/pdf"}
    if etag: headers["If-None-Match"]=etag
    request=urllib.request.Request(url,headers=headers,method="GET")
    opener=urllib.request.build_opener(PublicRedirect())
    try:
        with opener.open(request,timeout=25) as response:
            data=response.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES: raise ValueError("官方校历超过读取上限")
            return data,response.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code==304: return None,etag
        raise

def base():
    return read_json(Path(__file__).with_name("academic-calendar.json"),{"schema":1,"source":SOURCE,"years":[]})

def load(profile=None):
    with LOCK:
        if profile:
            saved=read_json(Path(profile)/"calendar/official.json",{})
            if saved.get("schema")==1 and saved.get("years"): return saved
        return MEMORY.get("calendar") or base()

def clear_memory():
    global REVISION
    with LOCK:
        MEMORY.clear()
        REVISION += 1

def store_value(value, profile=None):
    global REVISION
    with LOCK:
        if profile: save_private(Path(profile)/"calendar/official.json",value)
        MEMORY["calendar"]=value
        REVISION += 1

def due(profile=None):
    value=load(profile)
    try:
        checked=dt.datetime.fromisoformat(value.get("lastAttemptAt") or value["checkedAt"])
        if checked.tzinfo is None: return True
    except (KeyError,ValueError): return True
    return (dt.datetime.now(dt.timezone.utc)-checked).total_seconds()>=CHECK_DAYS*86400

def parse_html(html):
    from canvas_web import tree
    years=[]
    for table in (n for n in tree(html).walk() if n.tag=="table"):
        events=[]; current_year=None; month=None; origin=None
        for row in table.walk():
            if row.tag!="tr": continue
            cells=[c.text().strip() for c in row.children if c.tag in {"td","th"}]
            if len(cells)==1:
                if re.fullmatch(r"20\d\d",cells[0]): current_year=int(cells[0])
                elif cells[0].lower() in MONTHS: month=MONTHS[cells[0].lower()]
            elif len(cells)>=3 and current_year and month:
                m=re.fullmatch(r"(\d{1,2})(?:[-–](\d{1,2}))?",cells[0])
                if not m: continue
                start=dt.date(current_year,month,int(m[1])).isoformat()
                end=dt.date(current_year,month,int(m[2] or m[1])).isoformat()
                title=cells[2]
                event=_events(f"{m[1]} {calendar.month_abbr[month]}: {title}",current_year if month>=8 else current_year-1, "general holiday" in title.lower())[0]
                event.update(start=start,end=end,source=SOURCE,id=hashlib.sha256((start+end+title).encode()).hexdigest()[:20])
                events.append(event)
                if "semester one teaching commences" in title.lower(): origin=start
        if not origin: continue
        ending=next((e["end"] for e in events if "academic year" in e["title"].lower() and "ends" in e["title"].lower()),None)
        if not ending: raise ValueError("官方网页学年边界不完整")
        # Show the whole published registration/revision/exam interval.
        for event in list(events):
            if "commences" not in event["title"].lower() or event["kind"] not in {"add_drop","exam_period","revision"}: continue
            prefix=re.sub(r"commences.*","",event["title"],flags=re.I).strip().lower()
            final=next((e for e in events if re.sub(r"ends.*","",e["title"],flags=re.I).strip().lower()==prefix and "ends" in e["title"].lower()),None)
            if final:
                event["end"]=final["end"]; event["title"]=re.sub(r"\s+commences.*","",event["title"],flags=re.I)
                events.remove(final)
        year=origin[:4]+"/"+str(int(origin[:4])+1)[2:]
        years.append({"year":year,"start":origin,"end":ending,"events":events})
    if not years: raise ValueError("官方网页校历格式未能读取")
    return years

def merge_web_year(prior,web):
    """Web revisions own event dates; retain authoritative PDF column semantics."""
    if prior["start"]!=web["start"]:raise ValueError("网页学期边界与教学周表存在差异")
    def title_key(title):return re.sub(r"\s+"," ",title or "").strip().casefold()
    previous=prior.get("events",[])
    merged=[]
    for row in web.get("events",[]):
        event=copy.deepcopy(row)
        matches=[p for p in previous if title_key(p["title"])==title_key(row["title"])]
        exact=[p for p in matches if p["start"]==row["start"] and p["end"]==row["end"]]
        source=exact[0] if len(exact)==1 else matches[0] if len(matches)==1 else None
        if source and source.get("kind")=="holiday":
            event.update(kind="holiday",scope=source.get("scope") or "all",tentative=source.get("tentative",False),classificationSource=PDF)
        merged.append(event)
    # A PDF holiday omitted from the web table still carries its official scope.
    names={title_key(e["title"]) for e in merged}
    merged += [copy.deepcopy(p) for p in previous if p.get("kind")=="holiday" and title_key(p["title"]) not in names]
    prior.update({**web,"events":merged})
    return prior

def refresh(profile=None, cancelled=lambda:None, persist_guard=lambda:True, commit=None):
    previous=load(profile)
    def publish(value):
        cancelled()
        if commit: commit(value)
        elif persist_guard(): store_value(value,profile)
        return value
    try:
        html,_=_fetch(SOURCE)
        cancelled()
        web_years=parse_html((html or b"").decode("utf-8"))
        data,etag=_fetch(PDF,previous.get("etag") if previous.get("calendarInterpreterVersion")==2 else None)
        value=copy.deepcopy(previous) if data is None or previous.get("calendarInterpreterVersion")==2 and hashlib.sha256(data).hexdigest()==previous.get("pdfSha256") else parse_pdf(data)
        by_year={y["year"]:y for y in value["years"]}
        for web in web_years:
            prior=by_year.get(web["year"])
            if not prior: raise ValueError("新学年教学周表尚未能核对")
            merge_web_year(prior,web)
        value.update(calendarInterpreterVersion=2,checkedAt=stamp(),lastAttemptAt=stamp(),etag=etag,pageSha256=hashlib.sha256(html or b"").hexdigest(),issue="")
        return publish(value)
    except Exception as e:
        cancelled()
        value=copy.deepcopy(previous)
        value["issue"]="校历刷新未完成；继续显示此前有效版本："+type(e).__name__
        value["lastAttemptAt"]=stamp()
        return publish(value)

def selected_year(value, date=None, year=None):
    date=(date or today()).isoformat()
    return next((y for y in value.get("years",[]) if y["year"]==year),None) if year else next((y for y in value.get("years",[]) if y["start"]<=date<=y["end"]),None)

def month_summary(profile=None, year=None, month=None):
    value=load(profile)
    month=month or today().strftime("%Y-%m")
    if not re.fullmatch(r"20\d\d-(?:0[1-9]|1[012])",month): raise ValueError("月份无效")
    first=dt.date.fromisoformat(month+"-01"); last=first.replace(day=calendar.monthrange(first.year,first.month)[1])
    selected=selected_year(value,date=today() if month==today().strftime("%Y-%m") else first,year=year)
    if year and not selected: raise ValueError("学年尚未发布")
    if not selected: selected=next((y for y in value.get("years",[]) if y["start"]<=last.isoformat() and y["end"]>=first.isoformat()),None)
    events=[e for e in (selected or {}).get("events",[]) if e["start"]<=last.isoformat() and e["end"]>=first.isoformat()]
    week=next((w for w in (selected or {}).get("weeks",[]) if w["start"]<=today().isoformat()<=w["end"]),None)
    return {"year":(selected or {}).get("year"),"years":[y["year"] for y in value.get("years",[])],"month":month,"events":events,"teachingWeek":week,
            "source":SOURCE,"updatedAt":(selected or {}).get("updatedAt"),"checkedAt":value.get("checkedAt"),"issue":value.get("issue") or ("" if selected else "此月份的官方校历尚未发布。"),"offline":not bool(value.get("years"))}

def hints(date, profile=None):
    value=load(profile)
    year=selected_year(value,dt.date.fromisoformat(date))
    return [e for e in (year or {}).get("events",[]) if e["start"]<=date<=e["end"] and e["scope"]]
