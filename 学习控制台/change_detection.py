"""Observed content changes only: no first-sync alert or inferred deletions."""
import re
from document_text import html_text


def changes(previous, assignments, old_announcements, announcements):
    if not previous.get('collected_at'): return []
    before = previous.get('assignments') or {}
    result = []
    labels = {'name':'标题','due_at':'截止日期','points':'满分','sub_state':'提交 / 评分状态','score':'已公布分数'}
    for aid,row in assignments.items():
        old = before.get(aid)
        if old is None:
            if previous.get('complete'):
                result.append({'kind':'assignment','course':row.get('course'),'title':row.get('name'),
                               'url':row.get('html_url'),'detail':'新增 Canvas 作业'})
            continue
        changed = [label for key,label in labels.items() if old.get(key) != row.get(key)]
        if changed:
            result.append({'kind':'assignment','course':row.get('course'),'title':row.get('name'),
                           'url':row.get('html_url'),'detail':'、'.join(changed)+'发生变化'})

    def content(row):
        text,_ = html_text(row.get('message') or '')
        return row.get('title'), re.sub(r'\s+',' ',text).strip()

    existing = {str(row.get('id')):row for row in old_announcements if row.get('id') is not None}
    for row in announcements:
        if row.get('id') is None: continue
        old = existing.get(str(row['id']))
        if (old and content(old) != content(row)) or (old is None and previous.get('complete')):
            result.append({'kind':'announcement','course':row.get('context_code'),'title':row.get('title'),
                           'url':row.get('html_url'),'detail':'公告正文更新' if old else '新增公告'})
    return result[:100]
