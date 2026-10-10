"""Interpret assessment sections, timetable rows and notice cards on this Mac.

The unit of interpretation is a section or table row, not a keyword on one line.
PDF text, Office text and image OCR stay separate, with confidence on each row.
Every conclusion keeps its original document and page. No course IDs or dates
are built in, and no course material is sent to an external AI service.
"""
from __future__ import annotations

import re
import datetime as dt
from assessment_types import FINAL_RE, FINAL_PATTERN

SCHEDULE = re.compile(r"(?:teaching|tentative|course|exam|test|assessment)\s+(?:schedule|timetable)|\bweek\b[\s\S]{0,45}\bdate\b|教学(?:安排|日历|日程)|教學(?:安排|日曆|日程)", re.I)
NOTICE = re.compile(FINAL_PATTERN + r"|mid[\s‐‑–-]*term|written\s+exam|exam(?:ination)?\s+(?:arrangement|information|notice)|期[中末]|考试(?:安排|通知)|考試(?:安排|通知)", re.I)
INSTRUCTION = re.compile(r"closed[ -]book|open[ -]book|pencil|eraser|calculators?|turn\s+off|multiple[ -]choice|choice\s+questions|true\s*[/–-]\s*false|essay\s+questions|bring\b|闭卷|閉卷|开卷|開卷|铅笔|鉛筆|计算器|計算器|判断题|判斷題|选择题|選擇題", re.I)
COVERAGE = re.compile(r"cover(?:age|s|ed)?\b|scope\b|exam\s+content|(?:lectures?|weeks?)\s*\d+\s*(?:to|through|[-–—~])\s*\d+|考试范围|考試範圍|复习范围|溫習範圍", re.I)
PLACE = re.compile(r"(?:\b(?:location|venue|rooms?)|地[点點]|教室|考[场場])\s*[:：]\s*(.*)", re.I)
TIME = re.compile(r"(?<![\d.])(?P<h>[01]?\d|2[0-3])[:：](?P<m>[0-5]\d)(?!\d)\s*(?P<ap>[ap]\.?m\.?)?", re.I)
DUE = re.compile(r"\b(?:due\s+date|deadline|due\s+(?:on|by|at)|submit(?:ted)?\s+(?:on|by|before))\b|截止|提交日期|交[付作]", re.I)
NON_EVENT = re.compile(r"make[ -]up|practice|past\s+(?:exam|paper)|exam\s+preparation|sample\s+(?:exam|paper)|模拟|模擬|补考|補考", re.I)
ASSESSMENT_HEADER = re.compile(r"^\s*(?:specific\s+)?assessment\s+methods?(?:\s*/\s*tasks)?\b", re.I | re.M)
WEIGHT_HEADER = re.compile(r"(?:%|percentage|percent)\s*weight(?:ing)?|\bweighting\b|考核比例|评分比例|評分比例|總評|总评", re.I)
RUBRIC_HEADER = re.compile(r"(?:marking|scoring|assessment)\s+criteri|\brubric\b|评分细则|評分細則", re.I)
WEIGHT_CLAIM = re.compile(r"\b(?:weight(?:ed|ing)?|worth|contribut(?:e|es|ion)|counts?\s+(?:for|towards)|accounts?\s+for)\b|占(?:总评|總評|比|分)|比重", re.I)


def component_row(line, match):
    prefix = line[:match.start()].strip()
    prefix = re.sub(r"^\d+(?:[.)]|\s*%)?\s*", "", prefix)
    prefix = re.sub(r"^(?:continuous|specific|course)\s+assessment\s*[–—:-]?\s*(?:\d+\s*)?", "", prefix, flags=re.I)
    return not prefix or bool(re.fullmatch(r"(?:group|individual|final|written)\s*", prefix, re.I))


def cleaned(value):
    value = str(value).replace("\u00a0", " ").strip()
    return re.sub(r"^\s*[•➢❑▪●]\s*", "", value)


