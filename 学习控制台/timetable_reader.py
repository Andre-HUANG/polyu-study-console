"""Conservative classroom/exam schedule reader with original row evidence."""
import datetime as dt
import hashlib
import json
import re
from assessment_reader import readings, time_values, PLACE
from course_evidence import dates_in, source_for, term_bounds

VERSION = 3
KINDS = {"lecture": "讲课", "lab": "实验", "tutorial": "辅导课", "final": "期末考试", "midterm": "期中考试", "test": "测验", "exam": "考试（未分类）"}
EXAM_KINDS = {"final", "midterm", "test", "exam"}
TYPE = re.compile(r"\b(?:lectures?|classes?|sessions?|labs?|laborator(?:y|ies)|tutorials?|seminars?|exam(?:ination)?s?|tests?|quizzes?|mid[- ]?terms?)\b|讲课|講課|课堂|課堂|实验|實驗|辅导|輔導|考试|考試|测验|測驗", re.I)
DAYS = re.compile(r"\b(mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b|(?:星期|周|週)([一二三四五六日天])", re.I)
GROUP = re.compile(r"\b(?:group|section|class|班组|班組)\s*[:：#]?\s*([A-Z]+\d*|\d+)\b", re.I)
DAY_NUM = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
CN_NUM = {"一":0,"二":1,"三":2,"四":3,"五":4,"六":5,"日":6,"天":6}

def kind(text):
    mid = bool(re.search(r"mid[\s‐‑–-]*term|期中",text,re.I))
    final = bool(re.search(r"\bfinal\s+(?:exam(?:ination)?|test)|期末(?:考试|考試|测试|測試)",text,re.I))
    if mid and final: return "test"
    if mid: return "midterm"
    if final: return "final"
    if re.search(r"exam|test|quiz|考试|考試|测验|測驗",text,re.I): return "test"
    if re.search(r"\blabs?\b|laborator|实验|實驗",text,re.I): return "lab"
    if re.search(r"tutorial|辅导|輔導",text,re.I): return "tutorial"
    return "lecture"

def weekdays(text):
    return sorted({DAY_NUM[m[1].lower()[:3]] if m[1] else CN_NUM[m[2]] for m in DAYS.finditer(text)})

def weeks(text):
    m = re.search(r"(?:weeks?|教学周|教學週|第)\s*([\d\s,，、–—-]+)(?:周|週)?",text,re.I)
    if not m: return []
    result=set()
    for a,b,c in re.findall(r"(\d+)\s*[-–—]\s*(\d+)|(\d+)",m[1]):
        if c: result.add(int(c))
        elif 1<=int(a)<=int(b)<=53: result.update(range(int(a),int(b)+1))
    return sorted(n for n in result if 1<=n<=53)

