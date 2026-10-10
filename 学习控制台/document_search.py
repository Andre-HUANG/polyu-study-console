"""Search this account's already extracted text; never fetch or upload content."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from platform_support import is_link


def _json(path, root, default):
    try:
        path, root = Path(path), Path(root)
        relative = path.relative_to(root)
        components = [root, *[root.joinpath(*relative.parts[:n]) for n in range(1, len(relative.parts)+1)]]
        if not path.resolve().is_relative_to(root.resolve()) or any(is_link(p) for p in components):
            return default
        if path.stat().st_size > 32 * 1024 * 1024: return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError): return default


def _excerpt(text, terms):
    folded = text.casefold()
    position = min((folded.find(t) for t in terms if t in folded), default=0)
    start = max(0, position - 80)
    return ("…" if start else "") + text[start:start+300].strip() + ("…" if len(text) > start+300 else "")


def search(profile, query, limit=40):
    profile = Path(profile)
    query = str(query).strip()
    if not query or len(query) > 100: raise ValueError("请输入 1–100 个字符的搜索词")
    terms = query.casefold().split()
    if len(terms) > 8: raise ValueError("一次最多搜索 8 个词")
    config = _json(profile / "config.json", profile, {})
    account = str((config.get("user") or {}).get("id") or "")
    if not account: raise ValueError("请先连接 Canvas 账号")
    root = profile / "documents"
    index = _json(root / "index.json", root, {})
    if index.get("accountId") is not None and str(index["accountId"]) != account:
        raise ValueError("资料索引与当前账号不一致，请重新同步")
    courses = {str(c.get("id")) for c in config.get("courses") or [] if not c.get("inactive")}
    results, checked, skipped, size, truncated = [], 0, 0, 0, False
    limit = max(1, min(int(limit), 100))
    records = index.get("records") or []
    for doc in records[:5000]:
        cid, fid = str(doc.get("courseId") or ""), str(doc.get("fileId") or "")
        if not cid.isdigit() or cid not in courses: continue
        try:
            url = urlsplit(doc.get("url") or "")
            if url.scheme != "https" or url.hostname != "canvas.polyu.edu.hk" or url.port not in (None, 443) or url.username or url.password: continue
            if not (url.path == f"/courses/{cid}" or url.path.startswith(f"/courses/{cid}/")): continue
        except ValueError: continue
        metadata = " ".join(str(doc.get(k) or "") for k in ("course", "title"))
        pages = []
        if fid.isdigit():
            saved = _json(root / "cache" / f"{cid}-{fid}.json", root, {})
            if not doc.get("fingerprint") or saved.get("fingerprint") == doc["fingerprint"]:
                pages = saved.get("pages") or []
        elif doc.get("type") != "external":
            digest = hashlib.sha256(url.path.encode()).hexdigest()
            saved = _json(root / "web" / cid / (digest + ".json"), root, {})
            if str(saved.get("accountId")) == account and saved.get("path") == url.path:
                rows = saved.get("rows") or []
                pages = [{"page": n//100+1, "locator": f"网页正文行 {n+1}–{min(n+100,len(rows))}",
                          "text": "\n".join(rows[n:n+100])} for n in range(0, len(rows), 100)]
        if not pages: skipped += 1
        for page in pages:
            text = "\n".join([str(page.get("text") or ""), *[str(v) for v in page.get("ocrRows") or []]])
            size += len(text)
            checked += 1
            if size > 25_000_000:
                truncated = True
                break
            haystack = (metadata + " " + text).casefold()
            if all(term in haystack for term in terms):
                results.append({"id": doc.get("id"), "course": doc.get("course"), "title": doc.get("title"),
                    "url": doc.get("url"), "status": doc.get("status"), "readAt": doc.get("readAt"),
                    "page": page.get("page"), "locator": page.get("locator") or f"第 {page.get('page')} 页",
                    "excerpt": _excerpt(text or metadata, terms)})
                if len(results) >= limit:
                    truncated = True
                    break
        if not pages and all(term in metadata.casefold() for term in terms):
            results.append({"id": doc.get("id"), "course": doc.get("course"), "title": doc.get("title"),
                "url": doc.get("url"), "status": doc.get("status"), "readAt": doc.get("readAt"),
                "page": None, "locator": "Canvas 文件名 / 标题", "excerpt": metadata})
        if truncated or len(results) >= limit: break
    return {"query": query, "results": results, "cachedOnly": True, "pagesChecked": checked,
            "unavailableTexts": skipped, "truncated": truncated or len(records) > 5000,
            "indexedAt": index.get("completedAt"), "scanning": bool(index.get("scanning"))}
