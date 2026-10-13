"""Read-only Canvas transport for the Mac application's WebKit login."""
from __future__ import annotations

import http.cookiejar
import http.client
import json
import base64
import hashlib
import threading
from email.utils import parsedate_to_datetime
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from platform_support import IS_WINDOWS, dpapi
from app_config import VERSION

HOST = "https://canvas.polyu.edu.hk"


def material_host(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443) and (
        host == "canvas.polyu.edu.hk" or any(host == domain or host.endswith("." + domain)
        for domain in ("instructure.com", "instructuremedia.com", "canvas-user-content.com", "inscloudgate.net", "amazonaws.com", "cloudfront.net")))


def save_private(path, value):
    path = Path(path)
    if path.name == "canvas-cookies.json":
        from session_store import protect_session
        value = protect_session(path, value, windows=IS_WINDOWS, protector=dpapi)
    write_private(path, value)


def write_private(path, value):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("账号写入路径不能是链接。")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(16) + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=1)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


class DownloadGate:
    """A 429/503 pauses queued transfers as well as the current retry."""
    def __init__(self):
        self.lock, self.until = threading.Lock(), 0.0

    def defer(self, delay):
        with self.lock:
            self.until = max(self.until, time.monotonic() + delay)

    def wait(self, cancelled):
        while True:
            cancelled()
            with self.lock: remaining = self.until - time.monotonic()
            if remaining <= 0: return
            time.sleep(min(.25, remaining))


def retry_delay(value, attempt):
    try:
        delay = float(value)
        if delay >= 0 and delay < float('inf'): return delay
    except (TypeError, ValueError): pass
    try:
        return max(0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError, OverflowError): return 2 ** attempt


