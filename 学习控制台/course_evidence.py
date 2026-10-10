"""Extract traceable assessment facts; ambiguity never becomes a guessed mark."""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import re
from collections import defaultdict
from assessment_types import FINAL_RE, FINAL_PATTERN

VERSION = 8
MONTHS = {name.lower(): i for i in range(1, 13) for name in (calendar.month_name[i], calendar.month_abbr[i])}
MONTH_PATTERN = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"
KIND_RE = re.compile(r"\bmid[\s‐‑–-]*term(?:\s+(?:examination|exam|test|quiz))?\b|期中(?:考试|考試|测试|測試)?|\bwritten\s+(?:examination|exam)\b|\bfinal\s+(?:examination|exam|test)\b|期末(?:考试|考試|测试|測試)|\b(?:group|individual|final)\s+(?:project|presentation)\b|小[组組](?:项目|項目|报告|報告)|\bin[ -]class\s+activit(?:y|ies)\b|\b(?:in[ -]class\s+)?(?:quiz(?:zes)?|tests?)\b|\b(?:\d+\s+)?assignments?\b|\b(?:coursework|participation|attendance|presentation|project)\b|作[业業]|测[验驗]|考勤", re.I)
PRACTICE_RE = re.compile(r"past[ -](?:paper|exam)|mock|practice|sample\s+(?:exam|test)|往年|模拟|模擬|练习题|練習題", re.I)
KIND_RE = re.compile(KIND_RE.pattern + r"|\bmid[\s‐‑–-]*tests?\b", re.I)
KIND_RE = re.compile(KIND_RE.pattern + r"|\b(?:group|individual|final)\s+(?:report\s*/\s*presentation|presentation\s*/\s*report)\b", re.I)
# Admit hyphenated/traditional and end-of-term names before their generic test match.
KIND_RE = re.compile(FINAL_PATTERN + '|' + KIND_RE.pattern, re.I)
GRADING_RE = re.compile(r"(?:assessment|marking|grading)\s+(?:method|criteri|scheme|breakdown)|(?:%|percent|percentage)\s*(?:weight|mark)|weighting|评分|評分|考核(?:方式|比例)|成[绩績](?:计算|計算|构成|構成)", re.I)


