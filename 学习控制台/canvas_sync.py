"""Collect current courses and assignments without a weekly-plan engine."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from urllib.parse import quote

from native_canvas import HOST, save_private
from change_detection import changes as observed_changes


def read_json(path, fallback):
    import json
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def current_courses(api, today=None):
    # PolyU can expose a course in the student's web dashboard while its API
    # listing is unavailable to a browser session. Read the visible cards first.
    try:
        from canvas_web import all_courses, course_code_from_home, dashboard_courses, html_page
        dashboard = html_page(api, "/")
        try:
            rows = all_courses(html_page(api, "/courses"), dashboard)
            for row in rows:
                if row["code"] == row["id"]:
                    try:
                        row["code"] = course_code_from_home(html_page(api, f"/courses/{row['id']}"), row["id"]) or row["id"]
                    except Exception:
                        row["codeIncomplete"] = True
            return rows
        except Exception:
            return [{**row, "rosterIncomplete": True} for row in dashboard_courses(dashboard)]
    except Exception:
        pass
    today = today or dt.datetime.now(dt.timezone.utc).date()
    rows = api.get("/api/v1/courses?enrollment_state=active&include[]=term&per_page=100")
    if not isinstance(rows, list):
        raise RuntimeError("Canvas 课程列表暂不可读。")
    courses, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        term = row.get("term") or {}
        end = term.get("end_at") or row.get("end_at")
        if end:
            try:
                if dt.datetime.fromisoformat(end.replace("Z", "+00:00")).date() < today:
                    continue
            except ValueError:
                pass
        cid = str(row["id"])
        code = str(row.get("course_code") or cid).strip()
        code = re.sub(r"\s+", " ", code)
        if code in seen:
            code = f"{code} ({cid})"
        seen.add(code)
        courses.append({"id": row["id"], "code": code, "name": row.get("name") or code,
                        "term": term.get("name") or "", "start": term.get("start_at") or row.get("start_at"),
                        "end": end, "rosterIncomplete": bool(row.get("rosterIncomplete"))})
    return courses


def collect(api, profile, me, cancelled=lambda: None, progress=lambda stage: None):
    profile = Path(profile)
    old_config = read_json(profile / "config.json", {})
    old_snapshot = read_json(profile / "snapshot.json", {})
    old_meta = read_json(profile / "assignment-meta.json", {})
    old_announcements = read_json(profile / "announcements.json", [])
    courses = current_courses(api)
    if not courses and old_config.get("courses"):
        raise RuntimeError("Canvas 当前课程列表为空；沿用已保存的课程并在下次同步重试。")
    terms = [row["term"] for row in courses if row["term"]]
    term_name = max(set(terms), key=terms.count) if terms else ""
    term_rows = [row for row in courses if row["term"] == term_name] if term_name else courses
    starts = [row["start"] for row in term_rows if row["start"]]
    ends = [row["end"] for row in term_rows if row["end"]]
    config = {"canvas_host": HOST, "user": {"id": me["id"], "name": me.get("name") or "PolyU 学生"},
              "term": {"name": term_name, "start": min(starts) if starts else None,
                       "end": max(ends) if ends else None},
              "courses": courses}
    previous_id = (old_config.get("user") or {}).get("id")
    if previous_id is not None and str(previous_id) != str(me["id"]):
        raise RuntimeError("Canvas 登录账号与本机档案不一致，请先退出登录。")
    save_private(profile / "config.json", config)
    assignments, metadata, announcements, warnings = {}, {}, [], []
    if any(c.get("rosterIncomplete") for c in courses):
        warnings.append("全部课程页面尚未完整读取；当前仅含已发现的课程")
    old_assignments = old_snapshot.get("assignments") or {}
    for course in courses:
        cancelled()
        cid, code = str(course["id"]), course["code"]
        progress(f"正在同步 {code} 的作业与公告…")
        try:
            rows = api.get(f"/api/v1/courses/{quote(cid)}/assignments?include[]=submission&per_page=100")
            if not isinstance(rows, list):
                raise ValueError("作业接口返回内容无效")
            for row in rows:
                if not isinstance(row, dict) or row.get("id") is None:
                    continue
                aid = str(row["id"])
                submission = row.get("submission") or {}
                status = submission.get("workflow_state") or ""
                visible_grade = status == "graded" and not submission.get("grade_hidden") and submission.get("score") is not None
                sub_state = "graded" if visible_grade else "submitted" if status in {"submitted", "graded", "pending_review"} or submission.get("submitted_at") else "unsubmitted"
                assignments[aid] = {"course": code, "name": row.get("name") or "未命名作业",
                    "html_url": row.get("html_url") or HOST + f"/courses/{cid}/assignments/{aid}",
                    "due_at": row.get("due_at"), "lock_at": row.get("lock_at"),
                    "unlock_at": row.get("unlock_at"), "updated_at": row.get("updated_at"),
                    "is_quiz": bool(row.get("quiz_id")), "points": row.get("points_possible"),
                    "weight": "—", "sub_state": sub_state, "score": submission.get("score") if visible_grade else None,
                    "submitted_at": submission.get("submitted_at"),
                    "graded_at": submission.get("graded_at") if visible_grade else None,
                    "posted_at": submission.get("posted_at") if visible_grade else None}
                metadata[aid] = {"assignment_group_id": row.get("assignment_group_id"),
                    "omit_from_final_grade": bool(row.get("omit_from_final_grade")),
                    "excused": bool(submission.get("excused"))}
        except Exception as error:
            warnings.append(f"{code} 作业暂未完整同步：{error}")
            assignments.update({aid: row for aid, row in old_assignments.items() if row.get("course") == code})
            metadata.update({aid: row for aid, row in old_meta.items() if aid in assignments})
        try:
            items = api.get(f"/api/v1/announcements?context_codes[]=course_{quote(cid)}&per_page=100")
            if not isinstance(items, list):
                raise ValueError("公告接口返回内容无效")
            announcements.extend(items)
        except Exception as error:
            warnings.append(f"{code} 公告暂未完整同步：{error}")
            announcements.extend(row for row in old_announcements if row.get("context_code") == f"course_{cid}")
    cancelled()
    save_private(profile / "assignment-meta.json", metadata)
    save_private(profile / "announcements.json", announcements)
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat()
    last_complete = old_snapshot.get("last_complete_at") or (old_snapshot.get("collected_at") if old_snapshot.get("complete") else None)
    save_private(profile / "snapshot.json", {"collected_at": collected_at,
        "last_complete_at": collected_at if not warnings else last_complete,
        "changes": observed_changes(old_snapshot, assignments, old_announcements, announcements),
        "assignments": assignments, "complete": not warnings, "warnings": warnings})
    return config, warnings
