"""Present Canvas grades against verified course assessment weights.

An ungraded assessment is never treated as zero. Aggregate components remain
unweighted until Canvas exposes a final mark for the whole component.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path


SCHEMES = Path(__file__).with_name("grading_schemes.json")


def finite_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def published_grade(assignment):
    score = finite_number(assignment.get("score"))
    points = finite_number(assignment.get("points"))
    if assignment.get("sub_state") != "graded" or score is None or points is None or points <= 0:
        return None
    return score, points


def grade_item(aid, assignment, safe_url):
    grade = published_grade(assignment)
    if grade is None:
        return None
    score, points = grade
    return {
        "id": str(aid), "course": assignment.get("course") or "",
        "title": assignment.get("name") or "未命名评分",
        "score": score, "points": points,
        "at": assignment.get("graded_at") or assignment.get("posted_at"),
        "url": safe_url(assignment.get("html_url")),
    }


def component_data(spec, rows, safe_url, allow_weight, raw_assignment_meta):
    pattern = re.compile(spec["pattern"], re.IGNORECASE)
    group_id = spec.get("canvasGroupId")
    group_ids = spec.get("canvasGroupIds")
    matches = [(aid, row) for aid, row in rows if pattern.search(row.get("name") or "") and (
        group_id is None or raw_assignment_meta.get(aid, {}).get("assignment_group_id") == group_id
    ) and not raw_assignment_meta.get(aid, {}).get("omit_from_final_grade")
      and (group_ids is None or raw_assignment_meta.get(aid, {}).get("assignment_group_id") in group_ids)
      and not raw_assignment_meta.get(aid, {}).get("excused")]
    graded = [item for aid, row in matches if (item := grade_item(aid, row, safe_url))]
    score = sum(item["score"] for item in graded) if graded else None
    points = sum(item["points"] for item in graded) if graded else None
    weight = finite_number(spec.get("weight"))
    mode = spec.get("mode")
    expected = spec.get("expectedCount")
    complete = bool(matches) and len(graded) == len(matches)
    if mode == "single":
        complete = complete and len(matches) == 1
    elif mode != "group_all":
        complete = False
    if expected:
        complete = complete and len(matches) == expected
    allow_weight = allow_weight and not spec.get("conflict") and not spec.get("complex")
    weighted = round(weight * score / points, 3) if (
        allow_weight and complete and weight is not None and score is not None and points
    ) else None
    assessed_weight = weight if weighted is not None else None
    if allow_weight and mode == "group_all" and not spec.get("complex") and spec.get("equalItems") and expected and len(matches) <= expected and weight is not None and graded:
        weighted = round(sum(item["score"] / item["points"] * weight / expected for item in graded), 3)
        assessed_weight = weight / expected * len(graded)
    submitted = any(row.get("sub_state") in {"submitted", "pending_review"} for _, row in matches)
    return {
        "name": spec["name"], "weight": weight, "score": score, "points": points,
        "weighted": weighted, "gradedCount": len(graded), "itemCount": len(matches),
        "assessedWeight": assessed_weight, "expectedCount": expected,
        "sources": spec.get("sources") or [], "conflict": bool(spec.get("conflict")), "alternatives": spec.get("alternatives") or [],
        "note": spec.get("note") or "",
        "status": "graded" if weighted is not None and complete else "partial" if graded else "submitted" if submitted else "pending",
        "items": graded, "matchedIds": [str(aid) for aid, _ in matches],
    }


def build_grades(courses, assignments, safe_url, raw_assignment_meta=None, schemes_path=None, course_totals=None):
    raw_assignment_meta = raw_assignment_meta or {}
    course_totals = course_totals or {}
    try:
        schemes = json.loads(Path(schemes_path or SCHEMES).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        schemes = {}
    all_rows = [(str(aid), row) for aid, row in (assignments or {}).items() if isinstance(row, dict)]
    recent = [item for aid, row in all_rows if (item := grade_item(aid, row, safe_url))]
    recent.sort(key=lambda item: item["at"] or "", reverse=True)

    course_rows = []
    for course in courses:
        code = course.get("code")
        rows = [(aid, row) for aid, row in all_rows if row.get("course") == code]
        scheme = schemes.get(code) or {}
        conflict = bool(scheme.get("conflict"))
        allow = not conflict and scheme.get("verified", True)
        components = [component_data(spec, rows, safe_url, allow, raw_assignment_meta)
                      for spec in scheme.get("components") or []]
        ids = [aid for c in components for aid in c["matchedIds"]]
        mapping_conflict = len(ids) != len(set(ids))
        if mapping_conflict:
            components = [component_data(spec, rows, safe_url, False, raw_assignment_meta) for spec in scheme.get("components") or []]
        matched = {aid for component in components for aid in component.pop("matchedIds")}
        other_grades = [item for aid, row in rows if aid not in matched
                        and (item := grade_item(aid, row, safe_url))]
        weighted_components = [item for item in components if item["weighted"] is not None]
        earned = round(sum(item["weighted"] for item in weighted_components), 3) if weighted_components else None
        assessed_weight = sum(item["assessedWeight"] for item in weighted_components) if weighted_components else None
        graded_components = [item for item in components if item["score"] is not None]
        preview_raw = None
        if len(graded_components) == 1 and not other_grades:
            item = graded_components[0]
            preview_raw = {"label": item["name"], "score": item["score"], "points": item["points"]}
        elif not graded_components and len(other_grades) == 1:
            item = other_grades[0]
            preview_raw = {"label": item["title"], "score": item["score"], "points": item["points"]}
        course_rows.append({
            "code": code, "name": course.get("name") or "", "source": scheme.get("source") or "",
            "sourceFile": scheme.get("sourceFile") or "", "note": scheme.get("note") or (
                "Canvas 尚无可核对的课程总评权重；仅显示已公布的原始分数。" if not scheme else ""),
            "conflict": conflict or mapping_conflict, "basis": scheme.get("basis") or "documents", "verified": bool(allow and not mapping_conflict),
            "mappingNote": "同一 Canvas 评分同时对应多个考核项，折算留空。" if mapping_conflict else "",
            "components": components, "otherGrades": other_grades,
            "gradedCount": sum(bool(published_grade(row)) for _, row in rows),
            "earned": earned, "assessedWeight": assessed_weight,
            "previewRaw": preview_raw,
            "canvasCurrentPercent": finite_number((course_totals.get(str(course.get("id"))) or {}).get("currentPercent"))
                if any(published_grade(row) for _, row in rows) else None,
            "currentPercent": round(earned / assessed_weight * 100, 2) if assessed_weight else None,
        })
    return {"recent": recent[:10], "courses": course_rows}
