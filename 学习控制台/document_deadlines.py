"""Reconcile Canvas file dates with assignments and personal schedule entries."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from canvas_documents import read_json
from native_canvas import save_private

PREFIX = "Canvas 文件核对 · "
HK = ZoneInfo("Asia/Hong_Kong")


def reconcile(profile, index):
    profile = Path(profile)
    evidence = index["evidence"]
    snapshot = read_json(profile / "snapshot.json", {})
    state = read_json(profile / "state.json", {})
    manual = [row for row in state.get("manual_deadlines") or []
              if not str(row.get("source") or "").startswith(PREFIX)]
    if manual != state.get("manual_deadlines", []):
        save_private(profile / "state.json", {"manual_deadlines": manual})

    confirmed, assignment_sources, conflicts, manual_sources = [], {}, {}, {}
    pending = list(evidence.get("pending") or [])
    for event in evidence.get("deadlines") or []:
        pattern = re.compile(event["pattern"], re.I)
        matches = [(str(aid), row) for aid, row in (snapshot.get("assignments") or {}).items()
                   if row.get("course") == event["course"] and pattern.search(row.get("name") or "") and row.get("due_at")]
        if len(matches) == 1:
            aid, row = matches[0]
            try:
                date = dt.datetime.fromisoformat(row["due_at"].replace("Z", "+00:00")).astimezone(HK).date().isoformat()
            except ValueError:
                date = None
            api_source = {"documentId": "canvas-assignment:" + aid, "title": row.get("name") or "Canvas 作业",
                          "url": row.get("html_url"), "locator": "Canvas 截止日期", "quote": str(row.get("due_at"))}
            assignment_sources[aid] = event["sources"]
            if date and date != event["date"]:
                pending.append({**event, "needsReview": True,
                                "reason": f"文件日期 {event['date']} 与 Canvas 截止日期 {date} 不一致",
                                "sources": event["sources"] + [api_source]})
                conflicts[aid] = "课程文件日期与 Canvas 截止日期不一致，需核对来源"
            else:
                confirmed.append({**event, "canvasAssignmentId": aid})
            continue
        other = next((row for row in manual if row.get("course") == event["course"]
                      and (row.get("item") == event["title"] or pattern.search(row.get("item") or ""))), None)
        if other:
            if other.get("date") != event["date"]:
                pending.append({**event, "needsReview": True,
                                "reason": "现有安排与课程文件日期不一致，需核对"})
            else:
                confirmed.append(event)
                manual_sources[event["id"]] = other.get("item")
            continue
        confirmed.append(event)
    evidence["deadlines"] = confirmed
    evidence["pending"] = list({(row.get("id"), row.get("reason")): row for row in pending}.values())
    evidence["assignmentSources"] = assignment_sources
    evidence["assignmentConflicts"] = conflicts
    evidence["manualSources"] = manual_sources
    save_private(profile / "documents/index.json", index)
    return evidence