def rules_from(document,pages,config):
    full="\n".join(p.get("text","") for p in pages)
    bounds=term_bounds(config,document.get("title",""),full)
    dmy=bool(re.search(r"dd[/.-]mm|day.?month.?year|日[/.-]月",full,re.I))
    result={}
    for page in pages:
        for pairs,reading in readings(page):
            context=""
            ordinals={}
            for i,(line,confidence) in enumerate(pairs):
                if re.search(r"timetable|teaching schedule|class schedule|课程表|課程表|教学安排|教學安排",line,re.I): context=line
                if TYPE.search(line) and not dates_in(line,bounds,dmy) and not time_values(line) and len(line)<120: context=line
                scope=line+" "+context
                if not TYPE.search(scope): continue
                # Lecture notes can contain scientific test dates and standards.
                # A carried heading does not turn arbitrary prose into a schedule.
                if not TYPE.search(line) and (len(line)>140 or re.search(r"doi:|https?://|\b(?:published|reference|results?|samples?|concentrations?)\b",line,re.I)): continue
                if re.search(r"\b(?:ISO|ASTM|DOI|concentration|specimens?|measured|blood tests?|clinical trials?)\b",line,re.I) and not re.search(r"\b(?:timetable|held|scheduled|starts?|classroom|room|venue)\b|上课|上課",line,re.I): continue
                # A date/venue on the next row belongs only to a short explicit
                # class/exam heading, not to an arbitrary paragraph.
                if TYPE.search(line) and len(line)<160 and not dates_in(line,bounds,dmy) and not weekdays(line):
                    for following,score in pairs[i+1:i+4]:
                        if TYPE.search(following): break
                        if dates_in(following,bounds,dmy) or time_values(following) or PLACE.search(following):
                            scope+=" · "+following; confidence=min(confidence,score)
                dates=dates_in(scope,bounds,dmy)
                times=time_values(scope)
                days=weekdays(scope)
                if not dates and not (days and times): continue
                # Submission times do not establish when a class/exam starts.
                if re.search(r"deadline|submission|submit by|due(?:\s+date)?|截止|提交",scope,re.I) and not re.search(r"exam.*(?:starts?|held|time)|考试时间|考試時間",scope,re.I): continue
                event_kind=kind(scope)
                group_match=GROUP.search(scope)
                groups=[m[1].upper() for m in GROUP.finditer(scope)]
                issues=[]
                if confidence<.75: issues.append("OCR 行置信度不足")
                if len(set(groups))>1: issues.append("班组存在多个候选")
                parsed_dates=sorted({d["date"] for d in dates if d.get("date")})
                if any(not d.get("date") for d in dates): issues.append("日期或年份待确认")
                if len(times)>1: issues.append("同一行存在多个时间段")
                value=times[0] if times else {}
                place=PLACE.search(scope)
                location=re.split(r"\t| · |;",place[1])[0].strip()[:160] if place else ""
                if not location and "\t" in line:
                    cells=line.split("\t")
                    candidate=cells[-1].strip()
                    if re.fullmatch(r"[A-Z]{1,5}[ -]?\d{2,5}(?:[-/]\w+)?",candidate): location=candidate
                repeat=None
                if days and not parsed_dates:
                    period=dates_in(full,bounds,dmy)
                    explicit=[d["date"] for d in period if d.get("date")]
                    # Canvas term boundaries are permissible coverage bounds;
                    # no implicit week-one definition is derived from them.
                    start=bounds[0].isoformat() if bounds[0] else None
                    end=bounds[1].isoformat() if bounds[1] else None
                    parity="odd" if re.search(r"odd.?weeks?|单周|單週",scope,re.I) else "even" if re.search(r"even.?weeks?|双周|雙週",scope,re.I) else "all"
                    selected_weeks=weeks(scope)
                    basis="semester" if re.search(r"semester\s*(?:teaching\s*)?weeks?|学期教学周|學期教學週",scope,re.I) else None
                    semester=re.search(r"(?:semester|sem\.?|学期|學期)\s*([123])",full,re.I)
                    anchor=None
                    for a in re.finditer(r"(?:week\s*1|第\s*1\s*周)\s*[:：]?([^\n]{0,60})",full,re.I):
                        anchor_dates=dates_in(a[1],bounds,dmy)
                        if len(anchor_dates)==1 and anchor_dates[0].get("date"): anchor=anchor_dates[0]["date"]; basis="anchor"
                    repeat={"weekdays":days,"start":start,"end":end,"parity":parity,"weeks":selected_weeks,"basis":basis,"semester":int(semester[1]) if semester else None,"anchor":anchor}
                    if not start or not end: issues.append("重复安排的起止日期待确认")
                    if (parity!="all" or selected_weeks) and not (anchor or basis=="semester" and semester): issues.append("教学周定义待确认")
                if group_match: issues.append("请选择所属班组")
                if not TYPE.search(line) and len(line.split())>16:
                    issues.append("日期行与课项的关联待确认")
                source=source_for(document,{**reading,"confidence":confidence,"lowConfidence":confidence<.75},scope)
                # Position among actual schedule rows is stable when unrelated
                # prose is inserted. Changes to dates/times keep the decision
                # attached and are surfaced through the source digest.
                identity=(event_kind,group_match[1].upper() if group_match else "")
                ordinal=ordinals.get(identity,0);ordinals[identity]=ordinal+1
                stable=hashlib.sha256(json.dumps([document["id"],reading.get("page"),reading.get("locator"),ordinal,*identity],ensure_ascii=False).encode()).hexdigest()[:24]
                rule={"id":stable,"courseId":str(document.get("courseId","")),"course":document.get("course",""),"title":(line if TYPE.search(line) else context + " · " + line)[:160],"kind":event_kind,"group":group_match[1].upper() if group_match else "","dates":parsed_dates,"repeat":repeat,"time":value.get("time",""),"endTime":value.get("endTime",""),"location":location,"cancelled":bool(re.search(r"cancelled|canceled|no (?:class|lecture|lab|tutorial)|停课|停課|取消",scope,re.I)),"makeup":bool(re.search(r"make.?up|补课|補課",scope,re.I)),"issues":list(dict.fromkeys(issues)),"sources":[source],"confidence":confidence}
                rule["digest"]=hashlib.sha256(json.dumps(rule,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                key=json.dumps([rule[k] for k in ("courseId","kind","group","dates","repeat","time","endTime","location","cancelled")],sort_keys=True)
                if key in result: result[key]["sources"].append(source)
                else: result[key]=rule
    return list(result.values())
