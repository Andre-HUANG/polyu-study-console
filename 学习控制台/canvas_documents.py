"""Enumerate and read the student's course content, retaining Canvas evidence."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.error
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

from course_evidence import VERSION, consolidate, facts_from
from document_text import READER_VERSION, SUPPORTED, extract, html_text
from assessment_reader import document_findings
from native_canvas import HOST, save_private

MAXIMUM = 250 * 1024 * 1024


def file_suffix(meta):
    # Display names are free-form Canvas labels, often with no true extension.
    for name in (meta.get("filename"), meta.get("display_name")):
        suffix = Path(unquote(name or "")).suffix.lower()
        if suffix in SUPPORTED: return suffix
    mime = (meta.get("content-type") or meta.get("content_type") or "").split(";")[0].lower()
    return {"application/pdf": ".pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tif", "text/html": ".html",
        "text/plain": ".txt", "text/csv": ".csv", "application/zip": ".zip"}.get(mime, "")


def download_document(api, meta, cid, fid, blob, cancelled):
    from canvas_api import CanvasAuthError
    # Signed CDN URLs can expire between enumerating a course and reading its
    # last lecture. Refresh metadata once, then use Canvas's authenticated URL.
    current = meta
    for attempt in range(3):
        cancelled()
        url = current.get("url") if attempt < 2 else None
        try:
            api.download_file(url or HOST + f"/courses/{cid}/files/{fid}/download", blob, MAXIMUM, cancelled)
            return
        except CanvasAuthError:
            raise
        except Exception:
            if attempt == 2: raise
            if attempt == 0:
                current = api.get(f"/api/v1/files/{fid}")
                if current.get("locked_for_user"):
                    raise RuntimeError("文件目前未向本账号开放") from None


def read_json(path, default):
    try: return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError): return default


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def canonical(value, fallback):
    parsed = urlsplit(value or "")
    return HOST + parsed.path if parsed.scheme == "https" and parsed.hostname == "canvas.polyu.edu.hk" else fallback


def course_config(config, info):
    term = info.get("term") or {}
    saved = config.get("term") or {}
    start, end = term.get("start_at") or info.get("start_at"), term.get("end_at") or info.get("end_at")
    return {**config, "term": {**saved, "name": " ".join(str(v) for v in (term.get("name") or saved.get("name"), info.get("name"), info.get("course_code")) if v),
        "start": start or saved.get("start"), "end": end or saved.get("end")}}


def describe(document, pages, config, analyzing=False):
    document["pageCount"] = len(pages)
    document["readPages"] = sum(bool(p.get("readable")) for p in pages)
    document["processedPages"] = sum(bool(p.get("processed") or p.get("readable")) for p in pages)
    document["issues"] = list(dict.fromkeys([issue for p in pages for issue in p.get("issues") or []]))
    if not pages: document["issues"].append("正文未能读取，应用将在下次同步重试")
    document["status"] = "analyzing" if analyzing else "partial" if document["issues"] else "read"
    document["methods"] = sorted({p.get("method") for p in pages if p.get("method")})
    title_page = {"page": 0, "locator": "Canvas 标题", "text": document["title"], "rows": [document["title"]], "readable": True}
    document["facts"] = facts_from(document, [title_page, *pages], config)
    document["findings"] = document_findings(document["facts"])
    document["readAt"] = now()
    document["readerVersion"] = READER_VERSION
    return document


def scan_courses(api, profile, config, reader, cancelled, progress, checkpoint=None, followups=0, archive=None):
    if archive is not None:
        return scan_archived_web(api, profile, config, reader, cancelled, progress, checkpoint, archive)
    from canvas_api import CanvasAuthError
    root = Path(profile) / "documents"
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    old = read_json(root / "index.json", {})
    previous = {r["id"]: r for r in old.get("records") or []}
    active_courses = {str(c.get("id")): c for c in config.get("courses") or [] if not c.get("inactive")}
    archived = {}
    if archive is not None:
        for entry in archive.get("entries") or []:
            archived.setdefault(str(entry.get("courseId")), {})[str(entry.get("id"))] = entry
    index = {"accountId": str((config.get("user") or {}).get("id")), "startedAt": now(), "completedAt": None, "scanning": True,
        "version": VERSION,
        "records": [{**r, "status": "cached", "issues": ["沿用本账号上次识别结果，本轮尚未完成复核"]} for r in previous.values() if r.get("courseId") in active_courses],
        "courses": [{"id": cid, "code": c.get("code") or cid, "name": c.get("name") or "", "complete": False, "issues": ["本轮尚未读取此课程"]} for cid,c in active_courses.items()],
        "discoveredReferences": old.get("discoveredReferences") or {},
        "evidence": {"deadlines": [], "pending": [], "grading": {}}}
    live_ids = set()
    image_queue = []
    discovered_more = False

    def publish():
        cancelled()
        index["evidence"] = consolidate(index["records"])
        save_private(root / "index.json", index)

    def apply_checkpoint():
        if checkpoint:
            try:
                checkpoint(index)
                index.pop("integrationIssue", None)
            except Exception as error:
                cancelled()
                index["integrationIssue"] = "文档已识别，安排写入未完成：" + str(error)[-350:]
            save_private(root / "index.json", index)

    def get(path, issues, label, fallback=None):
        cancelled()
        try:
            result = api.get(path, max_pages=1000)
            if fallback is not None and not isinstance(result, type(fallback)):
                raise ValueError("Unexpected Canvas response")
            return result
        except CanvasAuthError: raise
        except Exception:
            issues.append(label + "未能完整读取；应用将在下次同步重试")
            return fallback

    def put(document):
        index["records"] = [r for r in index["records"] if r["id"] != document["id"]]
        index["records"].append(document)
        live_ids.add(document["id"])

    def commit(document, pages, cfg):
        describe(document, pages, cfg, analyzing=any(p.get("textOnly") for p in pages))
        put(document)
        publish()

    try:
        publish()
        for course in config.get("courses") or []:
            if course.get("inactive"): continue
            cancelled()
            cid, code = str(course.get("id") or ""), course.get("code") or ""
            if not cid.isdigit(): continue
            prefix = f"/api/v1/courses/{cid}"
            known_refs = index["discoveredReferences"].get(cid) or {}
            issues, files, page_keys, file_refs = [], {}, set(known_refs.get("pages") or []), set(known_refs.get("files") or [])
            for fid, entry in archived.get(cid, {}).items():
                if not fid.isdigit(): continue
                files[fid] = {"id": fid, "display_name": entry.get("title"), "filename": entry.get("title"),
                    "size": entry.get("size"), "sha256": entry.get("sha256"), "archivePath": (entry.get("paths") or [None])[0],
                    "archiveStatus": entry.get("status"), "archiveIssues": entry.get("issues") or [],
                    "url": entry.get("downloadUrl"), "sourceUrl": entry.get("url"), "folders": entry.get("folders") or []}
            if archive is not None:
                course_download = next((c for c in archive.get("courses") or [] if c.get("id") == cid), None)
                if course_download and course_download.get("issues"):
                    issues.extend(course_download["issues"])
            summary = next(c for c in index["courses"] if c["id"] == cid)
            summary.update(issues=issues)
            progress(f"{code}：正在遍历课程页面、模块、公告与全部文件…")
            info = get(prefix + "?include[]=syllabus_body&include[]=term", issues, "课程提纲", {})
            cfg = course_config(config, info)

            def document(kind, identifier, title, url, updated=None):
                return {"id": f"{cid}:{kind}:{identifier}", "course": code, "courseId": cid, "type": kind,
                    "title": title or "未命名内容", "url": canonical(url, HOST + f"/courses/{cid}"), "updatedAt": updated,
                    "issues": [], "facts": [], "readerVersion": READER_VERSION}

            def references(links, base):
                for link in links:
                    target = urlsplit(urljoin(base, link))
                    if target.hostname != "canvas.polyu.edu.hk": continue
                    path = unquote(target.path)
                    match = re.search(r"/(?:api/v1/)?(?:courses/\d+/)?files/(\d+)", path)
                    if match: file_refs.add(match[1])
                    page = re.search(rf"/courses/{cid}/pages/([^/]+)", path)
                    if page: page_keys.add(page[1])

            def html(kind, identifier, title, body, url, updated=None, locked=False):
                doc = document(kind, identifier, title, url, updated)
                if locked:
                    doc.update(status="locked", issues=["此内容尚未向本账号开放"], pageCount=0, readPages=0)
                    put(doc); publish(); return
                text, links = html_text(body or "")
                references(links, doc["url"])
                rows = [r.strip() for r in (title + "\n" + text).splitlines() if r.strip()]
                pages = [{"page": n//100+1, "locator": f"正文行 {n+1}–{min(n+100,len(rows))}", "text": "\n".join(rows[n:n+100]), "rows": rows[n:n+100], "readable": True, "method": "Canvas 页面正文与表格"} for n in range(0, len(rows), 100)]
                commit(doc, pages, cfg)

            html("syllabus", cid, "课程提纲 · " + (course.get("name") or code), info.get("syllabus_body"), HOST + f"/courses/{cid}/assignments/syllabus", info.get("updated_at"))
            # The front page is sometimes omitted from /pages when that course
            # hides the Pages tab. A missing front page (404) is normal.
            front_key = None
            try:
                front = api.get(prefix + "/front_page")
                if front.get("url"):
                    front_key = str(front["url"])
                    html("page", front_key, front.get("title") or "课程首页", front.get("body"), front.get("html_url"), front.get("updated_at"), front.get("locked_for_user", False))
            except CanvasAuthError: raise
            except urllib.error.HTTPError as error:
                if error.code not in {403, 404}: issues.append("课程首页暂未读取")
            except Exception: issues.append("课程首页暂未读取")
            listed = get(prefix + "/files?per_page=100", issues, "课程全部文件列表", []) if archive is None else []
            for row in listed:
                if isinstance(row, dict) and row.get("id"): files[str(row["id"])] = row
            # If Files is unavailable, module/page references still discover
            # individual files; the failed enumeration remains visibly incomplete.
            if archive is None and not listed and any("全部文件列表" in s for s in issues):
                folders = get(prefix + "/folders?per_page=100", issues, "课程文件夹列表", [])
                for folder in folders:
                    if not isinstance(folder, dict) or not str(folder.get("id") or "").isdigit(): continue
                    for row in get(f"/api/v1/folders/{folder['id']}/files?per_page=100", issues, "文件夹 " + str(folder.get("name") or folder["id"]), []):
                        if isinstance(row, dict) and row.get("id"): files[str(row["id"])] = row
            for row in get(prefix + "/pages?per_page=100", issues, "课程全部页面列表", []):
                if isinstance(row, dict) and row.get("url"): page_keys.add(str(row["url"]))
            modules = get(prefix + "/modules?include[]=items&include[]=content_details&per_page=100", issues, "课程模块列表", [])
            for module in modules:
                if not isinstance(module, dict): continue
                items = module.get("items")
                if not isinstance(items, list) or len(items) < (module.get("items_count") or 0):
                    items = get(prefix + f"/modules/{module['id']}/items?include[]=content_details&per_page=100", issues, "模块 " + str(module.get("name") or ""), [])
                for item in items:
                    if item.get("type") == "File" and item.get("content_id"): file_refs.add(str(item["content_id"]))
                    if item.get("type") == "Page" and item.get("page_url"): page_keys.add(str(item["page_url"]))
                    references([item.get("external_url") or item.get("html_url") or ""], HOST + f"/courses/{cid}")
                    if item.get("type") in {"ExternalUrl", "ExternalTool"}:
                        target = urlsplit(item.get("external_url") or "")
                        if target.hostname and target.hostname != "canvas.polyu.edu.hk":
                            doc = document("external", str(item.get("id")), item.get("title"), item.get("html_url"))
                            doc.update(status="unsupported", issues=["Canvas 外部链接 / 外部教学工具需打开核对"], pageCount=0, readPages=0)
                            put(doc)
            for row in get(prefix + "/assignments?include[]=submission&per_page=100", issues, "作业说明", []):
                html("assignment", row.get("id"), row.get("name"), row.get("description"), row.get("html_url"), row.get("updated_at"), row.get("locked_for_user", False))
            for row in get(prefix + "/discussion_topics?only_announcements=true&per_page=100", issues, "全部课程公告", []):
                html("announcement", row.get("id"), row.get("title"), row.get("message"), row.get("html_url"), row.get("updated_at") or row.get("posted_at"), row.get("locked_for_user", False))
                attachment = row.get("attachment") or {}
                if attachment.get("id"): file_refs.add(str(attachment["id"]))
            for row in get(prefix + "/discussion_topics?per_page=100", issues, "课程讨论说明", []):
                if row.get("is_announcement"): continue
                html("page", "discussion-" + str(row.get("id")), row.get("title") or "课程讨论", row.get("message"), row.get("html_url"), row.get("updated_at"), row.get("locked_for_user", False))
                attachment = row.get("attachment") or {}
                if attachment.get("id"): file_refs.add(str(attachment["id"]))
            for row in get(prefix + "/quizzes?per_page=100", issues, "测验说明", []):
                html("quiz", row.get("id"), row.get("title"), row.get("description"), row.get("html_url"), row.get("updated_at"), row.get("locked_for_user", False))
            seen_pages = {front_key} if front_key else set()
            def read_pages():
                while page_keys - seen_pages:
                    key = sorted(page_keys - seen_pages)[0]; seen_pages.add(key)
                    row = get(prefix + "/pages/" + quote(key, safe=""), issues, "页面 " + key, {})
                    if row:
                        html("page", key, row.get("title") or key, row.get("body"), row.get("html_url"), row.get("updated_at"), row.get("locked_for_user", False))
            read_pages()
            checked_refs = set(files)
            for fid in sorted(file_refs - files.keys()):
                if not fid.isdigit(): continue
                checked_refs.add(fid)
                row = get(f"/api/v1/files/{fid}", issues, "引用文件 " + fid, {})
                if row: files[fid] = row
                else:
                    doc = document("file", fid, "引用文件 " + fid, HOST + f"/courses/{cid}/files/{fid}")
                    doc.update(fileId=fid, status="failed", issues=["课程引用的文件未能读取"], pageCount=0, readPages=0)
                    put(doc)
            ordered = sorted(files.items(), key=lambda pair: (not bool(re.search(r"outline|syllabus|schedule|intro|assessment|大纲|提纲|日程|评分", pair[1].get("display_name") or pair[1].get("filename") or "", re.I)), pair[0]))
            summary["fileCount"] = len(files)
            for number, (fid, meta) in enumerate(ordered, 1):
                cancelled()
                title = meta.get("display_name") or meta.get("filename") or "文件 " + fid
                progress(f"{code}：读取文件 {number} / {len(files)} · {title}")
                doc = document("file", fid, title, meta.get("sourceUrl") or HOST + f"/courses/{cid}/files/{fid}", meta.get("updated_at") or meta.get("modified_at"))
                doc.update(fileId=fid, size=meta.get("size"), folders=meta.get("folders") or [],
                           fingerprint=fingerprint([fid, title, meta.get("updated_at"), meta.get("modified_at"), meta.get("size"), meta.get("sha256")]))
                suffix = file_suffix(meta)
                source_cache = cache / (cid + "-" + fid + ".json")
                blob = (root / "files" / meta["archivePath"]) if archive is not None and meta.get("archivePath") else cache / (cid + "-" + fid + suffix)
                prior = previous.get(doc["id"]) or {}
                try:
                    if meta.get("locked_for_user"):
                        doc.update(status="locked", issues=["文件尚未向本账号开放"], pageCount=0, readPages=0)
                    elif archive is not None and meta.get("archiveStatus") != "downloaded":
                        doc.update(status="failed", issues=meta.get("archiveIssues") or ["文件不在本轮已下载的附件中"], pageCount=0, readPages=0)
                    elif suffix not in SUPPORTED:
                        doc.update(status="unsupported", issues=["文件已下载；此格式暂未支持自动读取"], pageCount=0, readPages=0)
                    else:
                        cached = read_json(source_cache, {})
                        unchanged = cached.get("fingerprint") == doc["fingerprint"] and blob.exists() and blob.stat().st_size > 0
                        cached_pages = cached.get("pages") or []
                        fully_read = bool(cached_pages) and all(not p.get("textOnly") and not p.get("issues") and (p.get("processed") or p.get("readable")) for p in cached_pages)
                        if unchanged and fully_read and cached.get("readerVersion") == READER_VERSION:
                            pages = cached.get("pages") or []
                        else:
                            # Use the stable Canvas download URL. Redirects to an
                            # authorized signed CDN are handled by the transport.
                            if not unchanged:
                                if archive is not None:
                                    if not blob.is_file(): raise RuntimeError("下载清单记录的文件不存在")
                                else:
                                    download_document(api, meta, cid, fid, blob, cancelled)
                            pages = extract(blob, reader, cache, text_only=True)
                            save_private(source_cache, {"fingerprint": doc["fingerprint"], "readerVersion": READER_VERSION, "pages": pages})
                            if any(p.get("textOnly") for p in pages): image_queue.append((doc, cfg, blob, source_cache))
                        for page in pages:
                            links = (page.get("links") or []) + re.findall(r"https://canvas\.polyu\.edu\.hk/[^\s<>\"']+", (page.get("text") or "") + "\n" + "\n".join(page.get("ocrRows") or []))
                            references(links, doc["url"])
                        read_pages()
                        for linked_id in sorted(file_refs - checked_refs):
                            if not linked_id.isdigit(): continue
                            checked_refs.add(linked_id)
                            linked = get(f"/api/v1/files/{linked_id}", issues, "文件内引用 " + linked_id, {})
                            if linked:
                                files[linked_id] = linked
                                ordered.append((linked_id, linked))
                            else:
                                missing = document("file", linked_id, "文件内引用 " + linked_id, HOST + f"/courses/{cid}/files/{linked_id}")
                                missing.update(fileId=linked_id, status="failed", issues=["文件内引用的资料未能读取"], pageCount=0, readPages=0)
                                put(missing)
                        summary["fileCount"] = len(files)
                        commit(doc, pages, cfg)
                        if doc.get("findings"): apply_checkpoint()
                        continue
                except CanvasAuthError: raise
                except Exception as error:
                    reason = str(error) if isinstance(error, RuntimeError) else f"Canvas 返回 HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else "文件下载或正文读取未完成；应用将在下次同步重试"
                    doc.update(status="failed", issues=[reason[:200]], pageCount=0, readPages=0)
                    if prior.get("fingerprint") == doc.get("fingerprint") and prior.get("facts"):
                        doc.update(facts=prior["facts"], findings=prior.get("findings") or [])
                        doc["issues"].append("文件版本未改变，暂保留上次识别的考核信息")
                put(doc); publish()
            if not issues:
                # Delete old entries only after every enumeration for this
                # course succeeded. A transient failure cannot erase its exams.
                index["records"] = [r for r in index["records"] if r["courseId"] != cid or r["id"] in live_ids]
            summary["complete"] = not issues and all(r.get("status") == "read" for r in index["records"] if r["courseId"] == cid)
            publish()
        apply_checkpoint()
        # Publish text/table results from every course first. Then read images
        # on every PDF page, rather than delaying all exams behind lecture OCR.
        for number, (doc, cfg, blob, source_cache) in enumerate(image_queue, 1):
            cancelled()
            progress(f"图片识别 {number} / {len(image_queue)} · {doc['course']} · {doc['title']}")
            try:
                pages = extract(blob, reader, cache)
                save_private(source_cache, {"fingerprint": doc["fingerprint"], "readerVersion": READER_VERSION, "pages": pages})
                describe(doc, pages, cfg)
                cid = doc['courseId']
                known = index["discoveredReferences"].setdefault(cid, {"files": [], "pages": []})
                for page in pages:
                    links = (page.get("links") or []) + re.findall(r"https://canvas\.polyu\.edu\.hk/[^\s<>\"']+", (page.get("text") or "") + "\n" + "\n".join(page.get("ocrRows") or []))
                    for link in links:
                        target = urlsplit(urljoin(doc["url"], link))
                        if target.hostname != "canvas.polyu.edu.hk": continue
                        path = unquote(target.path)
                        for kind, pattern, ids_key in (("file", r"/(?:api/v1/)?(?:courses/\d+/)?files/(\d+)", "files"), ("page", rf"/courses/{cid}/pages/([^/]+)", "pages")):
                            match = re.search(pattern, path)
                            if match and match[1] not in known[ids_key]:
                                known[ids_key].append(match[1])
                                if f"{cid}:{kind}:{match[1]}" not in live_ids:
                                    discovered_more = True
            except Exception:
                doc["status"] = "partial"
                doc["issues"] = ["文字已识别；图片识别未完成，应用将在下次同步重试"]
            publish()
            if doc.get("findings"): apply_checkpoint()
        for summary in index["courses"]:
            summary["complete"] = not summary["issues"] and all(r.get("status") == "read" for r in index["records"] if r["courseId"] == summary["id"])
            if discovered_more:
                summary["complete"] = False
                summary["issues"].append("图片中发现更多 Canvas 资料引用，正在继续读取" if followups < 3 else "资料引用链尚有未读内容，后续同步继续读取")
        cancelled()
        index["evidence"] = consolidate(index["records"])
        index["completedAt"] = now()
        # Remove cache files no longer listed for the currently enrolled courses.
        retained = {r["courseId"] + "-" + r["fileId"] for r in index["records"] if r.get("fileId")}
        for item in cache.iterdir():
            if all(c.get("complete") for c in index["courses"]) and item.is_file() and re.fullmatch(r"\d+-\d+\..+", item.name) and item.stem not in retained:
                item.unlink()
    finally:
        index["scanning"] = False
        publish()
    if discovered_more and followups < 3:
        progress("正在继续读取图片中新发现的 Canvas 资料…")
        return scan_courses(api, profile, config, reader, cancelled, progress, checkpoint, followups+1, archive)
    return index


def scan_archived_web(api, profile, config, reader, cancelled, progress, checkpoint, archive):
    """Interpret downloaded files and rendered course pages without JSON APIs."""
    from canvas_web import html_page, identity, readable_main
    from sync_support import ProgressWriter
    root = Path(profile) / "documents"
    cache, files_root = root / "cache", root / "files"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous_index = read_json(root / "index.json", {})
    account_id = str((config.get('user') or {}).get('id'))
    if str(archive.get('accountId')) != account_id:
        raise RuntimeError('下载清单与当前账号不一致，请重新同步。')
    if previous_index.get('accountId') != account_id:
        previous_index = {}
    previous = {r.get("id"): r for r in previous_index.get("records") or []}
    active = {str(c.get("id")): c for c in config.get("courses") or [] if not c.get("inactive")}
    summaries = [{"id": cid, "code": c.get("code") or cid, "name": c.get("name") or "",
                  "fileCount": 0, "complete": False, "issues": list(next((row.get("issues") or [] for row in archive.get("courses") or [] if row.get("id") == cid), []))}
                 for cid, c in active.items()]
    index = {"accountId": str((config.get("user") or {}).get("id")), "startedAt": now(), "completedAt": None, "scanning": True, "sourceMode": "canvas_web",
             "version": VERSION, "records": [{**r, "status": "cached", "issues": ["沿用上次识别结果，尚未完成本轮复核"]}
                                    for r in previous.values() if r.get("courseId") in active],
             "courses": summaries, "discoveredReferences": previous_index.get("discoveredReferences") or {},
             "evidence": {"deadlines": [], "pending": [], "grading": {}}}
    live = set()
    image_queue = []
    writer = ProgressWriter(root / 'index.json', cancelled)
    context_key = fingerprint([VERSION, READER_VERSION, config])

    def publish(force=False):
        return writer.publish(index, force=force, prepare=lambda: index.update(evidence=consolidate(index['records'])))

    def apply_checkpoint():
        if not checkpoint: return
        try:
            checkpoint(index)
            if index.pop('integrationIssue', None): publish(force=True)
        except Exception as error:
            cancelled()
            index['integrationIssue'] = '识别结果写入未完成：' + str(error)[-350:]
            publish(force=True)

    def interpret(doc, pages, analyzing=False):
        key = fingerprint([context_key, doc['fingerprint'], doc['title'], doc['url'], doc.get('folders'), doc['course']])
        prior = previous.get(doc['id']) or {}
        complete_pages = bool(pages) and all(not p.get('textOnly') and not p.get('issues') and (p.get('processed') or p.get('readable')) for p in pages)
        reusable = complete_pages and not analyzing and doc.get('fingerprint') and prior.get('interpretationKey') == key and prior.get('status') == 'read'
        if reusable:
            for field in ('pageCount', 'readPages', 'processedPages', 'issues', 'status', 'methods', 'facts', 'findings'):
                if field in prior: doc[field] = prior[field]
            doc.update(readAt=now(), reusedInterpretation=True)
        else:
            describe(doc, pages, config, analyzing=analyzing)
        doc['interpretationKey'] = key

    def put(doc):
        index["records"] = [r for r in index["records"] if r.get("id") != doc["id"]]
        index["records"].append(doc)
        live.add(doc["id"])
        flushed = publish(force=doc['status'] in {'failed', 'partial', 'locked', 'unsupported'})
        if flushed and doc.get('findings') and not doc.get('reusedInterpretation'):
            apply_checkpoint()

    try:
        publish(force=True)
        entries = archive.get("entries") or []
        for number, entry in enumerate(entries, 1):
            cancelled()
            cid, fid = str(entry.get("courseId")), str(entry.get("id"))
            if cid not in active or not fid.isdigit(): continue
            course = active[cid]
            summary = next(row for row in summaries if row["id"] == cid)
            summary["fileCount"] += 1
            title = entry.get("title") or "文件 " + fid
            progress(f"识别正文与表格 {number}/{len(entries)} · {course.get('code') or cid} · {title}")
            doc = {"id": f"{cid}:file:{fid}", "course": course.get("code") or cid, "courseId": cid,
                   "fileId": fid, "type": "file", "title": title, "url": entry.get("url") or HOST + f"/courses/{cid}/files/{fid}",
                   "folders": entry.get("folders") or [], "size": entry.get("size"), "issues": [],
                   "readerVersion": READER_VERSION, "fingerprint": entry.get("sha256")}
            if entry.get("status") != "downloaded":
                doc.update(status="failed", issues=entry.get("issues") or ["文件下载未完成"], pageCount=0, readPages=0)
                put(doc); continue
            relative = (entry.get("paths") or [None])[0]
            from canvas_archive import safe_file
            try:
                blob = safe_file(files_root, relative) if relative else files_root / 'missing'
            except ValueError:
                doc.update(status='failed', issues=['下载清单中的文件路径无效'], pageCount=0, readPages=0)
                put(doc); continue
            if not blob.is_file() or blob.is_symlink() or not blob.resolve().is_relative_to(files_root.resolve()):
                doc.update(status="failed", issues=["下载清单中的文件不存在或路径无效"], pageCount=0, readPages=0)
                put(doc); continue
            if blob.suffix.lower() not in SUPPORTED:
                doc.update(status="unsupported", issues=["文件已下载；此格式暂未支持自动读取"], pageCount=0, readPages=0)
                put(doc); continue
            source_cache = cache / (cid + "-" + fid + ".json")
            stored = read_json(source_cache, {})
            reusable = bool(doc['fingerprint']) and stored.get("fingerprint") == doc["fingerprint"] and stored.get("readerVersion") == READER_VERSION and (stored.get('accountId') == account_id or (stored.get('accountId') is None and previous_index.get('accountId') == account_id))
            pages = stored.get("pages") or [] if reusable else []
            try:
                if not pages or any(p.get("textOnly") or p.get("issues") for p in pages):
                    pages = extract(blob, reader, cache, text_only=True)
                    save_private(source_cache, {'accountId': account_id, "fingerprint": doc["fingerprint"], "readerVersion": READER_VERSION, "pages": pages})
                interpret(doc, pages, analyzing=any(p.get("textOnly") for p in pages))
                if any(p.get("textOnly") for p in pages):
                    image_queue.append((doc, blob, source_cache, course))
            except Exception as error:
                doc.update(status="failed", issues=["文件正文读取失败：" + str(error)[:180]], pageCount=0, readPages=0)
                prior = previous.get(doc["id"]) or {}
                if prior.get("fingerprint") == doc.get("fingerprint") and prior.get("facts"):
                    doc.update(facts=prior["facts"], findings=prior.get("findings") or [])
                    doc["issues"].append("文件未变化，暂保留上次识别结果")
            put(doc)
        # Read the visible syllabus, course pages, tasks and announcements only
        # after every attachment download has reached a final status.
        for summary in summaries:
            cancelled()
            cid = summary["id"]
            course = active[cid]
            archived_course = next((row for row in archive.get("courses") or [] if row.get("id") == cid), {})
            for path in archived_course.get("webPaths") or []:
                cancelled()
                progress(f"读取课程网页 · {summary['code']} · {path.split('/')[-1] or '主页'}")
                kind = "syllabus" if path.endswith("/syllabus") else "announcement" if "/announcements" in path or "/discussion_topics" in path else "assignment" if "/assignments" in path else "quiz" if "/quizzes" in path else "page"
                doc = {"id": f"{cid}:{kind}:{hashlib.sha256(path.encode()).hexdigest()[:16]}", "course": summary["code"],
                       "courseId": cid, "type": kind, "title": path.split("/")[-1] or summary["name"],
                       "url": HOST + path, "issues": [], "readerVersion": READER_VERSION}
                try:
                    cached = read_json(root / "web" / cid / (hashlib.sha256(path.encode()).hexdigest() + ".json"), {})
                    if cached.get("accountId") == str((config.get("user") or {}).get("id")) and cached.get("path") == path:
                        page_data = cached
                    else:
                        source = html_page(api, path)
                        if str(identity(source)["id"]) != str((config.get("user") or {}).get("id")):
                            raise RuntimeError("Canvas 当前账号与本机档案不一致")
                        page_data = readable_main(source)
                    if page_data.get("title"): doc["title"] = page_data["title"]
                    rows = page_data.get("rows") or []
                    if not rows: continue
                    pages = [{"page": n // 100 + 1, "locator": f"网页正文行 {n+1}–{min(n+100,len(rows))}",
                              "text": "\n".join(rows[n:n+100]), "rows": rows[n:n+100], "readable": True,
                              "processed": True, "method": "Canvas 网页正文与表格"} for n in range(0, len(rows), 100)]
                    doc['fingerprint'] = fingerprint(rows)
                    interpret(doc, pages)
                except Exception as error:
                    doc.update(status="failed", issues=["课程网页识别未完成：" + str(error)[:160]], pageCount=0, readPages=0)
                    summary["issues"].append(path + "：网页识别未完成")
                put(doc)
        publish(force=True)
        apply_checkpoint()
        for number, (doc, blob, source_cache, course) in enumerate(image_queue, 1):
            cancelled()
            progress(f"识别图片 {number}/{len(image_queue)} · {doc['course']} · {doc['title']}")
            try:
                pages = extract(blob, reader, cache)
                save_private(source_cache, {'accountId': account_id, "fingerprint": doc["fingerprint"], "readerVersion": READER_VERSION, "pages": pages})
                interpret(doc, pages)
            except Exception as error:
                doc.update(status="partial", issues=["文字已读取；图片识别未完成：" + str(error)[:160]])
            put(doc)
        for summary in summaries:
            if not summary["issues"]:
                index["records"] = [r for r in index["records"] if r.get("courseId") != summary["id"] or r.get("id") in live]
            summary["complete"] = not summary["issues"] and all(r.get("status") == "read" for r in index["records"] if r.get("courseId") == summary["id"])
        index["completedAt"] = now()
    finally:
        index["scanning"] = False
        publish(force=True)
    apply_checkpoint()
    return index


def public_summary(profile):
    value = read_json(Path(profile) / "documents/index.json", {})
    downloads = read_json(Path(profile) / "documents/downloads.json", {})
    records = value.get("records") or []
    files = [r for r in records if r.get("type") == "file"]
    complete = bool(value.get("completedAt")) and all(c.get("complete") for c in value.get("courses") or [])
    evidence = value.get("evidence") or {}
    blocked = sum(r.get("status") in {"partial", "failed", "unsupported", "external", "cached"} for r in records) + sum(len(c.get("issues") or []) for c in value.get("courses") or []) + int(bool(value.get("integrationIssue")))
    conflicts = sum(bool(p.get("needsReview")) for p in evidence.get("pending") or []) + sum(bool(c.get("conflict")) for components in (evidence.get("grading") or {}).values() for c in components) + len(evidence.get("detailConflicts") or [])
    return {"startedAt": value.get("startedAt"), "completedAt": value.get("completedAt"), "sourceMode": value.get("sourceMode", "canvas"),
        "scanning": value.get("scanning", False), "complete": complete, "integrationIssue": value.get("integrationIssue"),
        "fileCount": max(len(files), sum(c.get("fileCount", 0) for c in value.get("courses") or [])), "readFiles": sum(r.get("status") == "read" for r in files),
        "pageCount": sum(r.get("pageCount", 0) for r in records),
        "readPages": sum(r.get("readPages", 0) for r in records), "processedPages": sum(r.get("processedPages", r.get("readPages", 0)) for r in records),
        "reviewCount": conflicts, "issueCount": blocked,
        "downloadComplete": downloads.get("complete", False), "downloadedAt": downloads.get("completedAt"),
        "downloadedFiles": sum(e.get("status") == "downloaded" for e in downloads.get("entries") or []),
        "revalidatedFiles": sum(e.get("status") == "downloaded" and e.get("transfer") == "revalidated" for e in downloads.get("entries") or []),
        "downloadFileCount": len(downloads.get("entries") or []),
        "downloadIssues": downloads.get("issues") or [],
        "downloadCourses": downloads.get("courses") or [],
        "downloadEntries": [{k: e.get(k) for k in ("course", "title", "folders", "paths", "status", "issues", "url", "size")}
                            for e in downloads.get("entries") or []],
        "findingCount": sum(len(r.get("findings") or []) for r in records),
        "courses": value.get("courses") or [],
        "records": [{k: r.get(k) for k in ("id", "course", "type", "title", "url", "updatedAt", "status", "issues", "pageCount", "readPages", "processedPages", "methods", "findings")} for r in records],
        "evidence": value.get("evidence") or {"deadlines": [], "pending": [], "grading": {}}}


def read_downloaded_materials(profile, config, reader, progress):
    """Interpret previously downloaded Canvas files when the login has expired.

    This uses only this profile's existing download manifest and material root.
    It never claims a fresh or exhaustive Canvas enumeration from a local cache.
    """
    profile = Path(profile)
    root, cache = profile / "documents", profile / "documents/cache"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    materials = Path(config.get("root") or "").resolve()
    courses = config.get("courses") or []
    manifest = read_json(profile / "raw/downloads.json", {}).get("done") or []
    index = {"startedAt": now(), "completedAt": None, "scanning": True, "version": VERSION,
        "sourceMode": "cached", "records": [], "courses": [], "evidence": {}}
    def publish():
        index["evidence"] = consolidate(index["records"])
        save_private(root / "index.json", index)
    queue = []
    try:
        for entry in manifest:
            path = Path(entry.get("saved") or "").resolve()
            if not path.is_file() or not path.is_relative_to(materials): continue
            parts = path.relative_to(materials).parts
            course = next((c for c in courses if parts and parts[0].startswith((c.get("code") or "") + " ")), None)
            fid = str(entry.get("id") or "")
            if not course or not fid.isdigit(): continue
            cid, code = str(course["id"]), course["code"]
            doc = {"id": f"{cid}:file:{fid}", "course": code, "courseId": cid, "fileId": fid, "type": "file",
                "title": entry.get("title") or path.name, "url": HOST + f"/courses/{cid}/files/{fid}", "issues": []}
            progress("识别正文 · " + code + " · " + doc["title"])
            try:
                pages = extract(path, reader, cache, text_only=True)
                describe(doc, pages, config, analyzing=any(p.get("textOnly") for p in pages))
                if doc["status"] == "analyzing": queue.append((doc, path))
            except Exception as error:
                doc.update(status="failed", pageCount=0, readPages=0, issues=[str(error)[:160]])
            index["records"].append(doc); publish()
        for n, (doc, path) in enumerate(queue, 1):
            progress(f"识别图片 {n}/{len(queue)} · {doc['course']} · {doc['title']}")
            try:
                pages = extract(path, reader, cache)
                save_private(cache / (doc['courseId'] + "-" + doc['fileId'] + ".json"), {"readerVersion": READER_VERSION, "pages": pages})
                describe(doc, pages, config)
            except Exception as error:
                doc.update(status="partial", issues=[str(error)[:160]])
            publish()
        for course in courses:
            files = [r for r in index["records"] if r["course"] == course["code"]]
            index["courses"].append({"id": str(course["id"]), "code": course["code"], "name": course.get("name"), "fileCount": len(files),
                "complete": False, "issues": ["已识别此前下载的 Canvas 文件；会话过期，尚未刷新文件目录"]})
        index["completedAt"] = now()
    finally:
        index["scanning"] = False; publish()
    return index


def reprocess_cached_documents(profile, config, reader, cancelled, progress):
    """Upgrade interpretation from this account's saved original pages/files."""
    profile = Path(profile)
    root, cache = profile / "documents", profile / "documents/cache"
    index = read_json(root / "index.json", {})
    if not index.get('records'): return None
    courses = {str(c['id']) for c in config.get('courses') or []}
    index.update(scanning=True, version=VERSION, sourceMode=index.get('sourceMode') or 'reprocessed')
    try:
        for record in index.get('records') or []:
            cancelled()
            if record.get('courseId') not in courses or not record.get('fileId'): continue
            prefix = record['courseId'] + '-' + record['fileId']
            saved = read_json(cache / (prefix + '.json'), {})
            pages = saved.get('pages') or []
            if saved.get('readerVersion') != READER_VERSION:
                original = next((p for p in cache.glob(prefix + '.*') if p.suffix.lower() in SUPPORTED and p.is_file() and not p.is_symlink()), None)
                if original is None:
                    downloads = read_json(root / 'downloads.json', {})
                    entry = next((e for e in downloads.get('entries') or [] if e.get('courseId') == record['courseId'] and e.get('id') == record['fileId'] and e.get('status') == 'downloaded'), None)
                    if entry and entry.get('paths'):
                        candidate = root / 'files' / entry['paths'][0]
                        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED and not candidate.is_symlink():
                            original = candidate
                if original:
                    progress('重读本账号已下载文件 · ' + record['title'])
                    try:
                        pages = extract(original, reader, cache)
                        save_private(cache / (prefix + '.json'), {**saved, 'readerVersion': READER_VERSION, 'pages': pages})
                    except Exception:
                        record.update(status='partial', issues=['此文件的识别升级未完成，继续扫描其他课程并在下载阶段重试'])
                        continue
            if pages:
                describe(record, pages, config)
                index['evidence'] = consolidate(index['records'])
                save_private(root / 'index.json', index)
        index['evidence'] = consolidate(index['records'])
    finally:
        index['scanning'] = False
        save_private(root / 'index.json', index)
    return index
