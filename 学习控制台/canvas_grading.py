"""Collect grading rules for the authenticated student's own Canvas courses.

No course-specific weights are distributed with the application. Unknown rules
and complex grading policies leave the locally calculated contribution blank.
"""
import json
import re
from pathlib import Path

from grades import finite_number
from native_canvas import HOST, save_private


def collect_grading(api, profile, config, cancelled):
    schemes, totals, notices = {}, {}, []
    user_id = str((config.get("user") or {}).get("id") or "")
    for course in config.get("courses") or []:
        if course.get("inactive"):
            continue
        cancelled()
        cid = str(course.get("id") or "")
        code = course.get("code") or cid
        if not cid.isdigit():
            continue
        try:
            info = api.get(f"/api/v1/courses/{cid}?include[]=total_scores")
            cancelled()
            groups = api.get(f"/api/v1/courses/{cid}/assignment_groups?per_page=100")
            cancelled()
            if not isinstance(info, dict) or not isinstance(groups, list):
                raise ValueError("Canvas 尚未提供评分标准")
            weighted = info.get("apply_assignment_group_weights") is True
            hidden = info.get("hide_final_grades") is True
            # Prefer only the posted score supplied by Canvas; never use final or
            # unposted scores, which can include future or concealed assessments.
            enrollments = info.get("enrollments") or []
            grade_values = set()
            periods = bool(info.get("grading_periods"))
            for enrollment in enrollments:
                if not isinstance(enrollment, dict):
                    continue
                if enrollment.get("user_id") is not None and str(enrollment["user_id"]) != user_id:
                    continue
                kind = enrollment.get("type") or enrollment.get("enrollment_type")
                if kind not in {"StudentEnrollment", "student"}:
                    continue
                periods = periods or bool(enrollment.get("has_grading_periods"))
                grade = finite_number(enrollment.get("computed_current_score"))
                if grade is None:
                    grade = finite_number((enrollment.get("grades") or {}).get("current_score"))
                if grade is not None:
                    grade_values.add(grade)
            totals[cid] = {"currentPercent": next(iter(grade_values)) if len(grade_values) == 1 and not hidden else None}
            components = []
            for group in groups:
                if not isinstance(group, dict) or group.get("id") is None:
                    continue
                rules = group.get("rules") or {}
                complex_rules = bool(rules.get("drop_lowest") or rules.get("drop_highest") or periods)
                weight = finite_number(group.get("group_weight")) if weighted else None
                if weight is not None and not 0 <= weight <= 100:
                    weight = None
                components.append({"name": group.get("name") or "考核项目", "weight": weight,
                    "mode": "group_all" if weighted and not complex_rules and not hidden else "show_only",
                    "pattern": ".*", "canvasGroupId": group["id"], "complex": complex_rules,
                    "sources": [{"documentId": f"canvas-grade:{cid}:{group['id']}", "title": "Canvas 作业组 · " + (group.get("name") or "考核项目"),
                        "url": HOST + f"/courses/{cid}/grades", "locator": "课程评分设置", "quote": f"{group.get('name') or '考核项目'}：{weight:g}%" if weight is not None else "Canvas 未启用此组的百分比权重"}]})
            note = "仅在作业组全部公布成绩且权重明确时折算；未评分项目留空。"
            if not weighted:
                note = "课程未启用作业组百分比权重；显示原始分数及 Canvas 已公布的当前成绩。"
            elif periods or any((g.get("rules") or {}).get("drop_lowest") or (g.get("rules") or {}).get("drop_highest") for g in groups if isinstance(g, dict)):
                note = "课程包含丢弃分数或分阶段评分规则；折算留空，以 Canvas 已公布的当前成绩为准。"
            if hidden:
                note = "老师尚未开放课程总成绩；总成绩及折算留空。"
            schemes[code] = {"source": "本账号 Canvas 课程的评分设置与作业组", "basis": "canvas", "hidden": hidden,
                "verified": weighted and not hidden and not periods and bool(components) and all(not c["complex"] and c["weight"] is not None for c in components) and abs(sum(c["weight"] or 0 for c in components)-100)<0.01,
                "note": note, "components": components}
        except Exception:
            cancelled()
            schemes[code] = {"source": "本账号 Canvas", "note": "暂未读取到该课程的评分设置；仅显示已公布的原始分数。", "components": []}
            notices.append(f"{code}：评分设置暂未读取")
    cancelled()
    save_private(Path(profile) / "canvas-grading-settings.json", schemes)
    save_private(Path(profile) / "canvas-course-grades.json", totals)
    return notices


