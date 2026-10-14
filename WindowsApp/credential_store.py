"""Opt-in, user-DPAPI credentials owned exclusively by the native shell."""
import base64
import json
import os
import secrets
import threading
from functools import wraps
from pathlib import Path
from platform_support import dpapi, is_link

CONSENT = "允许本应用加密保存你的学校账号和密码，用于下次填入学校登录页面。建议仅在个人电脑开启。"
FAILURE = "本机密码保护或清理暂不可用；没有改用明文。请检查系统权限后重试。"


def safe_error():
    return RuntimeError(FAILURE)


def masked(username):
    return username[:1] + "***" + (username[-1:] if len(username) > 1 else "")


def synchronized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.lock: return method(self, *args, **kwargs)
    return guarded


class RememberedCredentials:
    def __init__(self, root, protector=dpapi):
        self.root = Path(root)
        self.directory = self.root / "credentials"
        self.settings = self.directory / "consent.json"
        self.file = self.directory / "protected.bin"
        self.protector = protector
        self.lock = threading.RLock()

    def _paths(self):
        if any(is_link(p) for p in (self.root, self.directory, self.settings, self.file)):
            raise safe_error()

    def _state(self):
        self._paths()
        if not self.settings.exists(): return {"version": 1, "enabled": False}
        try:
            state = json.loads(self.settings.read_text(encoding="utf-8"))
            if state.get("version") != 1 or type(state.get("enabled")) is not bool: raise ValueError()
            return state
        except Exception: raise safe_error() from None

    def _write(self, path, data):
        self._paths()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.directory / (".write-" + secrets.token_hex(16))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            self._paths()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _save_state(self, state):
        self._write(self.settings, json.dumps(state, ensure_ascii=False).encode("utf-8"))

    @synchronized
    def enable(self):
        self.resume_purge()
        self._save_state({"version": 1, "enabled": True, "consentVersion": 1})

    @synchronized
    def resume_purge(self):
        if self._state().get("purgePending"): self.forget()

    @synchronized
    def forget(self):
        self._save_state({"version": 1, "enabled": False, "purgePending": True})
        try:
            self._paths(); self.file.unlink(missing_ok=True)
            if self.file.exists(): raise safe_error()
            self._save_state({"version": 1, "enabled": False})
        except Exception: raise safe_error() from None

    @synchronized
    def record(self):
        self.resume_purge()
        state = self._state()
        if not state["enabled"] or state.get("consentVersion") != 1: return None
        if not self.file.exists(): return None
        try:
            row = json.loads(self.protector(self.file.read_bytes(), decrypt=True))
            if (row.get("version") != 1 or not isinstance(row.get("username"), str) or
                not 0 < len(row["username"]) <= 512 or not isinstance(row.get("password"), str) or
                not 0 < len(row["password"]) <= 4096 or not isinstance(row.get("verifiedUserId"), str) or
                not row["verifiedUserId"].isdigit()): raise ValueError()
            return row
        except Exception: raise safe_error() from None

    @synchronized
    def save_verified(self, username, password, user_id):
        state = self._state()
        if not state["enabled"] or state.get("consentVersion") != 1: raise safe_error()
        if not isinstance(username, str) or not 0 < len(username) <= 512 or not isinstance(password, str) or not 0 < len(password) <= 4096:
            raise safe_error()
        user_id = str(user_id)
        if not user_id.isdigit(): raise safe_error()
        row = {"version": 1, "username": username, "password": password, "verifiedUserId": user_id}
        try:
            encoded = json.dumps(row, ensure_ascii=False).encode("utf-8")
            encrypted = self.protector(encoded)
            # Verify protection before replacing the old credential file.
            if not encrypted or encrypted == encoded or self.protector(encrypted, decrypt=True) != encoded: raise ValueError()
            self._paths(); self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.directory / (".verify-" + secrets.token_hex(16))
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
                if self.protector(temporary.read_bytes(), decrypt=True) != encoded: raise ValueError()
                self._paths(); os.replace(temporary, self.file)
            finally: temporary.unlink(missing_ok=True)
        except Exception: raise safe_error() from None

    @synchronized
    def status(self, owner=None):
        try:
            row = self.record(); state = self._state()
            mismatch = bool(row and owner and str(owner) != row["verifiedUserId"])
            return {"enabled": state["enabled"], "state": "saved" if row and not mismatch else "pending" if state["enabled"] else "off",
                    "maskedUsername": masked(row["username"]) if row else "",
                    "differentAccount": mismatch}
        except Exception:
            return {"enabled": False, "state": "unavailable", "maskedUsername": ""}


def sso_url(value):
    from urllib.parse import urlsplit
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and url.hostname == "adfs.polyu.edu.hk" and url.port in (None, 443)
                and not url.username and not url.password and url.path == "/adfs/ls/")
    except (TypeError, ValueError): return False


class LoginAttempt:
    def __init__(self):
        self.nonce = secrets.token_hex(32)
        self.document = None
        self.candidate = None
        self.filled = False
        self.active = True

    def close(self):
        self.active = False; self.candidate = None; self.document = None

    def receive(self, source, message, enabled):
        if (not self.active or not enabled or not sso_url(source) or not isinstance(message, dict) or
            message.get("type") != "polyuRemember" or message.get("nonce") != self.nonce): return False
        document = message.get("documentId")
        if not isinstance(document, str) or len(document) != 32 or any(c not in "0123456789abcdef" for c in document): return False
        if message.get("kind") == "ready" and set(message) == {"type", "kind", "nonce", "documentId"}:
            self.document = document; self.candidate = None
            return True
        if (message.get("kind") != "candidate" or document != self.document or
            set(message) != {"type", "kind", "nonce", "documentId", "username", "password"}): return False
        username, password = message.get("username"), message.get("password")
        if not isinstance(username, str) or not 0 < len(username) <= 512 or not isinstance(password, str) or not 0 < len(password) <= 4096: return False
        self.candidate = (username, password)
        return True


def read_logout_policy(root):
    try:
        path = Path(root) / "logout-pending"
        if is_link(path): raise safe_error()
        row = json.loads(path.read_text(encoding="utf-8"))
        return row.get("version") == 2 and row.get("keepRememberedPassword") is True
    except (ValueError, OSError): return False
