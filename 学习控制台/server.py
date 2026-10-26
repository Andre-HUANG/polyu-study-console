#!/usr/bin/env python3
"""Private, loopback-only PolyU Canvas study dashboard."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.error import URLError
from zoneinfo import ZoneInfo

from grades import build_grades
from canvas_sync import collect as collect_canvas
from canvas_documents import public_summary, scan_courses, needs_cached_upgrade, reprocess_cached_documents
from canvas_archive import archive_courses
from canvas_web import authenticated_user
from document_deadlines import PREFIX as DOCUMENT_DEADLINE_PREFIX, reconcile as reconcile_document_deadlines
from native_canvas import HOST as CANVAS_HOST, normalized_cookies, save_private, session_class
from platform_support import backend_session
from app_config import VERSION as RELEASE_VERSION
from document_search import search as search_documents
from freshness import summary as freshness_summary
from course_evidence import VERSION as EVIDENCE_VERSION
from assessment_types import KINDS, EXAM_RE, classify
from diagnostic_log import DiagnosticLog, error_code, validate_event
from local_access import LocalAccess
import runtime_cache
import academic_calendar
import timetable


HERE = Path(__file__).resolve().parent
PROJECT = Path(os.environ.get("POLYU_CONSOLE_DATA_DIR") or HERE.parent).resolve()
PROFILE = PROJECT / "account"
NATIVE = os.environ.get("POLYU_NATIVE_APP") == "1"
NATIVE_SECRET = os.environ.get("POLYU_NATIVE_SECRET") or ""
APP_VERSION = os.environ.get("POLYU_APP_VERSION") or RELEASE_VERSION
HK = ZoneInfo("Asia/Hong_Kong")
ACCESS = LocalAccess()
CSRF = ACCESS.csrf
MUTATION_LOCK = threading.Lock()
JOB = {"busy": False, "stage": "", "lastError": "", "needsLogin": False, "lastAttempt": 0.0}
SUBMITTED = {"graded", "submitted", "pending_review"}
DIAGNOSTICS = None


def load_json(path: Path, default):
    try:
        return runtime_cache.read_json(path, default)
    except (OSError, json.JSONDecodeError):
        return default


def diagnostics():
    global DIAGNOSTICS
    if DIAGNOSTICS is None or DIAGNOSTICS.profile != PROFILE:
        DIAGNOSTICS = DiagnosticLog(PROFILE, APP_VERSION)
    login, config = load_json(PROFILE / 'login.json', {}), load_json(PROFILE / 'config.json', {})
    owner = (config.get('user') or {}).get('id')
    authorized = login.get('user_id') and str(login['user_id']) == str(owner)
    return DIAGNOSTICS.for_account(owner if authorized else None)


def local_time(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(HK)
    except ValueError:
        return None


def migrate_legacy_profile():
    """Move an authenticated 1.4 account, discarding its weekly-plan output."""
    legacy = PROJECT / ".jiujiastudy"
    if not NATIVE or not legacy.exists():
        return
    if legacy.is_symlink():
        raise RuntimeError("旧账号目录是链接，无法安全迁移。")
    if PROFILE.exists() and any(PROFILE.iterdir()):
        return
    login = load_json(legacy / "login.json", {})
    config = load_json(legacy / "config.json", {})
    if not login.get("user_id") or str(login["user_id"]) != str((config.get("user") or {}).get("id")):
        return
    if PROFILE.exists():
        PROFILE.rmdir()
    legacy.rename(PROFILE)
    old_snapshot = load_json(PROFILE / "raw/daily/snapshot.json", {})
    if old_snapshot:
        save_private(PROFILE / "snapshot.json", {k: old_snapshot.get(k) for k in ("assignments", "collected_at", "complete")})
        raw_dir = PROFILE / str(old_snapshot.get("raw_dir") or "")
        if raw_dir.resolve().is_relative_to(PROFILE.resolve()):
            metadata = {}
            for course in config.get("courses") or []:
                for row in load_json(raw_dir / f"assignments_{course.get('id')}.json", []):
                    if isinstance(row, dict) and row.get("id") is not None:
                        metadata[str(row["id"])] = {"assignment_group_id": row.get("assignment_group_id"),
                            "omit_from_final_grade": bool(row.get("omit_from_final_grade")),
                            "excused": bool((row.get("submission") or {}).get("excused"))}
            save_private(PROFILE / "assignment-meta.json", metadata)
            save_private(PROFILE / "announcements.json", load_json(raw_dir / "announcements.json", []))
    config.pop("root", None)
    save_private(PROFILE / "config.json", config)
    state = load_json(PROFILE / "state.json", {})
    manual = [row for row in state.get("manual_deadlines") or []
              if not str(row.get("source") or "").startswith(DOCUMENT_DEADLINE_PREFIX)]
    save_private(PROFILE / "state.json", {"manual_deadlines": manual})
    for name in ("plans", "reports", "raw", "vendor", "study-latest.log", "radar-latest.log"):
        target = PROFILE / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
    legacy_output = PROJECT / "救驾"
    if legacy_output.is_symlink():
        legacy_output.unlink()
    elif legacy_output.exists():
        shutil.rmtree(legacy_output)


def rebuild(collect=True):
    phases = []
    def phase(name, status="running", detail=""):
        row = next((item for item in phases if item["name"] == name), None)
        if row is None:
            row = {"name": name}
            phases.append(row)
        row.update(status=status, detail=detail, at=datetime.now(HK).isoformat())
        save_private(PROFILE / "sync-status.json", {"version": APP_VERSION, "phases": phases})

    if not collect:
        return
    JOB["stage"] = "正在核对 Canvas 登录…"
    api = native_api()
    me = read_user(api)
    cfg = load_json(PROFILE / "config.json", {})
    if str(me.get("id")) != str((cfg.get("user") or {}).get("id")):
        raise RuntimeError("Canvas 登录账号与本机档案不一致，请退出后重新登录。")

    def still_connected():
        if not load_json(PROFILE / "login.json", {}):
            raise RuntimeError("已退出登录。")

    phase("课程与作业")
    JOB["stage"] = "正在抓取课程、作业、公告与成绩…"
    collection_failed = False
    try:
        cfg, notices = collect_canvas(api, PROFILE, me, still_connected, lambda stage: JOB.update(stage=stage))
        JOB.setdefault("warnings", []).extend(notices)
        phase("课程与作业", "partial" if notices else "complete", "\n".join(notices))
    except Exception as error:
        collection_failed = True
        read_user(api)
        still_connected()
        detail = str(error)[-1100:]
        phase("课程与作业", "partial", detail)
        JOB.setdefault("warnings", []).append("部分课程概览暂不可读；" + (
            "将沿用已保存的课程继续文件识别。" if cfg.get("courses") else "课程文件尚无法遍历。"))
    if collection_failed and not cfg.get("courses"):
        phase("课程文件与图片", "partial", "课程列表暂不可读，无法确定文件遍历范围。")
        JOB["stage"] = "课程列表暂不可读；尚未扫描课程文件。"
        return

    from canvas_grading import collect_grading, merge_document_grading
    JOB["stage"] = "正在读取课程评分规则…"
    phase("Canvas 评分设置")
    notices = collect_grading(api, PROFILE, cfg, still_connected)
    phase("Canvas 评分设置", "partial" if notices else "complete", "\n".join(notices))
    phase("Canvas 课程日历")
    calendar_result = timetable.collect(api, PROFILE, cfg, still_connected)
    phase("Canvas 课程日历", "partial" if calendar_result["issues"] else "complete",
          "课程日历覆盖受限；文件识别继续。" if calendar_result["issues"] else "已读取所有可见课程日历分页。")
    phase("课程附件下载")
    JOB["stage"] = "正在按 Canvas 模块下载所有可见附件…"
    archive = archive_courses(api, PROFILE, cfg, still_connected, lambda stage: JOB.update(stage=stage))
    downloaded = sum(c["downloaded"] for c in archive["courses"])
    discovered = sum(c["discovered"] for c in archive["courses"])
    phase("课程附件下载", "complete" if archive["complete"] else "partial",
          f"已下载 {downloaded} / {discovered} 个模块附件；" +
          ("所有可见模块附件下载完成，开始逐份识别。" if archive["complete"] else "下载或清单读取存在问题，已记录详情。"))
    phase("课程文件与图片")
    def apply_findings(index):
        still_connected()
        reconcile_document_deadlines(PROFILE, index)
        merge_document_grading(PROFILE, index)
        timetable.rebuild(PROFILE, cfg, index)
    try:
        index = scan_courses(api, PROFILE, cfg, os.environ.get("POLYU_DOCUMENT_READER"), still_connected,
                             lambda stage: JOB.update(stage=stage), checkpoint=apply_findings, archive=archive)
    except Exception as error:
        phase("课程文件与图片", "partial", str(error)[-500:])
        raise
    JOB["stage"] = "正在核对文件考试日期与评分标准…"
    apply_findings(index)
    coverage = public_summary(PROFILE)
    phase("课程文件与图片", "complete" if coverage["complete"] else "partial",
          f"已读取 {coverage['readFiles']} / {coverage['fileCount']} 个文件，处理 {coverage['processedPages']} 页；{coverage['issueCount']} 处读取问题。")


def native_api(cookies=None):
    return session_class()(CANVAS_HOST, str(PROFILE), cookies=cookies, persist=cookies is None,
                           timeout=25, retries=1)


def read_user(api):
    try:
        return authenticated_user(api)
    except Exception:
        # Existing accounts created before the web-page reader remain usable
        # when Canvas still accepts its first-party JSON endpoint.
        return api.get("/api/v1/users/self")


def connect_native(payload):
    diagnostics().record('login.verify', 'attempt')
    try:
        message = _connect_native(payload)
    except Exception as error:
        diagnostics().record('login.verify', 'failure', error_code(error))
        raise
    diagnostics().record('login.verify', 'success')
    return message


def account_needs_review():
    config = load_json(PROFILE / "config.json", {})
    owner = (config.get("user") or {}).get("id")
    login_owner = load_json(PROFILE / "login.json", {}).get("user_id")
    if owner:
        return bool(login_owner) and str(login_owner) != str(owner)
    if not PROFILE.exists(): return False
    # Only anonymous UI preferences may precede first authorization.
    return any(p.is_symlink() or (p.is_file() and p.name != "app-settings.json")
               for p in PROFILE.rglob("*"))


def _connect_native(payload):
    if account_needs_review():
        raise ValueError("现有账号资料的所有者无法核对，已保留且未合并。请先核对原资料；换账号需明确退出并删除后再连接。")
    cookies = normalized_cookies(payload.get("cookies"))
    if not cookies:
        raise ValueError("尚未完成学校登录，请在登录窗口完成验证。")
    api = native_api(cookies)
    from canvas_api import CanvasAuthError
    try:
        me = read_user(api)
    except CanvasAuthError:
        raise ValueError("Canvas 未认可这次登录，请在学校登录窗口完成验证后重试。") from None
    except URLError:
        raise RuntimeError("暂时无法连接 PolyU Canvas，请检查网络后重试。") from None
    if not isinstance(me, dict) or not me.get("id"):
        raise ValueError("Canvas 未返回已登录的账号，请重新登录。")
    cfg = load_json(PROFILE / "config.json", {})
    old_id = (cfg.get("user") or {}).get("id")
    if old_id and str(old_id) != str(me["id"]):
        # Do not combine grades or deadlines belonging to different accounts.
        raise ValueError("当前档案属于另一账号。请先退出登录并清除账号数据，再连接新的账号。")
    api.persist = True
    api.close()  # Protect and verify the session before changing account metadata.
    if not cfg:
        cfg = {"canvas_host": CANVAS_HOST, "user": {"id": me["id"], "name": me.get("name") or "PolyU 学生"},
               "courses": [], "term": {}}
        save_private(PROFILE / "config.json", cfg)
    if not (PROFILE / "state.json").exists():
        save_private(PROFILE / "state.json", {"manual_deadlines": []})
    save_private(PROFILE / "login.json", {"host": CANVAS_HOST, "lms": "canvas", "transport": "webkit",
                 "name": me.get("name") or "PolyU 学生", "user_id": me["id"],
                 "at": datetime.now(HK).isoformat()})
    from sync_support import sync_settings
    saved_settings = load_json(PROFILE / 'app-settings.json', {'autoSync': True})
    save_private(PROFILE / 'app-settings.json', sync_settings(saved_settings))
    JOB.update(needsLogin=False, lastError="")
    runtime_cache.clear()
    return "Canvas 已授权，正在建立本机学习控制台。"


def logout_native():
    global CSRF
    runtime_cache.clear()
    academic_calendar.clear_memory()
    DASHBOARD_CACHE.clear()
    ACCESS.rotate()
    CSRF = ACCESS.csrf
    from session_store import delete_session
    delete_session(PROFILE / "browser/canvas-cookies.json")
    # This endpoint runs under MUTATION_LOCK, so no sync worker can rewrite
    # an account after its data has been removed. The app also stops the whole
    # backend process group before clearing its data and WebKit stores.
    diagnostics().record('logout', 'attempt')
    for directory in (PROFILE,):
        if directory.is_symlink():
            directory.unlink()
        elif directory.exists():
            shutil.rmtree(directory)
    PROFILE.mkdir(mode=0o700)
    diagnostics().reset()
    diagnostics().record('logout', 'attempt')
    diagnostics().record('logout.cleanup', 'success')
    diagnostics().record('logout', 'success')
    JOB.update(busy=False, needsLogin=False, lastError="", warnings=[], stage="", lastAttempt=0.0)
    return "已退出登录，并删除本机该账号的全部学习数据。"


def connection_data():
    login = load_json(PROFILE / "login.json", {})
    config = load_json(PROFILE / "config.json", {})
    authorized = bool(login.get("user_id")) and str(login.get("user_id")) == str((config.get("user") or {}).get("id"))
    settings = read_sync_settings()
    return {"native": NATIVE, "authorized": authorized, "needsDataReview": account_needs_review(), "name": (login.get("name") or "") if authorized else "",
            "autoSync": bool(settings.get("autoSync", True)),
            "syncIntervalSeconds": settings['syncIntervalSeconds'],
            "syncStatus": load_json(PROFILE / "sync-status.json", {}) if authorized else {},
            **{k: v for k, v in JOB.items() if k != "lastAttempt"}}


def start_sync():
    if runtime_cache.STOP.is_set(): return False
    if NATIVE and not connection_data()["authorized"]:
        return False
    if not MUTATION_LOCK.acquire(blocking=False):
        return False
    JOB.update(busy=True, stage="正在开始同步…", lastError="", warnings=[], lastAttempt=time.monotonic())
    diagnostics().record('sync', 'attempt')
    def worker():
        try:
            rebuild(collect=True)
            coverage = public_summary(PROFILE)
            stage = JOB["stage"] if not load_json(PROFILE / "config.json", {}).get("courses") and JOB.get("warnings") else (
                f"已读取 {coverage['readFiles']} / {coverage['fileCount']} 个文件，考试与作业安排已更新。")
            if coverage["reviewCount"]: stage += f" {coverage['reviewCount']} 处来源存在冲突或识别歧义。"
            if coverage["issueCount"]: stage += " 部分内容暂未读取，后续同步会重试。"
            if JOB.get("warnings"): stage += " 课程概览有未完成项。"
            JOB.update(stage=stage, needsLogin=False)
            incomplete = bool(coverage['issueCount'] or coverage['reviewCount'] or JOB.get('warnings'))
            diagnostics().record('sync', 'partial' if incomplete else 'success', 'PARTIAL_DATA' if incomplete else '')
        except Exception as exc:
            message = str(exc)
            auth = "登录" in message or "unauthenticated" in message or "401" in message
            JOB.update(lastError=message[-1100:], stage="同步未完成", needsLogin=auth)
            diagnostics().record('sync', 'failure', error_code(exc))
        finally:
            JOB["busy"] = False
            MUTATION_LOCK.release()
            runtime_cache.WAKE.set()
    threading.Thread(target=worker, daemon=True).start()
    return True


def read_sync_settings():
    from sync_support import sync_settings
    path = PROFILE / 'app-settings.json'
    saved = load_json(path, {})
    settings = sync_settings(saved)
    if path.is_file() and settings != saved and MUTATION_LOCK.acquire(blocking=False):
        try:
            current = load_json(path, {})
            settings = sync_settings(current)
            if settings != current: save_private(path, settings)
        finally: MUTATION_LOCK.release()
    return settings


def automatic_sync_due(settings, login, snapshot, document_index, has_grading, now, monotonic):
    from sync_support import sync_settings
    settings = sync_settings(settings)
    interval = settings['syncIntervalSeconds']
    at = local_time(snapshot.get('collected_at'))
    from timetable_reader import VERSION as TIMETABLE_VERSION
    timetable_upgrade=any(record.get("status")=="read" and record.get("timetableVersion")!=TIMETABLE_VERSION for record in document_index.get("records",[]))
    stale = timetable_upgrade or not at or (now-at).total_seconds() >= interval or not has_grading or document_index.get('version') != EVIDENCE_VERSION or not document_index.get('completedAt')
    return bool(settings.get('autoSync', True) and login and stale and not JOB['busy'] and not JOB['needsLogin'] and (not JOB['lastAttempt'] or monotonic-JOB['lastAttempt'] >= interval))


def start_cached_upgrade():
    if runtime_cache.STOP.is_set() or not connection_data()["authorized"]:return False
    if not MUTATION_LOCK.acquire(blocking=False):return False
    config=load_json(PROFILE/"config.json",{});account=str((config.get("user") or {}).get("id"));epoch=ACCESS.token
    JOB.update(busy=True,stage="正在升级本账号已保存资料的识别结果…")
    def cancelled():
        if runtime_cache.STOP.is_set() or ACCESS.token!=epoch or str(load_json(PROFILE/"login.json",{}).get("user_id"))!=account:
            raise RuntimeError("本账号识别升级已取消")
    def worker():
        try:
            index=reprocess_cached_documents(PROFILE,config,os.environ.get("POLYU_DOCUMENT_READER"),cancelled,lambda stage:JOB.update(stage=stage))
            cancelled()
            if index:
                from canvas_grading import merge_document_grading
                reconcile_document_deadlines(PROFILE,index);merge_document_grading(PROFILE,index);timetable.rebuild(PROFILE,config,index)
            JOB.update(stage="已使用本账号的保存资料完成识别升级。")
        except Exception as error:
            JOB.update(lastError="保存资料的识别升级未完成，后续同步会重试。",stage="本机识别升级未完成")
            diagnostics().record("operation","failure",error_code(error))
        finally:
            JOB["busy"]=False;MUTATION_LOCK.release();runtime_cache.clear()
    threading.Thread(target=worker,daemon=True).start()
    return True

def automatic_sync():
    while not runtime_cache.STOP.is_set():
        runtime_cache.WAKE.clear()
        with runtime_cache.snapshot():
            settings = read_sync_settings()
            login = load_json(PROFILE / 'login.json', {})
            snapshot = load_json(PROFILE / 'snapshot.json', {})
            document_index = load_json(PROFILE / 'documents/index.json', {})
            config = load_json(PROFILE / "config.json", {})
        if login and needs_cached_upgrade(document_index,config) and not JOB["busy"]:
            if start_cached_upgrade():continue
        if automatic_sync_due(settings, login, snapshot, document_index, (PROFILE/'grading_schemes.json').exists(), datetime.now(HK), time.monotonic()):
            start_sync()
        delay = None
        if settings['autoSync'] and login and not JOB['needsLogin']:
            interval = settings["syncIntervalSeconds"]
            if JOB['busy']: delay = interval
            elif JOB["lastAttempt"]: delay = max(1, interval-(time.monotonic()-JOB["lastAttempt"]))
            else:
                at = local_time(snapshot.get('collected_at'))
                delay = max(1, interval-(datetime.now(HK)-at).total_seconds()) if at else interval
        runtime_cache.WAKE.wait(delay)

CALENDAR_LOCK = threading.Lock()
CALENDAR_JOB = {"busy": False}
def start_calendar_refresh():
    if not CALENDAR_LOCK.acquire(blocking=False): return False
    CALENDAR_JOB["busy"] = True
    epoch = ACCESS.token
    authenticated = connection_data()["authorized"]
    def cancelled():
        if runtime_cache.STOP.is_set() or epoch != ACCESS.token: raise RuntimeError("校历读取已取消")
    def worker():
        try:
            def commit(value):
                with MUTATION_LOCK:
                    cancelled()
                    academic_calendar.store_value(value, PROFILE if authenticated and connection_data()["authorized"] else None)
            academic_calendar.refresh(PROFILE if authenticated else None, cancelled, commit=commit)
        except RuntimeError: pass
        finally:
            CALENDAR_JOB["busy"] = False
            CALENDAR_LOCK.release()
    threading.Thread(target=worker, daemon=True).start()
    return True

def calendar_maintenance():
    # No background daemon survives application termination.
    while not runtime_cache.STOP.is_set():
        profile = PROFILE if connection_data()["authorized"] else None
        if academic_calendar.due(profile): start_calendar_refresh()
        if runtime_cache.STOP.wait(7*86400): return


def safe_url(value):
    if not isinstance(value, str):
        return ""
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname == "canvas.polyu.edu.hk":
        return url
    return ""


def clean_title(value):
    return re.sub(r"^[A-Z]{2,6}\d{3,8}[A-Z]?\s+", "", value or "").strip()


def latest_canvas_change(snapshot, courses, documents):
    """Most recent Canvas timestamp in assignments, announcements, pages and files."""
    candidates = []
    for row in (snapshot.get("assignments") or {}).values():
        at = local_time(row.get("updated_at"))
        if at:
            candidates.append({
                "at": at, "course": row.get("course") or "", "title": row.get("name") or "作业",
                "url": safe_url(row.get("html_url")), "type": "作业更新",
            })

    announcements = load_json(PROFILE / "announcements.json", [])
    course_ids = {str(row.get("id")): row.get("code") for row in courses}
    for row in announcements if isinstance(announcements, list) else []:
        if not isinstance(row, dict):
            continue
        at = local_time(row.get("posted_at"))
        if at:
            context = str(row.get("context_code") or "")
            candidates.append({
                "at": at, "course": course_ids.get(context.removeprefix("course_"), ""),
                "title": row.get("title") or "课程公告", "url": safe_url(row.get("html_url")),
                "type": "公告发布",
            })
    for record in documents.get("records") or []:
        at = local_time(record.get("updatedAt"))
        if at:
            candidates.append({"at": at, "course": record.get("course"), "title": record.get("title"),
                "url": safe_url(record.get("url")), "type": "课件文件更新" if record.get("type") == "file" else "课程内容更新"})
    if not candidates: return None
    latest = max(candidates, key=lambda row: row["at"])
    return {**latest, "at": latest["at"].isoformat()}


def raw_assignment_meta(snapshot, courses):
    return load_json(PROFILE / "assignment-meta.json", {})


def event_due_time(row):
    due = local_time(row.get("dueAt"))
    if due is None and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", row.get("time") or ""):
        due = datetime.fromisoformat(row["date"] + "T" + row["time"]).replace(tzinfo=HK)
    return due


def split_deadline_events(events, now):
    """Keep source uncertainty separate from a date that has passed."""
    completed = SUBMITTED | {"done", "completed", "已完成", "已提交", "已评分", "已交"}
    upcoming, overdue = [], []
    for original in events:
        row = dict(original)
        if row.get("status") in completed or row.get("submissionState") in SUBMITTED:
            continue
        past = row["daysLeft"] < 0
        due = event_due_time(row)
        if due is not None:
            past = due <= now
        candidate = (past and row.get("kind") == "assignment" and not row.get("pending")
                     and row.get("status") not in completed and row.get("submissionState") not in SUBMITTED)
        if candidate:
            canvas_unsubmitted = (row.get("source") == "canvas" and row.get("submissionState") == "unsubmitted"
                                  and row.get("submissionStateObserved") is True)
            row["overdueState"] = "canvas_unsubmitted" if canvas_unsubmitted else "date_passed"
            row["overdueLabel"] = ("Canvas 上次同步显示未提交" if canvas_unsubmitted else "已过日期，提交状态待核对")
            overdue.append(row)
        elif row["daysLeft"] >= 0:
            upcoming.append(row)
    return upcoming, overdue


def dashboard_data():
    config = load_json(PROFILE / "config.json", {})
    state = load_json(PROFILE / "state.json", {})
    snapshot = load_json(PROFILE / "snapshot.json", {})
    signed_in = not NATIVE or connection_data()["authorized"]
    if not signed_in:
        # Never reveal residual data before the current account is authorized.
        config, state, snapshot = {}, {}, {}
    now = datetime.now(HK)
    today = now.date()
    courses = config.get("courses") or []
    course_names = {row.get("code"): row.get("name") for row in courses}
    documents = public_summary(PROFILE) if signed_in else {}
    evidence = documents.get("evidence") or {}
    source_events = {DOCUMENT_DEADLINE_PREFIX + e["id"]: e for e in evidence.get("deadlines") or []}

    events = []
    covered_document_ids = set()
    manual = state.get("manual_deadlines") or []
    manual_urls = {row.get("url") for row in manual if row.get("url")}
    manual_assignment_ids = {str(row.get("assignment_id")) for row in manual if row.get("assignment_id")}
    for i, row in enumerate(manual):
        date = row.get("date")
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        title = str(row.get("item") or "")
        kind = row.get('kind') if row.get('kind') in KINDS else classify(title)
        document_event = source_events.get(row.get("source"))
        if not document_event:
            related = [e for e in evidence.get("deadlines") or [] if e["course"] == row.get("course") and e["date"] == date and re.search(e["pattern"], title, re.I)]
            if len(related) == 1: document_event = related[0]
        if document_event:
            kind = document_event['kind']
            covered_document_ids.add(document_event['id'])
        sources = (document_event or {}).get("sources") or []
        linked_assignment = (snapshot.get("assignments") or {}).get(str(row.get("assignment_id")), {})
        if not linked_assignment and row.get("url"):
            linked_assignment = next((a for a in (snapshot.get("assignments") or {}).values()
                                      if a.get("html_url") == row["url"]), {})
        events.append({
            "id": f"manual:{i}", "source": "documents" if document_event else "manual", "course": row.get("course"),
            "courseName": course_names.get(row.get("course"), ""),
            "title": clean_title(title), "rawTitle": title, "date": date,
            "time": (document_event or {}).get("time") or row.get("time") or "", "timeText": (document_event or {}).get("timeText") or row.get("time_text") or "",
            "endTime": (document_event or {}).get("endTime") or "", "location": (document_event or {}).get("location") or "",
            "instructions": (document_event or {}).get("instructions") or [], "coverage": (document_event or {}).get("coverage") or [], "warnings": (document_event or {}).get("warnings") or [],
            "kind": kind, "weight": row.get("weight") or "—", "status": row.get("status") or "待处理",
            "url": safe_url(row.get("url")), "note": row.get("note") or "",
            "sourceLabel": "Canvas 课程文件 · 自动识别" if document_event else row.get("source") or "手动记录", "editable": not (row.get("source") or "").startswith(DOCUMENT_DEADLINE_PREFIX),
            "sources": sources,
            "pending": bool(row.get("pending")),
            "submissionState": linked_assignment.get("sub_state"),
        })

    for aid, row in (snapshot.get("assignments") or {}).items():
        if row.get("html_url") in manual_urls or str(aid) in manual_assignment_ids:
            continue
        due = local_time(row.get("due_at"))
        if not due or row.get("sub_state") in SUBMITTED:
            continue
        title = str(row.get("name") or "")
        related = [e for e in evidence.get('deadlines') or [] if e['course'] == row.get('course') and e['date'] == due.date().isoformat() and re.search(e['pattern'], title, re.I)]
        document_event = related[0] if len(related) == 1 else {}
        if document_event: covered_document_ids.add(document_event['id'])
        events.append({
            "id": f"canvas:{aid}", "source": "canvas", "course": row.get("course"),
            "courseName": course_names.get(row.get("course"), ""),
            "title": title, "rawTitle": title, "date": due.date().isoformat(),
            "time": document_event.get('time') or due.strftime("%H:%M"), "timeText": document_event.get('timeText') or "",
            "dueAt": due.isoformat(), "submissionState": row.get("sub_state"),
            "submissionStateObserved": row.get("submission_state_observed") is True, "cached": bool(row.get("cached")),
            "endTime": document_event.get('endTime') or '', 'location': document_event.get('location') or '',
            'instructions': document_event.get('instructions') or [], 'coverage': document_event.get('coverage') or [], 'warnings': document_event.get('warnings') or [],
            "kind": document_event.get('kind') or classify(title),
            "weight": row.get("weight") or "—", "status": "未交" if row.get("sub_state") == "unsubmitted" and row.get("submission_state_observed") is True else "待核对",
            "url": safe_url(row.get("html_url")), "note": (evidence.get("assignmentConflicts") or {}).get(str(aid)) or "", "sourceLabel": "Canvas 作业与课程文件" if document_event else "Canvas 作业",
            "sources": (evidence.get("assignmentSources") or {}).get(str(aid)) or document_event.get('sources') or [],
            "editable": False, "pending": str(aid) in (evidence.get("assignmentConflicts") or {}),
        })

    # Show extracted arrangements as soon as a file is read, while later files
    # and image pages are still being processed in the background.
    for event in evidence.get("deadlines") or []:
        if event['id'] in covered_document_ids: continue
        linked_assignment = (snapshot.get("assignments") or {}).get(str(event.get("canvasAssignmentId")), {})
        if linked_assignment.get("sub_state") in SUBMITTED: continue
        if any(row.get("course") == event["course"] and row.get("date") == event["date"] and re.search(event["pattern"], row.get("rawTitle") or "", re.I) for row in events): continue
        sources = event.get("sources") or []
        events.append({**event, "id": "document:" + event["id"], "source": "documents", "rawTitle": event["title"], "courseName": course_names.get(event["course"], ""),
            "weight": f"{event['weight']:g}%" if event.get("weight") is not None else "—", "status": "待参加" if event["kind"] != "assignment" else "未交", "pending": False,
            "url": safe_url(sources[0].get("url")) if sources else "", "note": "", "sourceLabel": "Canvas 课程文件 · 自动识别", "editable": False})
    for row in events:
        row["daysLeft"] = (datetime.fromisoformat(row["date"]).date() - today).days
    events.sort(key=lambda x: (x["date"], x["time"] or "00:00", x["course"] or ""))
    upcoming, overdue = split_deadline_events(events, now)

    undated = []
    for aid, row in (snapshot.get("assignments") or {}).items():
        if row.get("due_at") or row.get("lock_at") or row.get("sub_state") in SUBMITTED:
            continue
        if row.get("html_url") in manual_urls or str(aid) in manual_assignment_ids:
            continue
        if not (row.get("is_quiz") or (row.get("points") or 0) > 0 or EXAM_RE.search(row.get("name") or "")):
            continue
        undated.append({
            "id": str(aid), "course": row.get("course"), "title": row.get("name"),
            "url": safe_url(row.get("html_url")), "kind": classify(row.get("name") or ""),
        })
    undated.sort(key=lambda x: (x["course"] or "", x["title"] or ""))
    for pending in evidence.get("pending") or []:
        sources = pending.get("sources") or []
        undated.append({**pending, "url": safe_url(sources[0].get("url")) if sources else ""})

    recent = []
    for aid, row in (snapshot.get("assignments") or {}).items():
        if row.get("sub_state") not in SUBMITTED:
            continue
        submitted = local_time(row.get("submitted_at") or row.get("graded_at"))
        if not submitted or (today - submitted.date()).days > 14:
            continue
        recent.append({
            "id": str(aid), "course": row.get("course"), "title": row.get("name"),
            "date": submitted.isoformat(), "status": "已评分" if row.get("sub_state") == "graded" else "已提交",
            "score": row.get("score"), "points": row.get("points"), "url": safe_url(row.get("html_url")),
        })
    recent.sort(key=lambda x: x["date"], reverse=True)

    sync_at = local_time(snapshot.get("collected_at"))
    changes = list(snapshot.get("changes") or [])
    downloads = load_json(PROFILE / "documents/downloads.json", {}) if signed_in else {}
    attempt = local_time(downloads.get("startedAt"))
    if attempt and sync_at and attempt >= sync_at:
        changes += [{"kind":"file", "course":e.get("course"), "title":e.get("title"), "url":e.get("url"),
            "detail":"附件内容更新" if e.get("contentChanged") else "新增可见附件"}
            for e in downloads.get("entries") or [] if e.get("status") == "downloaded" and (e.get("contentChanged") or e.get("newlyObserved"))]
    return {
        "today": today.isoformat(), "lastSync": sync_at.isoformat() if sync_at else None,
        "freshness": freshness_summary(snapshot),
        "recentChanges": changes[:100], "changeCount": len(changes),
        "latestCanvasChange": latest_canvas_change(snapshot, courses, documents) if signed_in else None,
        "version": APP_VERSION, "documents": documents,
        "academicCalendar": academic_calendar.month_summary(PROFILE if signed_in else None),
        "timetable": timetable.query(PROFILE, config, today.isoformat()) if signed_in else {"date": today.isoformat(), "view": "day", "days": [today.isoformat()], "events": [], "pending": [], "issues": []},
        "grades": build_grades(courses, snapshot.get("assignments"), safe_url,
                               raw_assignment_meta(snapshot, courses), PROFILE / "grading_schemes.json",
                               load_json(PROFILE / "canvas-course-grades.json", {}) if signed_in else None),
        "courseCount": len(courses), "courses": courses,
        "events": upcoming, "overdue": overdue, "undated": undated, "recent": recent[:10],
        "snapshotComplete": bool(snapshot.get("complete", False)),
        "connection": connection_data(),
    }


DASHBOARD_CACHE = {}
DEADLINE_REVISION = 0
def status_data():
    global DEADLINE_REVISION
    now = datetime.now(HK)
    with runtime_cache.LOCK:
        boundary = DASHBOARD_CACHE.get("validUntil")
        if boundary is not None and now >= boundary:
            DASHBOARD_CACHE.clear()
            DEADLINE_REVISION += 1
    with runtime_cache.snapshot():
        connection = connection_data()
    return {"calendarBusy": CALENDAR_JOB["busy"], "revision": runtime_cache.revision(PROFILE, ACCESS.token + str(academic_calendar.REVISION) + ":" + str(DEADLINE_REVISION), now.date().isoformat()),
            "connection": {k: connection.get(k) for k in ("native", "authorized", "busy", "stage", "lastError", "needsLogin", "autoSync", "syncIntervalSeconds")}}

def cached_dashboard(view="full"):
    key = status_data()["revision"]
    with runtime_cache.LOCK:
        if DASHBOARD_CACHE.get("key") != key:
            with runtime_cache.snapshot(): value = dashboard_data()
            DASHBOARD_CACHE.clear()
            # Keep a boundary crossed during construction so the next status poll
            # invalidates this snapshot. Uncertain rows cannot transition to overdue.
            boundaries = [due for row in value.get("events", []) if row.get("kind") == "assignment" and not row.get("pending")
                          and (due := event_due_time(row)) is not None]
            DASHBOARD_CACHE.update(key=key, data=value, validUntil=min(boundaries, default=None))
        value = {**DASHBOARD_CACHE["data"],"revision":key}
    with runtime_cache.snapshot(): value["connection"] = connection_data()
    import hashlib
    feature_files=("documents/timetable.json","timetable-manual.json","calendar/official.json")
    value["featuresRevision"]=hashlib.sha256(json.dumps([ACCESS.token,datetime.now(HK).date().isoformat(),academic_calendar.REVISION,
        [runtime_cache.signature(PROFILE/name) for name in feature_files]]).encode()).hexdigest()
    if view == "dashboard":
        value["documents"] = {k: v for k, v in value["documents"].items() if k not in {"records", "downloads", "evidence"}}
        value["timetable"]=timetable.compact_summary(value["timetable"])
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = "PolyUStudyConsole/" + RELEASE_VERSION

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def native_authorized(self):
        try:
            return bool(NATIVE_SECRET) and secrets.compare_digest(
                self.headers.get("X-PolyU-Native", "").encode("utf-8"), NATIVE_SECRET.encode("utf-8"))
        except (TypeError, UnicodeError):
            return False

    def send_sensitive(self, value):
        # Logout can revoke access while a document/data read is in flight.
        if not self.client_authorized():
            return self.send_json({"error": "客户端授权已失效"}, HTTPStatus.UNAUTHORIZED)
        return self.send_json(value)

    def local_host(self):
        return (len(self.headers.get_all("Host", [])) == 1 and
                self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}")

    def valid_origin(self):
        origin = self.headers.get("Origin")
        return (len(self.headers.get_all("Origin", [])) <= 1 and origin in (None, f"http://127.0.0.1:{self.server.server_port}") and
                self.headers.get("Sec-Fetch-Site") not in {"cross-site", "same-site"})

    def client_authorized(self):
        return ACCESS.authorized(self.headers, self.server.server_port)

    def log_message(self, fmt, *args):
        # HTTP requests can contain private search text. Structured diagnostics
        # are the only retained log; never print paths, queries or responses.
        pass

    def send_bytes(self, body: bytes, content_type: str, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if getattr(self, "response_etag", None): self.send_header("ETag", self.response_etag)
        self.send_header("Connection", "close")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        cookie = getattr(self, "session_cookie", None)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        policy = ("default-src 'self' data:; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                  "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Content-Security-Policy", policy)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=HTTPStatus.OK):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):
        if not self.local_host():
            return self.send_json({"error": "仅允许本机访问"}, HTTPStatus.FORBIDDEN)
        if not self.valid_origin():
            return self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.send_json({"app": "polyu-study-console", "ok": True, "native": NATIVE})
        if path.startswith("/api/") and not self.client_authorized():
            return self.send_json({"error": "需要原生客户端授权"}, HTTPStatus.UNAUTHORIZED)
        if path in {"/api/timetable", "/api/calendar"}:
            try:
                args = parse_qs(urlsplit(self.path).query)
                with runtime_cache.snapshot():
                    authorized = connection_data()["authorized"]
                    if path == "/api/calendar":
                        value = academic_calendar.month_summary(PROFILE if authorized else None, args.get("year", [None])[0], args.get("month", [None])[0])
                        value["busy"] = CALENDAR_JOB["busy"]
                    else:
                        if not authorized: return self.send_sensitive({"days": [], "events": [], "pending": [], "issues": []})
                        value = timetable.query(PROFILE, load_json(PROFILE / "config.json", {}), args.get("date", [datetime.now(HK).date().isoformat()])[0], args.get("view", ["day"])[0], args.get("course", [None])[0])
                        if args.get("detail"):
                            identifier=args["detail"][0]
                            value=next((r for r in value["events"]+value["pending"] if r["id"]==identifier),None)
                            if value is None:return self.send_json({"error":"课项已变化，请刷新后重新核对。"},HTTPStatus.NOT_FOUND)
                        elif args.get("summary")==["1"]:value=timetable.compact_summary(value)
                return self.send_sensitive(value)
            except ValueError as error:
                return self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/status":
            return self.send_sensitive(status_data())
        if path == "/api/documents":
            with runtime_cache.snapshot():
                value = public_summary(PROFILE) if connection_data()["authorized"] else {}
            return self.send_sensitive(value)
        if path == "/api/data":
            revision = status_data()["revision"]
            view = parse_qs(urlsplit(self.path).query).get("view", ["full"])[0]
            self.response_etag = '"' + revision + (".dashboard" if view=="dashboard" else ".full") + '"'
            if self.headers.get("If-None-Match") == self.response_etag and self.client_authorized():
                return self.send_bytes(b"", "application/json", HTTPStatus.NOT_MODIFIED)
            return self.send_sensitive(cached_dashboard(view))
        if path == '/api/diagnostics':
            if self.headers.get('X-PolyU-Console') != CSRF:
                return self.send_json({'error': '请求来源无效'}, HTTPStatus.FORBIDDEN)
            return self.send_sensitive(diagnostics().summary())
        if path == "/api/documents/search":
            if NATIVE and not connection_data()["authorized"]:
                return self.send_json({"error": "请先连接 Canvas 账号"}, HTTPStatus.UNAUTHORIZED)
            try:
                query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
                return self.send_sensitive(search_documents(PROFILE, query))
            except ValueError as error:
                return self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        files = {
            "/": (HERE / "index.html", "text/html; charset=utf-8"),
            "/styles.css": (HERE / "styles.css", "text/css; charset=utf-8"),
            "/app.js": (HERE / "app.js", "application/javascript; charset=utf-8"),
            "/academic-ui.js": (HERE / "academic-ui.js", "application/javascript; charset=utf-8"),
            "/assets/polyu-logo.png": (HERE / "assets/polyu-logo.png", "image/png"),
            "/assets/polyu-favicon.ico": (HERE / "assets/polyu-favicon.ico", "image/x-icon"),
        }
        if path not in files or not files[path][0].exists():
            return self.send_json({"error": "页面不存在"}, HTTPStatus.NOT_FOUND)
        file, content_type = files[path]
        data = file.read_bytes()
        if path == "/":
            data = data.replace(b"__CSRF_TOKEN__", CSRF.encode("ascii") if self.client_authorized() else b"")
        return self.send_bytes(data, content_type)

    def do_POST(self):
        if not self.local_host():
            return self.send_json({"error": "仅允许本机访问"}, HTTPStatus.FORBIDDEN)
        if not self.valid_origin():
            return self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
        origin = self.headers.get("Origin", "")
        expected = f"http://127.0.0.1:{self.server.server_port}"
        path = urlsplit(self.path).path
        trusted_native = (NATIVE and bool(NATIVE_SECRET) and path.startswith("/api/native/") and
                          self.native_authorized())
        bootstrap = path == "/api/client/session" and origin == expected
        if path.startswith("/api/native/") and not trusted_native:
            return self.send_json({"error": "原生授权无效"}, HTTPStatus.FORBIDDEN)
        if not trusted_native and not bootstrap and (not self.client_authorized() or origin != expected or self.headers.get("X-PolyU-Console") != CSRF):
            return self.send_json({"error": "请求来源无效"}, HTTPStatus.FORBIDDEN)
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            return self.send_json({"error": "请求长度无效"}, HTTPStatus.BAD_REQUEST)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > (65536 if trusted_native else 16384):
                return self.send_json({"error": "请求过大"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            raw = self.rfile.read(size)
            if len(raw) != size: raise ValueError("请求正文不完整")
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("请求内容无效")
        except (ValueError, json.JSONDecodeError, TimeoutError, OSError):
            return self.send_json({"error": "请求内容不是有效的 JSON"}, HTTPStatus.BAD_REQUEST)
        if bootstrap:
            token = ACCESS.redeem(payload.get("ticket")) if set(payload) == {"ticket"} else None
            if not token:
                return self.send_json({"error": "客户端授权已失效"}, HTTPStatus.FORBIDDEN)
            self.session_cookie = (ACCESS.cookie_name(self.server.server_port) + "=" + token +
                                   "; Path=/; HttpOnly; SameSite=Strict")
            return self.send_json({"csrf": CSRF})
        if trusted_native and path == "/api/native/update/prepare":
            if not MUTATION_LOCK.acquire(blocking=False): return self.send_json({"error": "正在写入账号资料"}, HTTPStatus.CONFLICT)
            try:
                runtime_cache.STOP.set()
                runtime_cache.WAKE.set()
                return self.send_json({"ok": True})
            finally: MUTATION_LOCK.release()
        if trusted_native and path == "/api/native/bootstrap":
            return self.send_json({"ticket": ACCESS.issue(), "expiresIn": 30})
        if not trusted_native and not bootstrap and not self.client_authorized():
            return self.send_json({'error': '客户端授权已失效'}, HTTPStatus.FORBIDDEN)
        if trusted_native and path == '/api/native/diagnostics':
            try:
                if set(payload) != {'event', 'result', 'code'}: raise ValueError('日志事件无效')
                validate_event(payload['event'], payload['result'], payload['code'])
                diagnostics().record(payload['event'], payload['result'], payload['code'], origin='native')
                return self.send_json({'ok': True})
            except ValueError:
                return self.send_json({'error': '日志事件无效'}, HTTPStatus.BAD_REQUEST)
        if NATIVE and not trusted_native and path not in {"/api/settings", "/api/calendar/refresh", '/api/diagnostics/clear'} and not connection_data()["authorized"]:
            return self.send_json({"error": "请先登录并授权连接 Canvas。"}, HTTPStatus.FORBIDDEN)
        if runtime_cache.STOP.is_set(): return self.send_json({"error": "程序更新正在安装，请等待重启。"}, HTTPStatus.CONFLICT)
        if path == "/api/calendar/refresh":
            if not start_calendar_refresh(): return self.send_json({"error": "校历正在刷新"}, HTTPStatus.CONFLICT)
            return self.send_json({"message": "正在检查官方校历。"})
        if path == "/api/refresh" and NATIVE:
            if not load_json(PROFILE / "login.json", {}):
                return self.send_json({"error": "请先登录并授权连接 Canvas。"}, HTTPStatus.BAD_REQUEST)
            if not start_sync():
                return self.send_json({"error": "已有更新正在进行。"}, HTTPStatus.CONFLICT)
            return self.send_json({"message": "正在同步 Canvas…", "data": dashboard_data()})
        if not MUTATION_LOCK.acquire(blocking=False):
            return self.send_json({"error": "已有更新正在进行，请稍后。"}, HTTPStatus.CONFLICT)
        try:
            sync_after = False
            if not trusted_native and not self.client_authorized():
                return self.send_json({"error": "客户端授权已失效"}, HTTPStatus.FORBIDDEN)
            if trusted_native and path == "/api/native/session":
                message = connect_native(payload)
                sync_after = True
            elif trusted_native and path == "/api/native/logout":
                message = logout_native()
            elif path == "/api/settings" and NATIVE:
                if not isinstance(payload.get("autoSync"), bool):
                    raise ValueError("请选择是否自动同步。")
                settings = read_sync_settings()
                settings['autoSync'] = payload['autoSync']
                save_private(PROFILE / 'app-settings.json', settings)
                message = "自动同步设置已保存。"
                diagnostics().record('settings.sync', 'success')
            elif path == '/api/diagnostics/clear':
                diagnostics().clear()
                return self.send_json({'message': '日志已清空。'})
            elif path == "/api/refresh":
                rebuild(collect=True)
                message = "Canvas、成绩和安排已更新。"
            elif path == "/api/timetable":
                message = timetable.mutate(PROFILE, load_json(PROFILE / "config.json", {}), payload)
            elif path == "/api/event/save":
                message = self.save_event(payload)
                diagnostics().record('event.save', 'success')
            elif path == "/api/event/delete":
                message = self.delete_event(payload)
                diagnostics().record('event.delete', 'success')
            else:
                return self.send_json({"error": "操作不存在"}, HTTPStatus.NOT_FOUND)
            runtime_cache.clear()
            response = {"message": message, "data": cached_dashboard("dashboard")}
            if trusted_native and path == "/api/native/session":
                response["verifiedUserId"] = str(load_json(PROFILE / "login.json", {})["user_id"])
            self.send_json(response)
            if sync_after:
                # Launch only after releasing the connection/setup mutation lock.
                threading.Timer(0.1, start_sync).start()
            return
        except ValueError as exc:
            if path != '/api/native/session': diagnostics().record('operation', 'failure', error_code(exc))
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            if path != '/api/native/session': diagnostics().record('operation', 'failure', error_code(exc))
            return self.send_json({"error": str(exc), "data": dashboard_data()}, HTTPStatus.BAD_GATEWAY)
        except Exception as exc:
            if path != '/api/native/session': diagnostics().record('operation', 'failure', error_code(exc))
            return self.send_json({"error": "操作未完成，请稍后重试或重新连接 Canvas。"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            MUTATION_LOCK.release()

    def save_event(self, payload):
        courses = {x.get("code") for x in load_json(PROFILE / "config.json", {}).get("courses") or []}
        course = str(payload.get("course") or "").strip()
        title = str(payload.get("title") or "").strip()
        date = str(payload.get("date") or "").strip()
        time = str(payload.get("time") or "").strip()
        weight = str(payload.get("weight") or "").strip()
        url = str(payload.get("url") or "").strip()
        note = str(payload.get("note") or "").strip()
        original = payload.get("original")
        kind = payload.get('kind')
        if kind is not None and (not isinstance(kind, str) or kind != '' and kind not in KINDS):
            raise ValueError('请选择有效的安排分类')
        if course not in courses or not title or len(title) > 120 or len(note) > 500:
            raise ValueError("请选择课程并填写简短的事项名称")
        try:
            if datetime.fromisoformat(date).date().isoformat() != date:
                raise ValueError
        except ValueError:
            raise ValueError("请填写有效日期") from None
        if time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
            raise ValueError("时间格式应为 HH:MM")
        if weight and not re.fullmatch(r"\d{1,3}(?:\.\d+)?%", weight):
            raise ValueError("权重格式示例：30%")
        if url and url != safe_url(url):
            raise ValueError("来源链接需为 PolyU Canvas 的 HTTPS 地址")
        if NATIVE and not url:
            raise ValueError("请填写 Canvas 来源链接，以便核对这项安排。")
        source = "Canvas / 来源核对记录"
        state = load_json(PROFILE / "state.json", {"manual_deadlines": []})
        manual = list(state.get("manual_deadlines") or [])
        if original is not None:
            if not isinstance(original, dict):
                raise ValueError("这项安排已被改动，请刷新后重试")
            orig_course = str(original.get("course") or "").strip()
            orig_title = str(original.get("title") or "").strip()
            orig_indices = [i for i, x in enumerate(manual) if x.get("course") == orig_course and x.get("item") == orig_title]
            if len(orig_indices) != 1:
                raise ValueError("这项安排已被改动，请刷新后重试")
            orig_idx = orig_indices[0]
            orig_row = manual[orig_idx]
            if (orig_row.get("source") or "").startswith(DOCUMENT_DEADLINE_PREFIX):
                raise ValueError("此安排来自课程文件扫描，请打开 Canvas 来源核对。")
            if (course, title) != (orig_course, orig_title) and any(
                i != orig_idx and x.get("course") == course and x.get("item") == title
                for i, x in enumerate(manual)
            ):
                raise ValueError("同课程已有同名安排，请编辑现有事项。")
            source = orig_row.get("source") or source
            if not weight:
                weight = orig_row.get("weight") or ""
        previous_kind = orig_row.get('kind') if original is not None else None
        selected_kind = kind or (previous_kind if previous_kind in KINDS else classify(title))
        row = {"course": course, "item": title, "date": date, "time": time or None,
               "time_text": "" if time else "具体时间待确认", "weight": weight or "—",
               "url": url, "note": note, "source": source,
               "kind": selected_kind, "status": "待参加" if selected_kind != "assignment" else "未交"}
        if original is not None:
            manual[orig_idx] = row
        elif any(item.get("course") == course and item.get("item") == title for item in manual):
            raise ValueError("同课程已有同名安排，请编辑现有事项。")
        else:
            manual.append(row)
        save_private(PROFILE / "state.json", {"manual_deadlines": manual})
        return "安排已保存。"

    def delete_event(self, payload):
        course = str(payload.get("course") or "")
        title = str(payload.get("title") or "")
        manual = load_json(PROFILE / "state.json", {}).get("manual_deadlines") or []
        index = next((i for i, row in enumerate(manual, 1)
                      if row.get("course") == course and row.get("item") == title), None)
        if index is None:
            raise ValueError("这项手动安排已不存在")
        if (manual[index-1].get("source") or "").startswith(DOCUMENT_DEADLINE_PREFIX):
            raise ValueError("此安排来自课程文件扫描，请打开 Canvas 来源核对。")
        manual.pop(index-1)
        save_private(PROFILE / "state.json", {"manual_deadlines": manual})
        return "手动安排已删除。"


def main():
    if not NATIVE:
        raise SystemExit("请从 PolyU study控制台桌面应用启动。")
    if len(NATIVE_SECRET) < 32:
        raise SystemExit("原生会话凭据缺失，拒绝启动。")
    backend_session()
    if PROJECT.is_symlink() or PROFILE.is_symlink():
        raise SystemExit("账号目录不能是链接。")
    os.umask(0o077)
    PROJECT.mkdir(parents=True, exist_ok=True, mode=0o700)
    migrate_legacy_profile()
    PROFILE.mkdir(parents=True, exist_ok=True, mode=0o700)
    PROFILE.chmod(0o700)
    diagnostics().record('backend.start', 'success')
    chosen = os.environ.get("POLYU_CONSOLE_PORT")
    ports = [int(chosen)] if chosen else ([0] if NATIVE else range(8765, 8785))
    server = None
    for port in ports:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError as exc:
            if exc.errno != 48:
                raise
    if server is None:
        raise SystemExit("8765–8784 端口都已被占用，请稍后重试。")
    port = server.server_port
    url = f"http://127.0.0.1:{port}/"
    print(f"PolyU 学习控制台已启动：{url}", flush=True)
    ready = os.environ.get("POLYU_READY_FILE")
    if ready:
        save_private(ready, {"url": url, "pid": os.getpid()})
    if NATIVE:
        threading.Thread(target=automatic_sync, daemon=True).start()
    threading.Thread(target=calendar_maintenance, daemon=True).start()
    if "--no-open" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n控制台已关闭。", flush=True)
    finally:
        runtime_cache.STOP.set()
        runtime_cache.WAKE.set()
        server.server_close()


if __name__ == "__main__":
    main()