def assessment(line, match):
    phrase = match.group(0).lower()
    lower = line.lower()
    if "mid" in phrase or "期中" in phrase:
        key, label, kind, pattern, mode = "midterm", "期中考试", "midterm", r"mid[\s‐‑–-]*(?:term|test)|期中", "single"
    elif FINAL_RE.search(phrase):
        key, label, kind, pattern, mode = 'final_exam', '期末考试', 'final', FINAL_PATTERN, 'single'
    elif "examination" in phrase or "exam" in phrase:
        key, label, kind, pattern, mode = 'written_exam', '笔试 / 考试', 'test', r'^(?!.*(?:mid[\s‐‑–-]*term|期中|final|期末)).*(?:written\s*exam|examinations?|\bexam\b)', 'single'
    elif re.search(r"project\s+(?:topic|title)|项目(?:题目|选题)|項目(?:題目|選題)", line, re.I):
        key, label, kind, pattern, mode = "project_topic", "项目选题 / 题目提交", "assignment", r"(?:project\s*)?(?:topic|title)|选题|選題|题目|題目", "single"
    elif ("project" in phrase or "项目" in phrase or "項目" in phrase) and not re.search(r"project\s+presentation", line, re.I):
        qualifier = "group" if "group" in phrase or "小组" in phrase or "小組" in phrase else "individual" if "individual" in phrase else "final" if "final" in phrase else ""
        key, label, kind = qualifier + "_project", {"group": "小组项目", "individual": "个人项目", "final": "期末项目"}.get(qualifier, "项目"), "assignment"
        pattern, mode = (r"group\s*project|小[组組].*(?:项目|項目)" if qualifier == "group" else r"individual\s*project|个人项目|個人項目" if qualifier == "individual" else r"(?:final\s*)?project|项目|項目"), "single"
    elif "participation" in phrase or "attendance" in phrase or "activit" in phrase or "考勤" in phrase:
        key, label, kind, pattern, mode = "participation", "参与及课堂表现", "assignment", r"participation|attendance|in[ -]class\s*quiz|考勤|课堂|課堂", "show_only"
    elif "presentation" in phrase or re.search(r"project\s+presentation", line, re.I):
        key, label, kind, pattern, mode = "presentation", "报告 / 展示", "assignment", r"presentation|报告|報告", "single"
    elif "assignment" in phrase or "coursework" in phrase or "作业" in phrase or "作業" in phrase:
        key, label, kind, pattern, mode = "assignments", "作业", "assignment", r"assignment|coursework|作[业業]", "group_all"
    else:
        key, label, kind, pattern, mode = "test", "阶段测试 / 测验", "test", r"(?:test|quiz)|测[验驗]|测[试試]", "single"
        lecture = re.search(r"lecture\s*(\d+)", line, re.I)
        if lecture:
            number = lecture.group(1)
            key, label = "lecture_test_" + number, "Lecture " + number + " 阶段测试"
            pattern = rf"lecture\s*{number}\b.*(?:test|quiz)|(?:test|quiz).*lecture\s*{number}\b"
        if "participation" in lower and "quiz" in lower:
            key, label, mode = "participation", "参与及课堂测验", "show_only"
    if key == "midterm":
        numbered = re.search(r"(?:mid[ -]?term\s*(?:exam(?:ination)?|test|quiz)?|期中(?:考试|考試)?)\s*(?:#|no\.?)?\s*([12]|ii|i)\b(?!\s*(?:hours?|hrs?|minutes?|mins?))", line, re.I)
        if numbered:
            number = numbered.group(1).upper(); key += "_" + number; label += " " + number
            pattern += rf".*\b{re.escape(number)}\b"
    if key == "test":
        numbered = re.search(r"\b(?:test|quiz)[ ]*(?:#|no\.?)?[ ]*([1-9]|ii|iii|iv|i)\b(?!\s*(?:hours?|hrs?|minutes?|mins?|%))", line, re.I)
        if numbered:
            number = numbered.group(1).upper()
            key += "_" + number; label += " " + number
            pattern = rf"(?:test|quiz)\s*(?:#|no\.?)?\s*{re.escape(number)}\b"
    expected = None
    equal_items = False
    unequal_items = False
    count = re.search(r"\b([2-9])\s+assignments?\b", line, re.I)
    if count and key == "assignments":
        expected = int(count.group(1))
        parts = re.search(r"(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)", line)
        equal_items = bool(parts and expected == 2 and float(parts[1]) == float(parts[2])) or bool(re.search(r"\beach\b|每", line, re.I))
        unequal_items = bool(parts and float(parts[1]) != float(parts[2]))
    if key == "assignments" and phrase.rstrip().endswith("assignment") and expected is None: mode = "single"
    return {"key": key, "title": label, "kind": kind, "pattern": pattern, "mode": mode, "expectedCount": expected, "equalItems": equal_items, "unequalItems": unequal_items}


def term_bounds(config, title, text):
    term = config.get("term") or {}
    def date(value):
        try: return dt.date.fromisoformat(str(value)[:10])
        except ValueError: return None
    start, end = date(term.get("start")), date(term.get("end"))
    if start and end and end >= start: return start, end
    label = title + " " + str(term.get("name") or "") + " " + text
    years = re.search(r"\b(20\d{2})\s*[-–—/]\s*(20\d{2}|\d{2})\b", label)
    if years:
        year = int(years.group(1)); ending = int(years.group(2)); ending += 2000 if ending < 100 else 0
        if ending == year + 1: return dt.date(year, 9, 1), dt.date(ending, 8, 31)
    if start: return start, start + dt.timedelta(days=300)
    return None, None


