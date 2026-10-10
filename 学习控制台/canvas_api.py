"""Small, read-only Canvas API client used by the native study console."""
from __future__ import annotations

import json
import http.client
import posixpath
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from app_config import VERSION


class CanvasAuthError(RuntimeError):
    pass


class CanvasPagingError(RuntimeError):
    pass


def require_readonly_url(url):
    """Reject Canvas GET routes that create folders, including format aliases."""
    path = urllib.parse.urlsplit(url).path
    for _ in range(8):
        decoded = urllib.parse.unquote(path)
        if decoded == path:
            break
        path = decoded
    else:
        raise ValueError("Canvas 路径编码层数异常")
    path = posixpath.normpath(path.replace("\\", "/")).rstrip("/").casefold()
    if re.search(r"/folders/(?:media|icon_maker)(?:\.[^/]*)?$", path):
        raise ValueError("拒绝会创建目录的 Canvas 读取端点")


class Canvas:
    def __init__(self, host, token=None, timeout=25, retries=1, max_pages=1000):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.max_pages = max_pages
        self.opener = urllib.request.build_opener()

    def url_of(self, path):
        url = path if path.startswith("https://") else self.host + path
        parsed = urllib.parse.urlsplit(url)
        host = urllib.parse.urlsplit(self.host).hostname
        if parsed.scheme != "https" or parsed.hostname != host or parsed.port not in (None, 443) or not parsed.path.startswith("/api/v1/"):
            raise ValueError("Canvas API 请求仅允许学校的只读接口。")
        return url

    def fetch(self, url, accept="application/json"):
        require_readonly_url(url)
        headers = {"Accept": accept, "User-Agent": f"PolyUStudyConsole/{VERSION} (read-only)"}
        request = urllib.request.Request(url, headers=headers, method="GET")
        for attempt in range(self.retries + 1):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    return response.headers, response.read()
            except urllib.error.HTTPError as error:
                if error.code == 401:
                    raise CanvasAuthError("Canvas 登录已过期，请在应用中重新连接。") from None
                if error.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise
                retry = error.headers.get("Retry-After", "")
                delay = min(float(retry), 20) if retry.replace(".", "", 1).isdigit() else 2 ** attempt
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead):
                if attempt == self.retries:
                    raise
                delay = 2 ** attempt
            time.sleep(delay)
        raise RuntimeError("Canvas 请求未完成。")

    @staticmethod
    def next_link(headers):
        for part in (headers.get("Link") or "").split(","):
            segments = part.strip().split(";")
            if len(segments) >= 2 and any('rel="next"' in item for item in segments[1:]):
                return segments[0].strip().strip("<>")
        return None

    def get(self, path, max_pages=None):
        url = self.url_of(path)
        rows = []
        limit = self.max_pages if max_pages is None else max_pages
        for _ in range(limit):
            headers, body = self.fetch(url)
            value = json.loads(body.decode("utf-8") or "null")
            if not isinstance(value, list):
                return value
            rows.extend(value)
            next_url = self.next_link(headers)
            if not next_url:
                return rows
            url = self.url_of(next_url)
        raise CanvasPagingError(f"Canvas 分页超过 {limit} 页：{path.split('?')[0]}")
