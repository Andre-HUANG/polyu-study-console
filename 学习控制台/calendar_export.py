"""Local RFC 5545 export of confirmed exams and deadlines; nothing leaves the account folder."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HK = ZoneInfo("Asia/Hong_Kong")
FILENAME = "polyu-schedule.ics"
LIMIT = 300
KIND_LABELS = {"midterm": "期中考试", "final": "期末考试", "test": "其他测试", "assignment": "作业"}
TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def escape(value) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    """Fold at 75 octets without splitting a UTF-8 character (RFC 5545 §3.1)."""
    parts, current, size = [], "", 0
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > (75 if not parts else 74):
            parts.append(current)
            current, size = "", 0
        current += ch
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def event_uid(row) -> str:
    identity = row.get("markKey") or "\n".join(str(row.get(k) or "") for k in ("id", "course", "rawTitle", "date"))
    return hashlib.sha256(("polyu-study-console\n" + identity).encode("utf-8")).hexdigest()[:32] + "@polyu-study-console.local"


def summary(row) -> str:
    title = str(row.get("title") or row.get("rawTitle") or "安排").strip()
    course = str(row.get("course") or "").strip()
    label = KIND_LABELS.get(row.get("kind"), "安排")
    return (f"[{course}] " if course else "") + title + f"（{label}）"


def description(row, version: str) -> str:
    lines = [KIND_LABELS.get(row.get("kind"), "安排") + (f" · {row['courseName']}" if row.get("courseName") else "")]
    if row.get("sourceLabel"): lines.append("来源：" + str(row["sourceLabel"]))
    weight = str(row.get("weight") or "")
    if weight and weight != "—": lines.append("占比：" + weight)
    if row.get("timeText") and not row.get("time"): lines.append("时间：" + str(row["timeText"]))
    if row.get("note"): lines.append("备注：" + str(row["note"]))
    if row.get("url"): lines.append("Canvas：" + str(row["url"]))
    lines.append(f"日期与时间以 Canvas、课程原文或 eStudent 为准。由 PolyU study控制台 {version} 在本机导出。")
    return "\n".join(lines)


def timed(row):
    day = row.get("date")
    if not isinstance(day, str) or not DATE_RE.fullmatch(day): return None, None
    start_text = row.get("time") if isinstance(row.get("time"), str) and TIME_RE.fullmatch(row.get("time") or "") else ""
    if not start_text:
        return date.fromisoformat(day), None
    start = datetime.fromisoformat(f"{day}T{start_text}").replace(tzinfo=HK)
    end = None
    end_text = row.get("endTime") if isinstance(row.get("endTime"), str) else ""
    if TIME_RE.fullmatch(end_text or ""):
        candidate = datetime.fromisoformat(f"{day}T{end_text}").replace(tzinfo=HK)
        if candidate > start: end = candidate
    return start, end


def build(rows, now: datetime, version: str) -> tuple[bytes, int]:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//PolyU Study Console//{escape(version)}//ZH",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:PolyU 考试与作业", "X-WR-TIMEZONE:Asia/Hong_Kong"]
    stamp, count = utc_stamp(now), 0
    for row in rows[:LIMIT]:
        start, end = timed(row)
        if start is None: continue
        lines += ["BEGIN:VEVENT", "UID:" + event_uid(row), "DTSTAMP:" + stamp, "SUMMARY:" + escape(summary(row))]
        if isinstance(start, datetime):
            lines.append("DTSTART:" + utc_stamp(start))
            lines.append("DTEND:" + utc_stamp(end or start))
        else:
            lines.append("DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d"))
            lines.append("DTEND;VALUE=DATE:" + (start + timedelta(days=1)).strftime("%Y%m%d"))
            lines.append("TRANSP:TRANSPARENT")
        if row.get("location"): lines.append("LOCATION:" + escape(row["location"]))
        if row.get("url"): lines.append("URL:" + re.sub(r"[\x00-\x20\x7f]", "", str(row["url"])))
        lines.append("CATEGORIES:" + escape(KIND_LABELS.get(row.get("kind"), "安排")))
        lines.append("DESCRIPTION:" + escape(description(row, version)))
        # Timed items remind 24 hours ahead; all-day items at 09:00 the previous day, not midnight.
        trigger = "-P1D" if isinstance(start, datetime) else "-PT15H"
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:" + trigger, "DESCRIPTION:" + escape(summary(row)), "END:VALARM", "END:VEVENT"]
        count += 1
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold(line) for line in lines) + "\r\n").encode("utf-8"), count


def write(profile: Path, body: bytes) -> Path:
    folder = Path(profile) / "exports"
    if Path(profile).is_symlink() or folder.is_symlink():
        raise RuntimeError("导出目录不能是链接。")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / FILENAME
    if target.is_symlink():
        raise RuntimeError("导出文件不能是链接。")
    temporary = folder / (FILENAME + "." + secrets.token_hex(16) + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return target
