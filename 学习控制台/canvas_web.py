"""Read the authenticated Canvas pages that PolyU presents to a student.

The Files navigation is hidden in some courses. Published module attachments
remain visible in the server-rendered Modules page and include their file IDs.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from native_canvas import HOST


class Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []
        self.parts = []

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def text(self):
        return " ".join(" ".join(self.parts + [child.text() for child in self.children]).split())

    def first(self, tag=None, css_class=None):
        return next((n for n in self.walk() if n is not self and
                     (tag is None or n.tag == tag) and
                     (css_class is None or css_class in n.classes())), None)


class Tree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.node = Node()

    def handle_starttag(self, tag, attrs):
        item = Node(tag, attrs, self.node)
        self.node.children.append(item)
        if tag not in self.VOID:
            self.node = item

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        cursor = self.node
        while cursor.parent and cursor.tag != tag:
            cursor = cursor.parent
        if cursor.parent:
            self.node = cursor.parent

    def handle_data(self, data):
        self.node.parts.append(data)


def tree(html):
    result = Tree()
    result.feed(html)
    return result.root


def identity(html):
    match = re.search(r'["\']current_user_id["\']\s*:\s*["\']?(\d+)', html)
    if not match:
        raise RuntimeError("Canvas 网页未显示已登录账号；请重新登录。")
    return {"id": match.group(1), "name": "PolyU 学生"}


def dashboard_courses(html):
    root = tree(html)
    rows = []
    for a in root.walk():
        if a.tag != "a" or "ic-DashboardCard__link" not in a.classes():
            continue
        match = re.fullmatch(r"/courses/(\d+)", urlsplit(a.attrs.get("href") or "").path)
        if not match:
            continue
        title = a.first("h2", "ic-DashboardCard__header-title")
        subtitle = a.first(css_class="ic-DashboardCard__header-subtitle")
        term = a.first(css_class="ic-DashboardCard__header-term")
        if not title or not subtitle:
            continue
        code = subtitle.attrs.get("title") or subtitle.text()
        name = title.attrs.get("title") or title.text()
        rows.append({"id": match.group(1), "code": code.strip(), "name": name.strip(),
                     "term": (term.attrs.get("title") or term.text()).strip() if term else "",
                     "start": None, "end": None})
    if not rows:
        raise RuntimeError("Canvas 控制面板未显示课程卡，无法确认课程范围。")
    return list({row["id"]: row for row in rows}.values())


def all_courses(html, dashboard_html=""):
    """Use the All Courses table so non-favorite courses are not skipped."""
    codes = {}
    if dashboard_html:
        try:
            codes = {row["id"]: row["code"] for row in dashboard_courses(dashboard_html)}
        except RuntimeError:
            pass
    root = tree(html)
    table = next((n for n in root.walk() if n.tag == "table" and n.attrs.get("id") == "my_courses_table"), None)
    if table is None:
        raise RuntimeError("Canvas 全部课程表格未显示，无法确认当前课程范围。")
    rows = []
    for row in table.walk():
        if row.tag != "tr" or "course-list-table-row" not in row.classes():
            continue
        heading = row.first("th", "course-list-course-title-column")
        link = heading.first("a") if heading else None
        if not link: continue
        match = re.fullmatch(r"/courses/(\d+)", urlsplit(link.attrs.get("href") or "").path)
        if not match: continue
        cid = match.group(1)
        name = (link.attrs.get("title") or link.text()).strip()
        term_node = row.first("td", "course-list-term-column")
        term = term_node.text().strip() if term_node else ""
        rows.append({"id": cid, "code": codes.get(cid) or cid, "name": name or cid,
                     "term": term, "start": None, "end": None})
    if not rows:
        raise RuntimeError("Canvas 全部课程表格为空，无法确认当前课程范围。")
    return rows


def course_code_from_home(html, course_id):
    root = tree(html)
    crumb = next((n for n in root.walk() if n.tag == "li" and n.attrs.get("id") == f"crumb_course_{course_id}"), None)
    link = crumb.first("a") if crumb else None
    return link.text().strip() if link else ""


def module_files(html, course_id):
    root = tree(html)
    result = []
    for module in root.walk():
        if "context_module" not in module.classes():
            continue
        label = module.first(css_class="ig-header-title")
        name = (module.attrs.get("aria-label") or (label.attrs.get("title") if label else "") or
                (label.first(css_class="name").text() if label and label.first(css_class="name") else "") or "未命名单元").strip()
        for item in module.walk():
            if item is module or item.tag != "li" or "context_module_item" not in item.classes():
                continue
            marker = next((re.fullmatch(r"Attachment_(\d+)", c) for c in item.classes() if c.startswith("Attachment_")), None)
            if not marker:
                continue
            link = item.first("a", "item_link")
            if not link:
                continue
            href = urlsplit(link.attrs.get("href") or "").path
            match = re.fullmatch(rf"/courses/{re.escape(str(course_id))}/modules/items/(\d+)", href)
            if not match:
                continue
            title = (link.attrs.get("title") or link.text()).strip()
            if title:
                result.append({"id": marker.group(1), "moduleItemId": match.group(1),
                               "title": title, "folder": name,
                               "url": HOST + href,
                               "downloadUrl": HOST + f"/courses/{course_id}/files/{marker.group(1)}/download?download_frd=1"})
    return result


def linked_files_and_pages(html, course_id):
    """Find first-party files and content pages exposed in rendered course HTML."""
    cid = re.escape(str(course_id))
    files, pages = [], []
    for node in tree(html).walk():
        for attribute in ("href", "src", "data-api-endpoint"):
            href = node.attrs.get(attribute) or ""
            parsed = urlsplit(href)
            if parsed.hostname and parsed.hostname != "canvas.polyu.edu.hk":
                continue
            file_match = re.match(rf"^/courses/{cid}/files/(\d+)(?:/|$)", parsed.path)
            if file_match:
                title = (node.attrs.get("title") or node.attrs.get("alt") or node.attrs.get("aria-label") or
                         (node.text() if node.tag == "a" else "") or "").strip()
                title = re.sub(r"^(?:Download|下载)\s+", "", title, flags=re.I)
                files.append({"id": file_match.group(1), "title": title or "文件 " + file_match.group(1),
                              "url": HOST + f"/courses/{course_id}/files/{file_match.group(1)}",
                              "downloadUrl": HOST + f"/courses/{course_id}/files/{file_match.group(1)}/download?download_frd=1"})
                continue
            if node.tag != "a" or attribute != "href": continue
            if re.match(rf"^/courses/{cid}/(?:modules/items/\d+|announcements/\d+|discussion_topics/\d+|assignments/\d+|quizzes/\d+|pages/[^/]+)$", parsed.path):
                pages.append(parsed.path)
            elif parsed.path in {f"/courses/{course_id}/{part}" for part in ("announcements", "assignments", "quizzes", "pages", "discussion_topics")} and parsed.query:
                if re.search(r"(?:^|&)page=\d+", parsed.query):
                    pages.append(parsed.path + "?" + parsed.query)
    return files, list(dict.fromkeys(pages))


def readable_main(html):
    """Keep visible course-page text without caching login scripts or tokens."""
    from document_text import html_text

    main = re.search(r"<main\b[^>]*>(.*?)</main\s*>", html, re.I | re.S)
    if not main:
        return {"title": "", "rows": []}
    readable, _ = html_text(main.group(1))
    heading = tree(main.group(0)).first("h1")
    return {"title": heading.text().strip() if heading else "",
            "rows": [line.strip() for line in readable.splitlines() if line.strip()]}


def html_page(api, path):
    headers, body = api.fetch(HOST + path, accept="text/html")
    if "html" not in (headers.get("Content-Type") or "").lower():
        raise RuntimeError("Canvas 网页返回非 HTML 内容。")
    html = body.decode("utf-8", "replace")
    identity(html)
    return html


def authenticated_user(api):
    return identity(html_page(api, "/"))
