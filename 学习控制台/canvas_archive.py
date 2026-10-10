"""Download every published attachment exposed by each course's Modules page."""
from __future__ import annotations

import os
import re
import shutil
import hashlib
import json
import datetime as dt
from collections import deque
from pathlib import Path

from canvas_web import html_page, identity, module_files, linked_files_and_pages, readable_main
from native_canvas import save_private
from platform_support import is_link
from sync_support import ProgressWriter, bounded_results


def safe_component(value, fallback):
    value = re.sub(r'[\\/\x00-\x1f\x7f:*?"<>|]', "_", str(value or "")).strip(" .")
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", value.split(".")[0], re.I):
        value = "_" + value
    if len(value.encode("utf-16-le")) > 320:
        suffix = Path(value).suffix
        suffix = suffix if len(suffix.encode("utf-16-le")) <= 30 else ""
        value = value.encode("utf-16-le")[:320-len(suffix.encode("utf-16-le"))].decode("utf-16-le", "ignore").rstrip(" .") + suffix
    if len(value.encode("utf-8")) > 230:
        suffix = Path(value).suffix
        suffix = suffix if len(suffix.encode("utf-8")) <= 30 else ""
        value = value.encode("utf-8")[:230-len(suffix.encode("utf-8"))].decode("utf-8", "ignore").rstrip(" .") + suffix
    return value or fallback


def safe_file(root, relative):
    root = Path(root)
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts: raise ValueError("文件归档路径无效")
    destination = root / relative
    components = [root, *[root.joinpath(*relative.parts[:n]) for n in range(1, len(relative.parts)+1)]]
    if any(is_link(p) for p in components) or not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError("文件归档路径含链接或越界")
    return destination


def file_digest(path, cancelled):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            cancelled()
            digest.update(block)
    return digest.hexdigest()


def download_record(api, files_root, pending, prior, old_complete, cancelled):
    """Workers return a result; only the coordinator mutates the manifest."""
    record = dict(pending)
    paths = record['paths']
    try:
        cancelled()
        destination = safe_file(files_root, paths[0])
        validated_digest = None
        if prior.get('etag') and prior.get('sha256'):
            for old_path in prior.get('paths') or []:
                try:
                    original = safe_file(files_root, old_path)
                    if original.is_file() and file_digest(original, cancelled) == prior['sha256']:
                        if original != destination:
                            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                            shutil.copy2(original, destination)
                        validated_digest = prior['sha256']
                        break
                except (OSError, ValueError): continue
        kwargs = {'cancelled': cancelled}
        if validated_digest: kwargs['validators'] = {'etag': prior['etag']}
        size = api.download_file(record['downloadUrl'], destination, **kwargs)
        cancelled()
        transfer = getattr(api, 'last_download', {})
        transfer = transfer if isinstance(transfer, dict) else {}
        # A fresh hash is computed in the streaming transport; a 304 uses only
        # the local file just verified against the same account's manifest.
        digest = transfer.get('sha256') or (validated_digest if transfer.get('transfer') == 'revalidated' else None) or file_digest(destination, cancelled)
        for relative in paths[1:]:
            cancelled()
            target = safe_file(files_root, relative)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.unlink(missing_ok=True)
            try: os.link(destination, target)
            except OSError: shutil.copy2(destination, target)
        record.update(status='downloaded', size=size, sha256=digest, etag=transfer.get('etag'),
                      transfer=transfer.get('transfer', 'downloaded'),
                      contentChanged=bool(prior.get('sha256') and prior['sha256'] != digest),
                      newlyObserved=old_complete and not prior)
    except Exception as error:
        record.update(status='failed', issues=[str(error)[:220]])
    return record