def load_cookies(path):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("会话路径不能是链接。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise RuntimeError("无法读取会话文件；原账号资料保持不变。") from None
    from session_store import read_session
    return read_session(path, value, windows=IS_WINDOWS, protector=dpapi)


def normalized_cookies(rows):
    """Keep only cookies applicable to Canvas; never export the SSO site's cookies."""
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        domain = str(row.get("domain", "")).lstrip(".").lower()
        if domain not in {"canvas.polyu.edu.hk", "polyu.edu.hk"}:
            continue
        name, value = row.get("name"), row.get("value")
        if not isinstance(name, str) or not isinstance(value, str) or len(name) > 256 or len(value) > 16384:
            continue
        if any(c in name + value for c in ("\r", "\n", "\x00")):
            continue
        expires = row.get("expires", -1)
        if not isinstance(expires, (int, float)):
            continue
        if expires > 0 and expires <= time.time():
            continue
        result.append({"name": name, "value": value, "domain": "canvas.polyu.edu.hk",
                       "path": str(row.get("path") or "/"), "secure": True,
                       "httpOnly": bool(row.get("httpOnly")), "expires": expires})
    return result


def session_class():
    from canvas_api import Canvas, CanvasAuthError, require_readonly_url

    class CanvasCookies(urllib.request.HTTPCookieProcessor):
        def http_request(self, request):
            if urllib.parse.urlsplit(request.full_url).hostname == 'canvas.polyu.edu.hk':
                return super().http_request(request)
            for name in ('Cookie', 'Authorization', 'Referer'): request.remove_header(name)
            return request

        def http_response(self, request, response):
            if urllib.parse.urlsplit(request.full_url).hostname == 'canvas.polyu.edu.hk':
                return super().http_response(request, response)
            return response

        https_request = http_request
        https_response = http_response

    class CanvasRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            target = urllib.parse.urlsplit(newurl)
            if target.hostname != "canvas.polyu.edu.hk" or target.scheme != "https" or target.port not in (None, 443) or target.username or target.password:
                raise CanvasAuthError("Canvas 登录已过期，请在应用中重新连接。")
            require_readonly_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    class FileRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not material_host(newurl):
                raise RuntimeError("文件跳转至非 Canvas 文件服务器，需在 Canvas 中核对。")
            if urllib.parse.urlsplit(newurl).hostname == "canvas.polyu.edu.hk":
                require_readonly_url(newurl)
            result = super().redirect_request(req, fp, code, msg, headers, newurl)
            if result and urllib.parse.urlsplit(newurl).hostname != "canvas.polyu.edu.hk":
                for name in ("Cookie", "Authorization", "Referer"):
                    result.remove_header(name)
            return result

    class NativeCanvas(Canvas):
        mode = "session"
        download_workers = 3

        def __init__(self, host, home, cookies=None, persist=True, **kwargs):
            if host.rstrip("/") != HOST:
                raise ValueError("此应用仅连接香港理工大学 Canvas。")
            super().__init__(HOST, None, **kwargs)
            self.cookie_path = Path(home) / "browser/canvas-cookies.json"
            self.persist = persist
            self._download_local = threading.local()
            self._download_gate = DownloadGate()
            self._cookie_save_lock = threading.Lock()
            self._saved_cookies = None
            self.jar = http.cookiejar.CookieJar()
            rows = normalized_cookies(cookies if cookies is not None else load_cookies(self.cookie_path))
            for row in rows:
                expires = int(row["expires"]) if row["expires"] > 0 else None
                self.jar.set_cookie(http.cookiejar.Cookie(
                    0, row["name"], row["value"], None, False, "canvas.polyu.edu.hk", False, False,
                    row["path"], True, True, expires, expires is None, None, None,
                    {"HttpOnly": None} if row["httpOnly"] else {}, False))
            self.opener = urllib.request.build_opener(CanvasRedirect(), CanvasCookies(self.jar))

        @property
        def last_download(self):
            return getattr(self._download_local, 'result', {})

        @last_download.setter
        def last_download(self, value):
            self._download_local.result = value

        def fetch(self, url, accept="application/json", validators=None):
            target = urllib.parse.urlsplit(url)
            if target.hostname != "canvas.polyu.edu.hk" or target.scheme != "https" or target.port not in (None, 443) or target.username or target.password:
                raise ValueError("Canvas API 请求仅允许学校 Canvas 域名。")
            try:
                headers, body = super().fetch(url, accept, validators)
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    raise CanvasAuthError("Canvas 登录已过期，请在应用中重新连接。") from None
                raise
            if accept == "application/json": body = body.lstrip()
            if not body.startswith((b"{", b"[", b"while(1);")) and accept == "application/json":
                raise CanvasAuthError("Canvas 登录已过期，请在应用中重新连接。")
            self.close()
            return headers, body.removeprefix(b"while(1);")

        def download_file(self, url, destination, maximum=None, cancelled=lambda: None, validators=None):
            """Read a Canvas-authorized file; cookies never follow a CDN redirect."""
            if not material_host(url):
                raise RuntimeError("文件地址不是受支持的 Canvas 文件服务器。")
            if urllib.parse.urlsplit(url).hostname == "canvas.polyu.edu.hk":
                require_readonly_url(url)
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = destination.parent / (".download-" + secrets.token_hex(16))
            opener = urllib.request.build_opener(FileRedirect(), CanvasCookies(self.jar))
            self.last_download = {}
            headers = {"Accept": "*/*", "User-Agent": f"PolyUStudyConsole/{VERSION} (read-only)"}
            etag = (validators or {}).get("etag")
            conditional = isinstance(etag, str) and 0 < len(etag) < 2048 and not any(c in etag for c in "\r\n\x00") and destination.is_file() and not destination.is_symlink()
            if conditional: headers["If-None-Match"] = etag
            request = urllib.request.Request(url, headers=headers)
            for attempt in range(3):
                self._download_gate.wait(cancelled)
                try:
                    with opener.open(request, timeout=60) as response:
                        length = response.headers.get("Content-Length")
                        expected = int(length) if length and length.isdigit() else None
                        if maximum is not None and expected is not None and expected > maximum:
                            raise RuntimeError("文件超过下载大小限制。")
                        # Never cache a login/error HTML page as a PDF/Office file.
                        content_type = response.headers.get_content_type()
                        response_etag = response.headers.get("ETag")
                        if content_type == "text/html" and destination.suffix.lower() not in {".html", ".htm"}:
                            raise RuntimeError("下载地址返回网页，正在刷新文件地址重试")
                        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        size = 0
                        digest = hashlib.sha256()
                        with os.fdopen(fd, "wb") as stream:
                            while True:
                                cancelled()
                                block = response.read(1024 * 1024)
                                if not block: break
                                size += len(block)
                                if maximum is not None and size > maximum: raise RuntimeError("文件超过下载大小限制。")
                                stream.write(block)
                                digest.update(block)
                        if expected is not None and size != expected:
                            raise http.client.IncompleteRead(b"", expected-size)
                        if not size: raise RuntimeError("下载文件为空，正在刷新文件地址重试")
                    os.replace(temporary, destination)
                    destination.chmod(0o600)
                    self.close()
                    self.last_download = {"transfer": "downloaded", "etag": response_etag, 'sha256': digest.hexdigest()}
                    return size
                except urllib.error.HTTPError as error:
                    if error.code == 304 and conditional:
                        self.close()
                        self.last_download = {"transfer": "revalidated", "etag": etag}
                        return destination.stat().st_size
                    if error.code == 401 and urllib.parse.urlsplit(error.url).hostname == "canvas.polyu.edu.hk":
                        raise CanvasAuthError("Canvas 登录已过期，请重新登录。") from None
                    if error.code not in {429, 500, 502, 503, 504}: raise
                    delay = retry_delay(error.headers.get("Retry-After", ""), attempt)
                    self._download_gate.defer(delay)
                    if attempt == 2: raise
                except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead):
                    if attempt == 2: raise
                    delay = 2 ** attempt
                finally:
                    temporary.unlink(missing_ok=True)
                # Small cancellable waits; logging out never leaves a downloader.
                until = time.monotonic() + delay
                while time.monotonic() < until:
                    cancelled(); time.sleep(min(.25, max(0, until-time.monotonic())))

        def close(self):
            if not self.persist:
                return
            with self._cookie_save_lock:
                rows = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                         "secure": c.secure, "httpOnly": c.has_nonstandard_attr("HttpOnly"),
                         "expires": c.expires if c.expires is not None else -1} for c in self.jar]
                rows = normalized_cookies(rows)
                if rows != self._saved_cookies or not self.cookie_path.is_file():
                    save_private(self.cookie_path, rows)
                    self._saved_cookies = rows

    return NativeCanvas