def readings(page):
    """Give native and OCR rows independent provenance and confidence."""
    rows = page.get("rows") or (page.get("text") or "").splitlines()
    if rows:
        scores = page.get("rowConfidences") or []
        default = 0.0 if page.get("confidenceUnknown") else 0.6 if page.get("lowConfidence") else 1.0
        yield [(cleaned(v), scores[i] if i < len(scores) and isinstance(scores[i], (int, float)) else default) for i, v in enumerate(rows)], page
    if page.get("ocrRows"):
        scores = page.get("ocrRowConfidences") or []
        default = 0.0 if page.get("ocrConfidenceUnknown") else 0.6 if page.get("ocrLowConfidence") else 0.9
        yield [(cleaned(v), scores[i] if i < len(scores) and isinstance(scores[i], (int, float)) else default) for i, v in enumerate(page["ocrRows"])], {**page, "method": "图片 OCR", "confidenceUnknown": page.get("ocrConfidenceUnknown", False)}
    if page.get("annotations"):
        yield [(cleaned(v), 1.0) for v in page["annotations"]], {**page, "locator": page.get("locator", "") + " · PDF 批注"}
    for number, table in enumerate(page.get("tables") or [], 1):
        cells = table.get("cells") or []
        labels = {}
        for header in cells[:2]:
            for column, value in enumerate(header):
                if re.fullmatch(r"(?:exam(?:ination)?\s+)?(?:time|时间|時間)", value, re.I): labels[column] = "Time"
                if re.fullmatch(r"(?:exam(?:ination)?\s+)?(?:venue|location|room|地点|地點|考场|考場)", value, re.I): labels[column] = "Location"
        content = ["\t".join((labels[j]+": "+value) if j in labels and value and value.lower() != labels[j].lower() else value for j,value in enumerate(row)) for row in cells] if cells else table.get("rows") or []
        rows = (table.get("context") or []) + content
        yield [(cleaned(v), 1.0) for v in rows], {**page, "structuredTable": True,
            "method": table.get("method") or "PDF 表格结构", "locator": page.get("locator", "") + f" · 表格 {number}"}


def join_wrapped(pairs):
    result = []
    for line, score in pairs:
        if result and ((re.search(r"(?:mid[\s‐‑–-]*term|\bgroup|\bfinal)\s*$", result[-1][0], re.I) and re.match(r"(?:test|exam(?:ination)?|quiz|presentation|report)\b", line, re.I)) or (re.search(r"activity[:：].*\band\s*$", result[-1][0], re.I) and re.match(r"(?:debates?|discussions?)\b", line, re.I))):
            previous, confidence = result.pop()
            result.append((previous + " " + line, min(confidence, score)))
        else: result.append((line, score))
    return result


def time_values(text):
    result = []
    matches = list(TIME.finditer(text))
    for i, match in enumerate(matches):
        # A duration and a mark ratio are not a time of day.
        h, m = int(match["h"]), int(match["m"])
        ap = (match["ap"] or "").replace(".", "").lower()
        following = matches[i+1] if i+1 < len(matches) else None
        separator = text[match.end():following.start()] if following else ""
        paired = following and bool(re.fullmatch(r"\s*(?:[-‐‑–—~～]+|to|至)\s*", separator, re.I))
        if paired and not ap: ap = (following["ap"] or "").replace(".", "").lower()
        if ap:
            if h > 12 or h == 0: continue
            h = h % 12 + (12 if ap == "pm" else 0)
        start = f"{h:02d}:{m:02d}"
        if paired:
            endh = int(following["h"]); endap = (following["ap"] or match["ap"] or "").replace(".", "").lower()
            if endap:
                if endh > 12 or endh == 0: continue
                endh = endh % 12 + (12 if endap == "pm" else 0)
            end = f"{endh:02d}:{int(following['m']):02d}"
            duration = endh * 60 + int(following["m"]) - (h * 60 + m)
            result.append({"time": start, "endTime": end, "timeText": start + "–" + end, "durationMinutes": duration if duration > 0 else None})
        elif i == 0 or not re.fullmatch(r"\s*(?:[-‐‑–—~～]+|to|至)\s*", text[matches[i-1].end():match.start()], re.I):
            result.append({"time": start, "endTime": "", "timeText": start})
    return result