def merge_document_grading(profile, document_index):
    """A document requirement is usable only as a coherent, unambiguous scheme."""
    profile = Path(profile)
    try: base = json.loads((profile / "canvas-grading-settings.json").read_text())
    except (OSError, ValueError): base = {}
    grading = (document_index.get("evidence") or {}).get("grading") or {}
    result = {}
    for course in document_index.get("courses") or []:
        code = course["code"]
        canvas = base.get(code) or {"components": [], "verified": False}
        components = [dict(c) for c in grading.get(code) or []]
        if not components:
            result[code] = {**canvas, "note": (canvas.get("note") or "") + " 未从已读课程文件识别到完整评分表；文件核对清单可查看覆盖情况。"}
            continue
        for component in components:
            pattern = re.compile(component["pattern"], re.I)
            groups = [g for g in canvas.get("components") or [] if pattern.search(g["name"])]
            # A lecture-specific test may be stored in Canvas as simply "Test".
            if not groups and component["key"].startswith("lecture_test_") and sum(c["key"].startswith("lecture_test_") or c["key"] == "test" for c in components) == 1:
                groups = [g for g in canvas.get("components") or [] if re.search(r"\btest\b", g["name"], re.I) and not re.search(r"mid|final", g["name"], re.I)]
            if len(groups) == 1:
                group = groups[0]
                component["canvasGroupId"] = group["canvasGroupId"]
                component["pattern"] = ".*"
                component["sources"] = component.get("sources", []) + group.get("sources", [])
                if group.get("weight") is not None and component.get("weight") is not None and abs(group["weight"]-component["weight"])>0.01:
                    component["conflict"] = True
                    component["alternatives"] = sorted(set(component.get("alternatives", []) + [group["weight"]]))
                    component["weight"] = None
                if group.get("complex"): component["mode"] = "show_only"
            elif len(groups) > 1 and component.get("mode") == "group_all":
                component["canvasGroupIds"] = [g["canvasGroupId"] for g in groups]
                component["pattern"] = ".*"
                component["sources"] = component.get("sources", []) + [s for g in groups for s in g.get("sources", [])]
                if any(g.get("complex") for g in groups): component["mode"] = "show_only"
                weights = [g.get("weight") for g in groups]
                if component.get("weight") is not None and all(w is not None for w in weights):
                    total = sum(weights)
                    mismatch = abs(total-component["weight"])>0.01
                    if component.get("equalItems") and len(groups) == component.get("expectedCount"):
                        mismatch = mismatch or any(abs(w-component["weight"]/len(groups))>0.01 for w in weights)
                    if mismatch:
                        component["conflict"] = True
                        component["alternatives"] = sorted(set(component.get("alternatives", []) + [total]))
            if component.get("unequalItems"):
                component["mode"] = "show_only"
                component["note"] = "文件给出不同的单项权重，尚未核对各单项的对应关系；折算留空。"
            elif component["mode"] == "group_all" and not component.get("equalItems"):
                pool_verified = len(groups) == 1 and groups[0].get("weight") is not None and groups[0].get("weight") == component.get("weight") and not groups[0].get("complex")
                if not pool_verified:
                    component["mode"] = "show_only"
                    component["note"] = "文件仅给出合计权重，尚未核对单项比例或 Canvas 作业组计算方式；折算留空。"
            if component.get("conflict"): component["weight"] = None
        conflict = any(c.get("conflict") for c in components)
        full = bool(components) and not conflict and all(c.get("weight") is not None for c in components) and abs(sum(c["weight"] for c in components)-100)<0.01
        note = "按课程文件公布的评分表折算；仅计算已出分且对应关系明确的考核，未考项目留空。"
        if conflict: note = "课程文件之间，或文件与 Canvas 作业组的权重存在冲突。保留来源和原始成绩，折算留空。"
        elif not full: note = "识别出的评分表尚未组成明确的 100% 总评标准。已知权重列出供核对，课程折算留空。"
        if canvas.get("hidden"): note = "老师尚未开放总成绩；折算留空。 " + note
        if not course.get("complete"): note += " 仍有课程内容需核对，详见文件清单。"
        result[code] = {"source": "Canvas 课程文件原文与课程评分设置", "basis": "documents", "conflict": conflict,
            "verified": full and not canvas.get("hidden"), "note": note, "components": components}
    # Expose Canvas/file comparison conflicts in the file audit as well.
    for code, scheme in result.items():
        if scheme.get("basis") == "documents":
            (document_index["evidence"]["grading"])[code] = scheme["components"]
    save_private(profile / "documents/index.json", document_index)
    save_private(profile / "grading_schemes.json", result)
    return result