def dates_in(line, bounds, dmy=False):
    found = []
    occupied = []
    def add(match, year, month, day, ambiguous=False):
        if any(a < match.end() and match.start() < b for a, b in occupied): return
        occupied.append((match.start(), match.end()))
        inferred = year is None
        values = []
        if year is not None:
            try: values = [dt.date(int(year), int(month), int(day))]
            except ValueError: pass
        elif bounds[0] and bounds[1]:
            for y in range(bounds[0].year, bounds[1].year + 1):
                try:
                    candidate = dt.date(y, int(month), int(day))
                    if bounds[0] <= candidate <= bounds[1]: values.append(candidate)
                except ValueError: pass
        if len(values) == 1 and not ambiguous:
            candidate = values[0]
            # A dated historical example does not establish a current exam.
            if bounds[0] and not bounds[0] - dt.timedelta(days=30) <= candidate <= bounds[1] + dt.timedelta(days=30): return
            found.append({"date": candidate.isoformat(), "dateInferred": inferred, "rawDate": match.group(0), "offset": match.start()})
        else:
            found.append({"date": None, "dateInferred": inferred, "rawDate": match.group(0), "offset": match.start(), "uncertainDate": True, "possibleDates": [v.isoformat() for v in values]})
    for m in re.finditer(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", line): add(m, m[1], m[2], m[3])
    for m in re.finditer(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})[日号號]?", line): add(m, m[1], m[2], m[3])
    for m in re.finditer(rf"\b(\d{{1,2}})(?:\s*(?:st|nd|rd|th))?(?:\s+of\s+|\s*[-‐‑–]?\s*)({MONTH_PATTERN})\.?(?:\s*[,/‐‑–-]?\s*(20\d{{2}}))?\b", line, re.I): add(m, m[3], MONTHS[m[2].lower()], m[1])
    for m in re.finditer(rf"\b({MONTH_PATTERN})\.?\s*(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s*(20\d{{2}}))?", line, re.I): add(m, m[3], MONTHS[m[1].lower()], m[2])
    numeric_line = re.sub(r"https?://\S+", "", line)
    for m in re.finditer(r"(?<![\d.])(\d{1,2})([/.-])(\d{1,2})(?:[/.-](20\d{2}|\d{2}))?(?![\d.])", numeric_line):
        a, b = int(m[1]), int(m[3]); y = int(m[4]) if m[4] else None
        if not (1 <= a <= 31 and 1 <= b <= 31): continue
        if not y and m[2] == "." and not re.search(r"date|deadline|due|日期", line, re.I): continue
        if re.match(r"\s*(?:lectures?|weeks?|scores?|marks?|students?|groups?|people|persons?|members?|hours?|minutes?|%|kWh|MPa)\b", numeric_line[m.end():], re.I): continue
        if a > 12 and b > 12: continue
        if y and y < 100: y += 2000
        if a > 12: add(m, y, b, a)
        elif b > 12: add(m, y, a, b)
        else: add(m, y, b, a, ambiguous=not dmy and a != b)
    return found


def source_for(document, page, quote):
    return {"documentId": document["id"], "fileId": document.get("fileId"), "title": document["title"],
        "url": document["url"], "page": page.get("page"), "locator": page.get("locator") or f"第 {page.get('page', 1)} 页",
        "quote": re.sub(r"\s+", " ", quote).strip()[:700], "updatedAt": document.get("updatedAt"),
        "lowConfidence": bool(page.get("lowConfidence")), "method": page.get("method"),
        "confidence": page.get("confidence", 1.0),
        "confidenceKind": page.get("confidenceKind", "measured")}


def weight_from(line, table, spec):
    match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:\+\s*(\d{1,3}(?:\.\d+)?))?\s*%", line)
    if match:
        weight = float(match[1]) + (float(match[2]) if match[2] else 0)
        if re.search(r"\beach\b|每", line, re.I) and spec.get("expectedCount"): weight *= spec["expectedCount"]
        return weight if 0 < weight <= 100 else None
    if not table: return None
    cells = line.split("\t")
    for cell in cells[1:]:
        if re.fullmatch(r"\s*\d{1,3}(?:\.\d+)?(?:\s*\+\s*\d{1,3}(?:\.\d+)?)?\s*", cell):
            value = sum(float(v) for v in cell.split("+"))
            return value if 0 < value <= 100 else None
    match = re.search(r"(?<![\w/.])(\d{1,3}(?:\.\d+)?)(?:\s*\+\s*(\d{1,3}(?:\.\d+)?))?\s*$", line)
    if not match:
        match = re.match(r"\s*(\d{1,3}(?:\.\d+)?)(?:\s*\+\s*(\d{1,3}(?:\.\d+)?))?\s+(?=[A-Za-z\u4e00-\u9fff])", line)
    if match:
        weight = float(match[1]) + (float(match[2]) if match[2] else 0)
        return weight if 0 < weight <= 100 else None
    return None