def archive_courses(api, profile, config, cancelled, progress):
    """Finish the download manifest before document interpretation begins.

    One Canvas file may appear in several modules. Keep a file at every visible
    location, while downloading its bytes only once per course.
    """
    root = Path(profile) / "documents"
    files_root = root / "files"
    web_root = root / "web"
    files_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    account_id = str((config.get("user") or {}).get("id") or "")
    try:
        previous = json.loads((root / "downloads.json").read_text(encoding="utf-8"))
    except (OSError, ValueError): previous = {}
    if not isinstance(previous, dict): previous = {}
    old_entries = {(str(e.get("courseId")), str(e.get("id"))): e for e in previous.get("entries") or []
                   if isinstance(e, dict) and previous.get("accountId") == account_id and e.get("status") == "downloaded"}
    manifest = {"accountId": account_id, "scope": "visible_course_attachments",
                "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                "completedAt": None, "complete": False, "courses": [], "entries": [], "issues": []}
    if any(course.get("rosterIncomplete") for course in config.get("courses") or []):
        manifest["issues"].append("Canvas 全部课程表格未能读取；本轮仅能覆盖控制面板显示的课程")

    writer = ProgressWriter(root / 'downloads.json', cancelled)
    def publish(force=False):
        manifest['progress'] = {'completed': sum(e['status'] in {'downloaded', 'failed'} for e in manifest['entries']),
                                'discovered': len(manifest['entries'])}
        writer.publish(manifest, force=force)

    publish(force=True)
    for course in config.get("courses") or []:
        if course.get("inactive"):
            continue
        cancelled()
        cid = str(course.get("id") or "")
        if not cid.isdigit():
            continue
        code = course.get("code") or cid
        summary = {"id": cid, "code": code, "name": course.get("name") or "", "discovered": 0,
                   "downloaded": 0, "complete": False, "issues": [], "webPaths": []}
        manifest["courses"].append(summary)
        progress(f"{code}：正在读取 Canvas 模块和附件清单…")
        try:
            page = html_page(api, f"/courses/{cid}/modules")
            if str(identity(page)["id"]) != account_id:
                raise RuntimeError("Canvas 当前账号与本机档案不一致。")
            items = module_files(page, cid)
            if "context_module" not in page and not items:
                raise RuntimeError("模块页未呈现课程单元，无法确认附件范围。")
        except Exception as error:
            summary["issues"].append("模块附件清单未能完整读取：" + str(error)[:180])
            publish(force=True)
            continue
        # The Files navigation can be hidden. Follow course content pages to
        # capture attachments linked from syllabus, announcements and tasks.
        known_module_files = {item["id"] for item in items}
        queue = deque([f"/courses/{cid}", f"/courses/{cid}/assignments/syllabus",
                 f"/courses/{cid}/announcements", f"/courses/{cid}/assignments",
                 f"/courses/{cid}/quizzes", f"/courses/{cid}/pages"])
        _, module_links = linked_files_and_pages(page, cid)
        queue.extend(module_links)
        queued = set(queue)
        visited = set()
        while queue and len(visited) < 1000:
            cancelled()
            path = queue.popleft()
            if path in visited: continue
            visited.add(path)
            progress(f"{code}：正在检查课程页面附件 {len(visited)}…")
            try:
                content = html_page(api, path)
                if str(identity(content)["id"]) != account_id:
                    raise RuntimeError("Canvas 当前账号与本机档案不一致。")
                linked, more = linked_files_and_pages(content, cid)
                for file in linked:
                    if file["id"] not in known_module_files:
                        file["folder"] = "课程页面与公告附件"
                        items.append(file)
                        known_module_files.add(file["id"])
                for link in more:
                    if link not in queued:
                        queue.append(link); queued.add(link)
                page_cache = web_root / cid / (hashlib.sha256(path.encode()).hexdigest() + ".json")
                save_private(page_cache, {"accountId": account_id, "path": path, **readable_main(content)})
                summary["webPaths"].append(path)
            except Exception as error:
                # A hidden course tab may redirect or deny access. Keep its
                # failure visible; module attachments remain downloadable.
                summary["issues"].append(path + "：网页附件检查未完成（" + str(error)[:100] + "）")
        if queue:
            summary["issues"].append("课程页面超过 1000 页，后续页面尚未遍历")
        groups = {}
        for item in items:
            groups.setdefault(item["id"], []).append(item)
        summary["discovered"] = len(groups)
        course_dir = safe_component(code, cid) + " [" + cid + "]"
        used = set()
        records = []
        for number, (fid, placements) in enumerate(groups.items(), 1):
            cancelled()
            title = placements[0]["title"]
            paths = []
            for placement in placements:
                folder = safe_component(placement["folder"], "未命名单元")
                filename = safe_component(placement["title"], "文件 " + fid)
                relative = Path(course_dir) / folder / filename
                collision = 0
                while str(relative).casefold() in used:
                    collision += 1
                    path = Path(filename)
                    relative = relative.with_name(path.stem + " [" + fid + (f'-{collision}' if collision > 1 else '') + "]" + path.suffix)
                used.add(str(relative).casefold())
                paths.append(relative)
            record = {"id": fid, "courseId": cid, "course": code, "title": title,
                      "url": placements[0]["url"], "downloadUrl": placements[0]["downloadUrl"],
                      "folders": list(dict.fromkeys(item["folder"] for item in placements)),
                      "paths": [str(p) for p in paths], "status": "pending", "issues": []}
            manifest["entries"].append(record)
            records.append(record)
        publish(force=True)
        old_complete = previous.get('accountId') == account_id and bool(previous.get('complete'))
        completed = 0
        def transfer(pending):
            prior = old_entries.get((cid, pending['id'])) or {}
            return pending, download_record(api, files_root, pending, prior, old_complete, cancelled)
        progress(f'下载 {code} · 已完成 0/{len(records)}')
        for pending, result in bounded_results(transfer, records, getattr(api, 'download_workers', 1), cancelled):
            pending.update(result)
            completed += 1
            if pending['status'] == 'downloaded': summary['downloaded'] += 1
            else: summary['issues'].append(f"{pending['title']}：下载未完成")
            progress(f"下载 {code} · 已完成 {completed}/{len(records)} · {pending['title']}")
            publish(force=pending['status'] == 'failed')
        summary["complete"] = not summary["issues"] and summary["downloaded"] == summary["discovered"]
        publish(force=True)
    manifest["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest["complete"] = not manifest["issues"] and all(row["complete"] for row in manifest["courses"]) and bool(manifest["courses"])
    publish(force=True)
    progress(("模块附件下载完成" if manifest["complete"] else "模块附件下载部分完成") +
             f"：{sum(c['downloaded'] for c in manifest['courses'])}/{sum(c['discovered'] for c in manifest['courses'])} 个文件。")
    return manifest