def interpret(document, pages, config):
    from course_evidence import KIND_RE, PRACTICE_RE, GRADING_RE, assessment, dates_in, term_bounds, source_for, weight_from
    if PRACTICE_RE.search(document["title"]): return []
    text = "\n".join((p.get("text") or "") + "\n" + "\n".join(p.get("ocrRows") or []) for p in pages)
    bounds = term_bounds(config, document["title"], text)
    # ISO/yearless dates from one file keep that file's day/month convention.
    dmy = bool(re.search(r"\b(?:1[3-9]|2\d|3[01])[/.-]\d{1,2}\b", text))
    facts = []
    def emit(spec, category, page, quote, confidence=1.0, **values):
        reading = {**page, "confidence": confidence, "lowConfidence": confidence < 0.75}
        if page.get("confidenceUnknown"):
            reading["confidenceKind"] = "unavailable"
        facts.append({**spec, "category": category, "source": source_for(document, reading, quote),
            "pending": confidence < 0.75, "course": document["course"], **values})

    week_dates = {}
    for page in pages:
        for pairs, reading in readings(page):
            rows = [v for v, _ in pairs]
            full = "\n".join(rows)
            if not SCHEDULE.search(full): continue
            for line, confidence in pairs:
                week = re.match(r"\s*(?:week\s*)?(\d{1,2})(?:\s*[:.(\t ]+)", line, re.I)
                if not week: continue
                parsed = [d for d in dates_in(line, bounds, dmy) if d.get("date")]
                if len(parsed) == 1 and confidence >= .75 and 1 <= int(week[1]) <= 20:
                    week_dates.setdefault(int(week[1]), set()).add(parsed[0]["date"])
                    emit({"key": "teaching_week_"+week[1]}, "week_date", reading, line, confidence, week=int(week[1]), **parsed[0])

    for page in pages:
        for pairs, reading in readings(page):
            pairs = join_wrapped([(v, c) for v, c in pairs if v])
            rows = [v for v, _ in pairs]
            full = "\n".join(rows)
            if not full: continue
            grading = bool(WEIGHT_HEADER.search(full) or ASSESSMENT_HEADER.search(full))
            rubric = bool(RUBRIC_HEADER.search(full)) and not ASSESSMENT_HEADER.search(full)
            if rubric: grading = False
            schedule = bool(SCHEDULE.search(full) or reading.get("structuredTable") and re.search(r"\bweek\b|周次", " ".join(rows[:4]), re.I))
            role = document['type'] in {'syllabus', 'assignment', 'quiz', 'announcement', 'page'} or bool(re.search(r"outline|syllabus|schedule|intro|subject\s+brief|assignment|assessment|提纲|大纲|課程簡介|课程简介", document['title'], re.I))
            notice_heading = any(NOTICE.match(line) and len(line) < 110 for line in rows)
            if not (grading or rubric or schedule or notice_heading or role or DUE.search(full)): continue
            if grading:
                compact = []
                for line, score in pairs:
                    cells = line.split("\t")
                    number = next((n for n,cell in enumerate(cells[1:], 1) if re.fullmatch(r"\s*\d+(?:\.\d+)?(?:\s*\+\s*\d+(?:\.\d+)?)?\s*%?\s*", cell)), None)
                    compact.append(("\t".join(cells[:number+1]) if number else line, score))
                pairs, rows = compact, [v for v,_ in compact]
            hits = []
            for i, line in enumerate(rows):
                if PRACTICE_RE.search(line) or NON_EVENT.search(line): continue
                matches = list(KIND_RE.finditer(line))
                if (grading or schedule or notice_heading) and not matches: matches = list(re.finditer(r"\bexaminations?\b|\bexam\b|考试|考試", line, re.I))
                if any(re.search(r"participation|attendance|in[ -]class\s+activit", m[0], re.I) for m in matches):
                    matches = [next(m for m in matches if re.search(r"participation|attendance|in[ -]class\s+activit", m[0], re.I))]
                # A mid-term quiz is one assessment, not a midterm plus a quiz.
                elif any(re.search(r"mid|期中", m[0], re.I) for m in matches):
                    # Keep an explicitly separate final exam in a mixed heading.
                    matches = [m for m in matches if re.search(r"mid|期中", m[0], re.I) or FINAL_RE.search(m[0])]
                for match in matches:
                    spec = {**assessment(line, match), "componentRow": component_row(line, match)}
                    # Scientific experiments and generic technical "tests" are
                    # not course assessments without an academic section.
                    if spec["key"] == "test" and not (grading or schedule or re.search(r"assessment|continuous\s+assessment|in[ -]class|quiz|测[验驗]", line, re.I) or document["type"] == "quiz"):
                        continue
                    if spec["key"] == "_project" and not (grading or document["type"] == "assignment" or DUE.search(line) or re.search(r"course\s+project|assessment", full, re.I)):
                        continue
                    hits.append((i, spec))
            # "Closed-book quiz" and "mid-term quiz" on the same notice refer
            # to the same assessment, including on a requirements-only page.
            primary = next((s for i,s in hits if i < 3 and s['kind'] in {'midterm','final','test'} and NOTICE.search(rows[i])), None)
            if primary and not schedule and not grading and len(full) < 2300:
                hits = [(i, primary if s['key'] == 'test' else s) for i,s in hits]
            exam_keys = {s["key"] for _, s in hits if s["kind"] in {"midterm", "final", "test"}}
            is_notice = bool(NOTICE.search(full)) and not schedule and not grading and len(exam_keys) == 1 and len(full) < 2300
            seen = set()
            for i, spec in hits:
                line, score = pairs[i]
                identity = (i, spec["key"])
                if identity in seen: continue
                seen.add(identity)
                # Notice pages commonly put the title at the bottom and the
                # date, time and venue above it. Timetables bind within a row.
                if is_notice and spec["key"] in exam_keys:
                    block = list(range(len(rows)))
                elif schedule or grading or "\t" in line:
                    block = [i]
                else:
                    other_hits = [j for j, s in hits if s["key"] != spec["key"]]
                    lo = i
                    hi = min([j for j in other_hits if j > i] or [min(len(rows), i+8)])
                    block = list(range(lo, hi))
                combined_heading = len({s['key'] for j,s in hits if j == i}) > 1
                if combined_heading and not dates_in(line, bounds, dmy): block = [i]
                relevant = "\n".join(rows[j] for j in block)
                row_dates = []
                for j in block:
                    if NON_EVENT.search(rows[j]): continue
                    if re.search(r"\bas\s+of\b|last\s+updated|issued\s+(?:on|date)|修订|修訂", rows[j], re.I): continue
                    if spec["mode"] == "show_only": continue
                    if spec["kind"] == "assignment" and not (schedule or j == i or DUE.search(rows[j]) or re.search(r"\bdate\s*[:：]|日期", rows[j], re.I)):
                        continue
                    row_dates.extend((d, j) for d in dates_in(rows[j], bounds, dmy))
                # A separate labelled date cell may precede or follow a title.
                if not row_dates and not schedule and not combined_heading and spec["mode"] != "show_only":
                    for j in (i+1,):
                        if 0 <= j < len(rows) and len(rows[j]) < 60 and not KIND_RE.search(rows[j]) and not re.search(r"lecture|tutorial", rows[j], re.I):
                            row_dates.extend((d, j) for d in dates_in(rows[j], bounds, dmy))
                valid = [(d,j) for d,j in row_dates if d.get("date")]
                week = re.search(r"\bweek\s*(\d+)\b", relevant, re.I)
                if not valid and week and len(week_dates.get(int(week[1]), set())) == 1:
                    value = next(iter(week_dates[int(week[1])]))
                    valid = [({"date": value, "dateInferred": True, "rawDate": week[0], "week": int(week[1])}, i)]
                combined_exams = len({s['key'] for j,s in hits if j == i and s['kind'] in {'midterm','final','test'}}) > 1
                for date, j in valid or row_dates:
                    if combined_exams:
                        date = {**date, 'pending': True, 'ambiguousAssessment': True}
                    emit(spec, "date", reading, rows[j] + (" · " + line if j != i else ""), min(score, pairs[j][1]), timetable=schedule, **date)
                if not row_dates and not valid and spec["kind"] in {"midterm", "final", "test"} and (NOTICE.search(line) or grading and weight_from(line, True, spec) is not None or document["type"] == "quiz"):
                    emit(spec, "mention", reading, line, score, week=int(week[1]) if week else None)
                # A current-term notice retains stale-year discrepancies for
                # display, without treating last year's example as this exam.
                if is_notice and bounds[0]:
                    for j in block:
                        for d in dates_in(rows[j], (None, None), dmy):
                            if d.get("date") and not bounds[0] <= dt.date.fromisoformat(d["date"]) <= bounds[1]:
                                emit(spec, "historical", reading, rows[j], pairs[j][1], date=d["date"], note="该日期不属于本课程学年，未采用")
                # Details are linked to an assessment, even on a subsequent
                # requirements page that repeats its title.
                if is_notice or (schedule and len({s['key'] for j,s in hits if j == i}) == 1) or (len(hits) == 1 and not grading and not schedule):
                    for j in block:
                        detail = rows[j]
                        for value in time_values(detail): emit(spec, "detail", reading, detail, pairs[j][1], detailType="time", **value)
                        venues = list(re.finditer(r"(?:\b(?:location|venue|rooms?)|地[点點]|教室|考[场場])\s*[:：]", detail, re.I))
                        if venues:
                            place = detail[venues[-1].end():].strip(" ;；")
                            if not place and j+1 < len(rows):
                                next_venue = PLACE.search(rows[j+1])
                                if next_venue: place = next_venue[1]
                                elif not TIME.search(rows[j+1]) and not dates_in(rows[j+1], bounds, dmy): place = rows[j+1]
                            place = re.split(r"\t|\s*;\s*(?:time|date)|\s*日期", place, flags=re.I)[0]
                            if place: emit(spec, "detail", reading, detail, pairs[j][1], detailType="location", location=place[:160])
                        if INSTRUCTION.search(detail): emit(spec, "detail", reading, detail, pairs[j][1], detailType="instruction", value=detail[:450])
                        if COVERAGE.search(detail): emit(spec, "detail", reading, detail, pairs[j][1], detailType="coverage", value=detail[:450])
                weight = weight_from(line, grading, spec)
                if weight is None and grading:
                    # A wrapped label followed by a standalone weight is one
                    # table cell; never borrow another assessment's number.
                    for j in (i-1, i+1):
                        if 0 <= j < len(rows) and re.fullmatch(r"\d{1,3}(?:\.\d+)?(?:\s*\+\s*\d{1,3}(?:\.\d+)?)?\s*%?", rows[j]):
                            weight = weight_from(line + "\t" + rows[j], True, spec)
                            if weight is not None: break
                if weight is not None and (grading and spec.get('componentRow') or WEIGHT_CLAIM.search(line)) and not re.search(r"\bbonus\b|average\s+score|奖励|獎勵", line, re.I):
                    if re.search(r"including|included\s+in|其中|包含", line, re.I):
                        prior = next((s for j,s in reversed(hits) if j < i), spec)
                        emit(prior, "detail", reading, line, score, detailType="weightBreakdown", value=line)
                    else:
                        part = re.search(r"in[ -]class\s+activit(?:y|ies)\s*[:：]\s*(.+)", line, re.I)
                        label = re.sub(r"\s*\d+(?:\.\d+)?\s*%?\s*$", "", part[1]).strip() if part else None
                        emit(spec, "weight", reading, line, score, weight=weight, weightScope="breakdown" if part else "course", part=label)
                elif rubric and re.search(r"%|\bmarks?\b|\bscores?\b", line, re.I):
                    emit(spec, "detail", reading, line, score, detailType="rubric", value=line)
            # Rubric rows often say only "Scientific background 25%". Keep
            # these as criteria, never add their percentages to course totals.
            if rubric:
                rubric_spec = {"key": "rubric:" + document['id'], "title": "考核评分细则", "kind": "assignment", "mode": "show_only", "pattern": r"(?!)"}
                for line, score in pairs:
                    if re.search(r"\d+(?:\.\d+)?\s*%|\d+\s*(?:marks?|scores?|分)\b", line, re.I):
                        emit(rubric_spec, "detail", reading, line, score, detailType="rubric", value=line)
            # Labelled deliverables are useful even when they are not called
            # "assignment", e.g. "Title and group due date" on lecture slides.
            for i, line in enumerate(rows):
                if not DUE.search(line) or KIND_RE.search(line): continue
                label = DUE.split(line)[0].strip(" :–-()")
                if not label or len(label) > 100: continue
                previous = next((s for j,s in reversed(hits) if j < i and s['kind'] == 'assignment'), None)
                title_match = KIND_RE.search(document['title'])
                if previous and re.search(r"submit|submission|canvas", label, re.I):
                    spec = previous
                elif title_match and re.search(r"submit|submission|canvas", label, re.I):
                    spec = assessment(document['title'], title_match)
                    spec = {**spec, 'title': document['title'].rsplit('.',1)[0]}
                else:
                    spec = {"key": "deliverable:" + re.sub(r"\W+", "_", label.lower()).strip("_"), "title": label, "kind": "assignment", "pattern": re.escape(label), "mode": "single"}
                combined = line + " " + (rows[i+1] if i+1 < len(rows) and not KIND_RE.search(rows[i+1]) else "")
                for value in dates_in(combined, bounds, dmy): emit(spec, "date", reading, combined, pairs[i][1], **value)
    unique = {}
    for fact in facts:
        key = (fact["key"], fact["category"], fact.get("date"), fact.get("rawDate") if not fact.get("date") else "", fact.get("weight"), fact.get("detailType"), fact.get("timeText"), fact.get("location"), fact.get("value"), fact["source"]["locator"])
        if key not in unique or fact["source"]["confidence"] > unique[key]["source"]["confidence"]: unique[key] = fact
    return list(unique.values())


def document_findings(facts):
    """A useful per-file interpretation, instead of a manual-review task list."""
    findings = {}
    for fact in facts:
        if fact["category"] in {"week_date", "historical"}: continue
        row = findings.setdefault(fact["key"], {"title": fact["title"], "kind": fact["kind"], "dates": [], "weights": [], "details": [], "sources": []})
        if fact.get("date") and fact["date"] not in row["dates"]: row["dates"].append(fact["date"])
        if fact.get('weightScope') == 'breakdown':
            row['details'].append(f"{fact.get('part') or '细项'} {fact['weight']:g}%")
        elif fact.get("weight") is not None and fact["weight"] not in row["weights"]: row["weights"].append(fact["weight"])
        detail = fact.get("timeText") or fact.get("location") or fact.get("value")
        if detail and detail not in row["details"]: row["details"].append(detail)
        if fact["category"] != "mention" and fact["source"] not in row["sources"]: row["sources"].append(fact["source"])
    return list(findings.values())