def facts_from(document, pages, config):
    from assessment_reader import interpret
    return interpret(document, pages, config)


def consolidate(records):
    grouped = defaultdict(list)
    weeks = defaultdict(set)
    for record in records:
        for fact in record.get("facts") or []:
            if fact["category"] == "week_date":
                weeks[(fact["course"], fact["week"])].add(fact["date"])
            else: grouped[(fact["course"], fact["key"])].append(fact)
    # When the timetable names the same dated event "Test" and "Mid-term",
    # resolve the synonym using the date, rather than creating a second exam.
    for (course, key), facts in list(grouped.items()):
        if key not in {'test', 'written_exam'}: continue
        generic = {f['date'] for f in facts if f['category'] == 'date' and f.get('date') and not f.get('pending')}
        if len(generic) != 1: continue
        candidates = []
        for target_key in ('midterm', 'final_exam'):
            target_facts = grouped.get((course, target_key), [])
            dates = {f['date'] for f in target_facts if f['category'] == 'date' and f.get('date') and not f.get('pending')}
            if dates == generic: candidates.append((target_key, target_facts))
        if len(candidates) == 1:
            target_key, target_facts = candidates[0]
            target = target_facts[0]
            pattern = '(?:' + target['pattern'] + ')|(?:' + facts[0]['pattern'] + ')'
            target_facts.extend({**f, 'key': target_key, 'title': target['title'], 'kind': target['kind'], 'pattern': pattern} for f in facts)
            # All facts carry the same matching pattern after the merge.
            grouped[(course, target_key)] = [{**f, 'pattern': pattern} for f in target_facts]
            del grouped[(course, key)]
    deadlines, pending, grading, detail_conflicts = [], [], {}, []
    for (course, key), facts in grouped.items():
        date_facts = [f for f in facts if f["category"] == "date"]
        if not date_facts:
            for fact in facts:
                week = fact.get("week")
                values = weeks[(course, week)] if week else set()
                if fact["category"] == "mention" and len(values) == 1:
                    date_facts.append({**fact, "category": "date", "date": next(iter(values)), "dateInferred": True})
        reliable_dates = [f for f in date_facts if f.get("date") and not f.get("pending")]
        dates = {f["date"] for f in reliable_dates}
        weight_facts = [f for f in facts if f["category"] == "weight" and f.get("weightScope") != "breakdown"]
        parts = defaultdict(list)
        for fact in facts:
            if fact["category"] == "weight" and fact.get("weightScope") == "breakdown":
                parts[fact["source"]["documentId"]].append(fact)
        for values in parts.values():
            named = {}
            for f in values:
                label = re.sub(r"\W+", " ", f.get("part") or "").strip().lower()
                label = "quiz" if "quiz" in label else "discussion" if "discussion" in label or "debate" in label else "presentation" if "presentation" in label else label
                if label not in named or f["source"].get("confidence", 1) > named[label]["source"].get("confidence", 1): named[label] = f
            if len(named) > 1 and all(not f.get("pending") for f in named.values()):
                combined = sum(f["weight"] for f in named.values())
                if combined <= 100:
                    first = next(iter(named.values()))
                    weight_facts.append({**first, "weight": combined, "source": {**first["source"], "quote": "；".join(f["source"]["quote"] for f in named.values())[:700]}, "breakdown": [{"name": f["part"], "weight": f["weight"]} for f in named.values()]})
        reliable_weights = [f for f in weight_facts if not f.get("pending")]
        weights = {f["weight"] for f in reliable_weights or weight_facts}
        spec = facts[0]
        def sources(items):
            unique = {}
            for f in items:
                s = f["source"]; unique[(s["documentId"], s["locator"], s["quote"])] = s
            return list(unique.values())
        identifier = hashlib.sha256((course+":"+key).encode()).hexdigest()[:16]
        details = [f for f in facts if f["category"] == "detail" and not f.get("pending")]
        result_details = {"time": "", "endTime": "", "timeText": "", "location": "", "instructions": [], "coverage": [], "warnings": []}
        for field, kind in (("timeText", "time"), ("location", "location")):
            values = {f[field] for f in details if f.get("detailType") == kind and f.get(field)}
            if len(values) == 1:
                selected = next(f for f in details if f.get("detailType") == kind and f.get(field))
                if kind == "time": result_details.update({k: selected.get(k) for k in ("time", "endTime", "timeText", "durationMinutes")})
                else: result_details[field] = next(iter(values))
            elif len(values) > 1:
                result_details["warnings"].append("文件中的" + ("时间" if kind == "time" else "地点") + "不一致")
                detail_conflicts.append({"course": course, "title": spec["title"], "field": kind, "alternatives": sorted(values), "sources": sources([f for f in details if f.get("detailType") == kind])})
        result_details["instructions"] = list(dict.fromkeys(f["value"] for f in details if f.get("detailType") == "instruction"))
        result_details["coverage"] = list(dict.fromkeys(f["value"] for f in details if f.get("detailType") == "coverage"))
        old = [f for f in facts if f["category"] == "historical"]
        if old: result_details["warnings"].append("部分页面保留其他学年的日期；已采用当前学年且相互印证的安排")
        session_dates = defaultdict(set)
        for f in reliable_dates:
            if f.get('timetable'): session_dates[f['source']['documentId']].add(f['date'])
        multiple_sessions = any(len(v) > 1 and v == dates for v in session_dates.values())
        if len(dates) > 1 and spec["kind"] == "assignment" and multiple_sessions:
            for number, value in enumerate(sorted(dates), 1):
                deadlines.append({"id": identifier + ":" + value, "course": course, "key": key, "title": spec["title"] + f"（日程第 {number} 场）", "kind": spec["kind"], "date": value,
                    "weight": next(iter(weights)) if len(weights)==1 and reliable_weights else None, "pattern": spec["pattern"],
                    "sources": sources([f for f in reliable_dates if f["date"] == value] + details), "dateInferred": True, **result_details})
        elif len(dates) == 1 and reliable_dates:
            deadlines.append({"id": identifier, "course": course, "key": key, "title": spec["title"], "kind": spec["kind"],
                "date": next(iter(dates)), "weight": next(iter(weights)) if len(weights)==1 and reliable_weights else None,
                "pattern": spec["pattern"], "sources": sources(reliable_dates + details + old),
                "dateInferred": all(f.get("dateInferred") for f in reliable_dates), **result_details})
        elif date_facts or any(f["category"] == "mention" for f in facts):
            pending.append({"id": identifier, "course": course, "title": spec["title"], "kind": spec["kind"],
                "needsReview": bool(date_facts),
                "reason": "同一行包含多个考试，日期对应关系待核对" if any(f.get('ambiguousAssessment') for f in date_facts) else "当前学年文件中的日期不一致" if len(dates)>1 else "日期识别或年份存在歧义" if date_facts else "已读资料中尚未找到明确日期；后续同步会继续自动查找",
                "sources": sources(date_facts or [f for f in facts if f["category"] == "mention"]), **result_details})
        if weights:
            usable_weights = reliable_weights or weight_facts
            weight_spec = max(usable_weights, key=lambda f: f.get("expectedCount") or 0)
            counts = {f["expectedCount"] for f in usable_weights if f.get("expectedCount")}
            grading.setdefault(course, []).append({"key": key, "name": weight_spec["title"], "weight": next(iter(weights)) if len(weights)==1 and len(counts)<=1 else None,
                "alternatives": sorted(weights), "conflict": len(weights)>1 or len(counts)>1 or not reliable_weights,
                "note": "文件中的考核数量不一致；折算留空。" if len(counts)>1 else "",
                "pattern": weight_spec["pattern"], "mode": weight_spec["mode"], "expectedCount": next(iter(counts)) if len(counts)==1 else None,
                "equalItems": any(f.get("equalItems") for f in weight_facts), "unequalItems": any(f.get("unequalItems") for f in weight_facts), "sources": sources(weight_facts)})
    return {"deadlines": sorted(deadlines, key=lambda r: (r["date"], r["course"])), "pending": pending, "grading": grading, "detailConflicts": detail_conflicts}
