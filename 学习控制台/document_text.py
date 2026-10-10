"""Local text, table and image reading for Canvas course files."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from native_canvas import save_private
from platform_support import helper_command, IS_WINDOWS

READER_VERSION = 8
SUPPORTED = {".pdf", ".pptx", ".pptm", ".docx", ".docm", ".xlsx", ".xlsm", ".txt", ".csv", ".md", ".html", ".htm", ".rtf", ".doc", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".bmp", ".gif", ".zip"}
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".bmp", ".gif"}
MAX_ARCHIVE_EXPANSION = 500 * 1024 * 1024


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.ignore, self.links = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}: self.ignore += 1
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3", "br"}: self.parts.append("\n")
        if tag in {"td", "th"}: self.parts.append("\t")
        for key, value in attrs:
            if key in {"href", "src"} and value: self.links.append(value)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}: self.ignore = max(0, self.ignore - 1)
        if tag in {"p", "div", "tr", "li"}: self.parts.append("\n")

    def handle_data(self, value):
        if not self.ignore: self.parts.append(value)


def html_text(value):
    parser = PlainHTML()
    parser.feed(value or "")
    return "".join(parser.parts), parser.links


def native_read(path, reader, text_only=False):
    if reader == "builtin:windows":
        from windows_document import read
        return read(path, text_only=text_only)
    if not reader or not Path(reader).is_file():
        raise RuntimeError("缺少应用自带的 PDF/OCR 阅读工具")
    arguments = [str(reader)] + (["--text-only"] if text_only else []) + [str(path)]
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=7200)
    try: data = json.loads(result.stdout)
    except ValueError: raise RuntimeError("文件阅读未完成") from None
    if result.returncode or data.get("error"):
        raise RuntimeError(data.get("error") or "文件无法读取")
    return data.get("pages") or []


def pdf_tables(path, pages, cache):
    """Keep table cells beside native/OCR text, with page-level provenance."""
    from pdf_layout import ACADEMIC
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    stored = Path(cache) / "pdf-layout" / (digest.hexdigest() + f"-v{READER_VERSION}.json")
    selected = [p["page"] for p in pages if ACADEMIC.search(p.get("text") or "") or not (p.get("text") or "").strip()]
    if not selected:
        return pages
    try:
        result = json.loads(stored.read_text()) if stored.exists() else {}
        if not all(str(n) in result for n in selected):
            run = subprocess.run(helper_command("pdf_layout.py", path, json.dumps(selected)), capture_output=True, text=True, encoding="utf-8", timeout=600,
                                 creationflags=0x08000000 if IS_WINDOWS else 0)
            result = json.loads(run.stdout)
            if run.returncode or result.get("error"):
                raise RuntimeError(result.get("error") or "PDF 表格读取失败")
            if not any(p.get("issue") for p in result.values()):
                save_private(stored, result)
        for page in pages:
            extra = result.get(str(page["page"]), {})
            if extra.get("issue"):
                page.setdefault("issues", []).append(extra["issue"])
            if extra.get("tables"):
                page["tables"] = extra["tables"]
                page["method"] = page.get("method", "PDF") + " · pdfplumber 表格"
            # A second independent parser can recover PDFKit font/encoding misses.
            if not (page.get("text") or "").strip() and extra.get("text"):
                page.update(text=extra["text"], rows=extra["text"].splitlines(), readable=True)
    except Exception as error:
        for page in pages:
            if page["page"] in selected:
                page.setdefault("issues", []).append("PDF 表格结构解析未完成（" + type(error).__name__ + "）；已保留正文与 OCR")
    return pages


def image_ocr(data, suffix, reader, cache):
    digest = hashlib.sha256(data).hexdigest()
    stored = Path(cache) / "image-ocr" / (digest + f"-v{READER_VERSION}.json")
    if stored.exists(): return json.loads(stored.read_text())
    with tempfile.TemporaryDirectory(dir=cache) as temporary:
        image = Path(temporary) / ("image" + suffix)
        image.write_bytes(data)
        pages = native_read(image, reader)
    save_private(stored, pages)
    return pages


def safe_member(bundle, name):
    normalized = posixpath.normpath(name)
    if normalized.startswith("../") or normalized.startswith("/"):
        raise RuntimeError("压缩包路径无效")
    info = bundle.getinfo(normalized)
    if info.file_size > 250 * 1024 * 1024 or info.file_size > max(1, info.compress_size) * 1500:
        raise RuntimeError("压缩包内容过大，需人工核对")
    return bundle.read(info)


def office_read(path, reader, cache, text_only=False):
    suffix = Path(path).suffix.lower()
    pages = []
    with zipfile.ZipFile(path) as bundle:
        names = bundle.namelist()
        has_images = any(name.startswith(('ppt/media/', 'word/media/', 'xl/media/')) and not name.endswith('/') for name in names)
        links = []
        for name in names:
            if name.endswith(".rels"):
                links.extend(r.get("Target") for r in ET.fromstring(safe_member(bundle, name)) if r.get("TargetMode") == "External" and r.get("Target"))
        if suffix in {".pptx", ".pptm"}:
            slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))
            for index, name in enumerate(slides, 1):
                root = ET.fromstring(safe_member(bundle, name))
                rows = []
                for paragraph in root.findall(".//{*}sp/{*}txBody/{*}p"):
                    text = "".join(t.text or "" for t in paragraph.findall(".//{*}t"))
                    if text.strip(): rows.append(text.strip())
                tables = []
                for table in root.findall(".//{*}tbl"):
                    cells = [[" ".join(t.text or "" for t in cell.findall(".//{*}t")) for cell in row.findall("{*}tc")] for row in table.findall("{*}tr")]
                    tables.append({"cells": cells, "rows": ["\t".join(row) for row in cells], "context": rows[:3], "method": "Office 原始表格"})
                rel_name = "ppt/slides/_rels/" + Path(name).name + ".rels"
                relations = {}
                if rel_name in names:
                    relations = {r.get("Id"): r.get("Target") for r in ET.fromstring(safe_member(bundle, rel_name)) if r.get("TargetMode") != "External"}
                for target in relations.values():
                    member = posixpath.normpath(posixpath.join("ppt/slides", target))
                    if member.startswith("ppt/notesSlides/") and member.endswith(".xml") and member in names:
                        note = ET.fromstring(safe_member(bundle, member))
                        rows.extend("演讲者备注：" + "".join(t.text or "" for t in p.findall(".//{*}t")) for p in note.findall(".//{*}p"))
                image_rows, image_scores, image_issues = [], [], []
                for blip in root.findall(".//{*}blip"):
                    if text_only: continue
                    identifier = next((v for k, v in blip.attrib.items() if k.endswith("}embed")), None)
                    target = relations.get(identifier)
                    if not target: continue
                    member = posixpath.normpath(posixpath.join("ppt/slides", target))
                    ext = Path(member).suffix.lower()
                    if ext not in IMAGES:
                        image_issues.append("嵌入图片格式暂不支持：" + ext); continue
                    try:
                        recognized = image_ocr(safe_member(bundle, member), ext, reader, cache)
                        for item in recognized:
                            values = item.get("rows") or []
                            image_rows.extend(values)
                            image_scores.extend(item.get("rowConfidences") or [0.6 if item.get("lowConfidence") else 0.9]*len(values))
                    except Exception:
                        image_issues.append("幻灯片中的图片未能识别")
                pages.append({"page": index, "locator": f"幻灯片 {index}", "text": "\n".join(rows + [r for t in tables for r in t["rows"]]), "rows": rows, "tables": tables, "ocrRows": image_rows, "ocrRowConfidences": image_scores, "readable": bool(rows or image_rows or tables), "processed": True, "textOnly": text_only, "method": "PPTX 正文与表格" if text_only else "PPTX 正文、表格与图片 OCR", "lowConfidence": False, "issues": image_issues})
        elif suffix in {".docx", ".docm"}:
            root = ET.fromstring(safe_member(bundle, "word/document.xml"))
            body = root.find("{*}body")
            paragraph_rows, paragraph_start = [], 1
            def paragraph_page():
                if paragraph_rows:
                    pages.append({"page": len(pages)+1, "locator": f"段落 {paragraph_start}–{paragraph_start+len(paragraph_rows)-1}", "text": "\n".join(paragraph_rows), "rows": list(paragraph_rows), "readable": True, "method": "Word 连续正文"})
            for index, node in enumerate(body if body is not None else [], 1):
                rows = []
                if node.tag.endswith("}tbl"):
                    context = paragraph_rows[-3:]
                    paragraph_page(); paragraph_rows = []; paragraph_start = index+1
                    for row in node.findall("{*}tr"):
                        rows.append("\t".join(" ".join(t.text or "" for t in c.findall(".//{*}t")) for c in row.findall("{*}tc")))
                    pages.append({"page": len(pages)+1, "locator": f"表格 / 段落 {index}", "text": "\n".join(rows), "rows": [], "tables": [{"rows": rows, "context": context, "method": "Word 原始表格"}], "readable": bool(rows), "method": "Word 表格"})
                else:
                    text = "".join(t.text or "" for t in node.findall(".//{*}t")).strip()
                    if text: paragraph_rows.append(text)
                    if len(paragraph_rows) >= 100:
                        paragraph_page(); paragraph_rows = []; paragraph_start = index+1
            paragraph_page()
            for name in names:
                if re.fullmatch(r"word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml", name):
                    extra = ET.fromstring(safe_member(bundle, name))
                    rows = ["".join(t.text or "" for t in p.findall(".//{*}t")) for p in extra.findall(".//{*}p")]
                    if any(rows): pages.append({"page": len(pages)+1, "locator": "Word 附注 " + Path(name).stem, "text": "\n".join(rows), "rows": rows, "readable": True, "method": "Word 页眉、页脚与附注"})
                if name.startswith("word/media/") and Path(name).suffix.lower() in IMAGES:
                    if text_only: continue
                    try:
                        for item in image_ocr(safe_member(bundle, name), Path(name).suffix.lower(), reader, cache):
                            pages.append({**item, "page": len(pages) + 1, "locator": "Word 嵌入图片 " + Path(name).name})
                    except Exception:
                        pages.append({"page": len(pages) + 1, "locator": "Word 嵌入图片 " + Path(name).name, "text": "", "rows": [], "readable": False, "issues": ["嵌入图片未能识别"]})
        else:
            shared = []
            if "xl/sharedStrings.xml" in names:
                shared = ["".join(t.text or "" for t in s.findall(".//{*}t")) for s in ET.fromstring(safe_member(bundle, "xl/sharedStrings.xml"))]
            percentage_styles = set()
            if "xl/styles.xml" in names:
                styles = ET.fromstring(safe_member(bundle, "xl/styles.xml"))
                formats = {e.get("numFmtId"): e.get("formatCode") or "" for e in styles.findall("{*}numFmts/{*}numFmt")}
                for i, style in enumerate(styles.findall("{*}cellXfs/{*}xf")):
                    fmt = style.get("numFmtId")
                    if fmt in {"9", "10"} or "%" in formats.get(fmt, ""): percentage_styles.add(str(i))
            for name in sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)):
                rows = []
                root = ET.fromstring(safe_member(bundle, name))
                for row in root.findall(".//{*}row"):
                    cells = []
                    for cell in row.findall("{*}c"):
                        value = cell.findtext("{*}v") or "".join(t.text or "" for t in cell.findall(".//{*}t"))
                        if cell.get("t") == "s":
                            value = shared[int(value)] if value.isdigit() and int(value) < len(shared) else ""
                        if cell.get("s") in percentage_styles:
                            try: value = f"{float(value)*100:g}%"
                            except ValueError: pass
                        cells.append(value)
                    rows.append("\t".join(cells))
                pages.append({"page": len(pages) + 1, "locator": "工作表 " + Path(name).stem, "text": "\n".join(rows), "rows": rows, "readable": bool(rows), "method": "Excel 单元格与百分比格式"})
        if any(n.startswith(("ppt/embeddings/", "word/embeddings/", "xl/embeddings/")) for n in names):
            pages.append({"page": len(pages)+1, "locator": "嵌入对象", "text": "", "rows": [], "readable": False, "issues": ["Office 嵌入对象需在 Canvas 中核对"]})
        if suffix in {".xlsx", ".xlsm"}:
            for name in names:
                if name.startswith("xl/media/") and Path(name).suffix.lower() in IMAGES:
                    if text_only: continue
                    try:
                        for item in image_ocr(safe_member(bundle, name), Path(name).suffix.lower(), reader, cache):
                            pages.append({**item, "page": len(pages)+1, "locator": "Excel 嵌入图片 " + Path(name).name})
                    except Exception:
                        pages.append({"page": len(pages)+1, "locator": "Excel 嵌入图片 " + Path(name).name, "text": "", "rows": [], "readable": False, "issues": ["嵌入图片未能识别"]})
    if pages: pages[0]["links"] = links
    if text_only:
        for page in pages: page["textOnly"] = has_images
    return pages


def extract(path, reader, cache, depth=0, text_only=False):
    path, cache = Path(path), Path(cache)
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    suffix = path.suffix.lower()
    if suffix == ".pdf" or suffix in IMAGES:
        pages = native_read(path, reader, text_only=text_only and suffix == ".pdf")
        for page in pages:
            page.setdefault("locator", f"第 {page['page']} 页" if suffix == ".pdf" else f"图片 {page['page']}")
        return pdf_tables(path, pages, cache) if suffix == ".pdf" else pages
    if suffix in {".pptx", ".pptm", ".docx", ".docm", ".xlsx", ".xlsm"}: return office_read(path, reader, cache, text_only=text_only)
    if suffix == ".zip":
        if depth >= 3: raise RuntimeError("压缩包嵌套超过三层，需人工核对")
        pages = []
        with zipfile.ZipFile(path) as bundle, tempfile.TemporaryDirectory(dir=cache) as temporary:
            expanded = 0
            for index, entry in enumerate(bundle.infolist()):
                if entry.is_dir() or entry.filename.startswith("__MACOSX/"): continue
                expanded += entry.file_size
                if expanded > MAX_ARCHIVE_EXPANSION:
                    pages.append({"page": len(pages)+1, "locator": entry.filename, "text": "", "rows": [], "readable": False, "issues": ["压缩包展开总量超过 500 MB，此文件需人工核对"]}); continue
                member = Path(temporary) / (str(index) + Path(entry.filename).suffix.lower())
                if member.suffix not in SUPPORTED:
                    pages.append({"page": len(pages)+1, "locator": entry.filename, "text": "", "rows": [], "readable": False, "issues": ["压缩包中此文件格式需人工核对"]}); continue
                member.write_bytes(safe_member(bundle, entry.filename))
                try:
                    nested = extract(member, reader, cache, depth+1, text_only=text_only)
                    for item in nested: pages.append({**item, "locator": entry.filename + " · " + item.get("locator", "")})
                except Exception:
                    pages.append({"page": len(pages)+1, "locator": entry.filename, "text": "", "rows": [], "readable": False, "issues": ["压缩包内文件未能读取"]})
        return pages
    if suffix in {".doc", ".rtf"}:
        if IS_WINDOWS:
            if suffix == ".doc":
                raise RuntimeError("旧版 DOC 暂未支持本机读取，请提供 DOCX 或 PDF；原文件已保留")
            from striprtf.striprtf import rtf_to_text
            text = rtf_to_text(path.read_text(encoding="latin-1"))
            return [{"page": 1, "text": text, "rows": text.splitlines(), "method": "RTF 正文", "readable": bool(text.strip()), "processed": True}]
        result = subprocess.run(["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)], capture_output=True, timeout=120)
        if result.returncode: raise RuntimeError("此 Office 文件未能转换为可读文字")
        text = result.stdout.decode("utf-8", errors="replace")
    else:
        links = []
        data = path.read_bytes()
        try: text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try: text = data.decode("utf-16")
            except UnicodeDecodeError: text = data.decode("gb18030", errors="replace")
        if suffix in {".html", ".htm"}: text, links = html_text(text)
    lines = text.splitlines()
    pages = [{"page": i//100 + 1, "locator": f"行 {i+1}–{min(i+100,len(lines))}", "text": "\n".join(lines[i:i+100]), "rows": lines[i:i+100], "readable": True, "method": "文件正文"} for i in range(0, len(lines), 100)]
    if not pages:
        pages = [{'page': 1, 'locator': '文件正文', 'text': '', 'rows': [], 'readable': False, 'processed': True, 'method': '文件正文'}]
    if pages and suffix in {".html", ".htm"}: pages[0]["links"] = links
    return pages
