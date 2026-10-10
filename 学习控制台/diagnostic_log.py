"""Bounded diagnostic events, with no free-form input or private response text."""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import uuid

from native_canvas import save_private
from platform_support import is_link

LIMIT = 200
EVENTS = {
    "backend.start": "学习服务启动", "backend.stop": "学习服务停止",
    "login.open": "打开登录窗口", "login.close": "关闭登录窗口",
    "login.export": "连接登录会话", "login.verify": "验证 Canvas 登录",
    "logout": "退出登录", "logout.cleanup": "删除账号数据",
    "sync": "更新 Canvas", "settings.sync": "修改自动同步设置",
    "event.save": "保存个人安排", "event.delete": "删除个人安排",
    "operation": "本机操作",
}
RESULTS = {"attempt": "开始", "success": "成功", "failure": "失败", "cancelled": "已取消", "partial": "部分完成"}
CODES = {
    "": "", "INVALID_INPUT": "输入或会话无效", "SESSION_EXPIRED": "Canvas 需要重新登录",
    "NETWORK_ERROR": "网络请求未完成", "TIMEOUT": "操作超时", "IO_ERROR": "本机文件操作未完成",
    "INTERNAL_ERROR": "内部操作未完成", "SERVICE_UNAVAILABLE": "本机服务不可用",
    "TEST_MODE": "隔离测试未访问学校", "PARTIAL_DATA": "部分数据读取未完成",
    "HTTP_400": "请求未被接受", "HTTP_403": "请求未获授权", "HTTP_409": "已有操作正在进行",
    "HTTP_502": "服务请求未完成", "HTTP_500": "内部服务错误",
}


def error_code(error):
    # Never inspect, serialize, or retain the exception message.
    from canvas_api import CanvasAuthError
    from urllib.error import URLError
    if isinstance(error, CanvasAuthError): return "SESSION_EXPIRED"
    if isinstance(error, TimeoutError): return "TIMEOUT"
    if isinstance(error, URLError): return "NETWORK_ERROR"
    if isinstance(error, ValueError): return "INVALID_INPUT"
    if isinstance(error, OSError): return "IO_ERROR"
    return "INTERNAL_ERROR"


def validate_event(event, result, code=""):
    if any(not isinstance(v, str) for v in (event, result, code)) or event not in EVENTS or result not in RESULTS or code not in CODES:
        raise ValueError("日志事件无效")


class DiagnosticLog:
    def __init__(self, profile, version):
        self.profile, self.version = profile, version
        self.path = profile / "diagnostics/events.json"
        self.events = deque(maxlen=LIMIT)
        self.scope = None
        self.storage_issue = False
        self.lock = threading.RLock()

    def safe_path(self):
        return not any(is_link(p) for p in (self.profile.parent, self.profile, self.path.parent, self.path))

    def valid_saved(self, row):
        if not isinstance(row, dict) or set(row) != {"id", "at", "version", "event", "result", "code", "origin"}: return False
        try:
            validate_event(row["event"], row["result"], row["code"])
            return (bool(re.fullmatch(r"[0-9a-f]{32}", row["id"])) and
                    bool(re.fullmatch(r"\d+\.\d+\.\d+", row["version"])) and
                    row["origin"] in {"native", "backend"} and len(row["at"]) <= 40 and
                    datetime.fromisoformat(row["at"]).tzinfo is not None)
        except (TypeError, ValueError): return False

    def for_account(self, account_id):
        scope = hashlib.sha256(str(account_id).encode()).hexdigest() if account_id is not None else None
        with self.lock:
            if scope == self.scope: return self
            # Account switches discard all history. Before first login, retain
            # only this login's events; no prior logout/backend history follows it.
            pending = list(self.events) if self.scope is None and scope else []
            login_start = next((i for i in range(len(pending)-1, -1, -1)
                                if pending[i]["event"] == "login.open" and pending[i]["result"] == "attempt"), None)
            if login_start is None:
                login_start = next((i for i in range(len(pending)-1, -1, -1)
                                    if pending[i]["event"] == "login.verify" and pending[i]["result"] == "attempt"), len(pending))
            pending = [e for e in pending[login_start:] if e["event"].startswith("login.")]
            self.events.clear()
            self.scope = scope
            self.storage_issue = False
            if scope and self.safe_path() and self.path.is_file():
                try:
                    if self.path.stat().st_size > 160000: raise ValueError("Log exceeds bound")
                    saved = json.loads(self.path.read_text(encoding="utf-8"))
                    if saved.get("scope") == scope:
                        self.events.extend(e for e in saved.get("events", [])[-LIMIT:] if self.valid_saved(e))
                except (OSError, ValueError, TypeError, AttributeError): self.storage_issue = True
            self.events.extend(pending)
            if scope: self.persist()
            return self

    def persist(self):
        if not self.scope: return
        if not self.safe_path():
            self.storage_issue = True
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.parent.chmod(0o700)
            save_private(self.path, {"scope": self.scope, "events": list(self.events)})
            self.storage_issue = False
        except OSError: self.storage_issue = True

    def record(self, event, result, code="", origin="backend"):
        validate_event(event, result, code)
        if origin not in {"native", "backend"}: raise ValueError("日志来源无效")
        with self.lock:
            row = {"id": uuid.uuid4().hex, "at": datetime.now(timezone.utc).isoformat(),
                   "version": self.version, "event": event, "result": result, "code": code, "origin": origin}
            self.events.append(row)
            self.persist()
        return row

    def clear(self):
        with self.lock:
            self.events.clear()
            self.persist()

    def reset(self):
        with self.lock:
            self.scope = None
            self.events.clear()
            self.storage_issue = False

    def summary(self):
        with self.lock:
            return {"version": self.version, "limit": LIMIT, "persistent": bool(self.scope),
                    "storageIssue": self.storage_issue,
                    "events": [{**e, "label": EVENTS[e["event"]], "resultLabel": RESULTS[e["result"]],
                                "codeLabel": CODES[e["code"]]} for e in reversed(self.events)]}
