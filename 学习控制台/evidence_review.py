"""Account-local human review overlays. Original Canvas and document evidence stays intact."""
import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from assessment_types import KINDS
from grades import finite_number
from native_canvas import save_private
from runtime_cache import read_json

FILE = 'documents/reviews.json'
LIMIT = 800
HK = ZoneInfo('Asia/Hong_Kong')


def digest(value):
    # Source order and last scan timestamps do not constitute new evidence.
    def stable(v):
        if isinstance(v, dict):
            return {k: stable(x) for k, x in sorted(v.items()) if k not in {'checkedAt', 'collected_at'}}
        if isinstance(v, list):
            rows = [stable(x) for x in v]
            return sorted(rows, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
        return v
    return hashlib.sha256(json.dumps(stable(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load(profile, config):
    value = copy.deepcopy(read_json(Path(profile) / FILE, {}))
    owner = str((config.get('user') or {}).get('id') or '')
    if not isinstance(value, dict) or value.get('accountId') != owner or not owner:
        return {'schema': 1, 'accountId': owner, 'decisions': {}}
    if not isinstance(value.get('decisions'), dict): value['decisions'] = {}
    return copy.deepcopy(value)


def protected_schemes(profile, schemes):
    schemes = copy.deepcopy(schemes)
    for code, base in read_json(Path(profile) / 'canvas-grading-settings.json', {}).items():
        if code in schemes and base.get('hidden'):
            schemes[code]['hidden'] = True
            schemes[code]['verified'] = False
        # Canvas settings are published before file reconciliation during sync.
        # New restrictions must take effect immediately, including that interval.
        for part in (schemes.get(code) or {}).get('components') or []:
            ids = part.get('canvasGroupIds') or ([part['canvasGroupId']] if 'canvasGroupId' in part else [])
            groups = [g for g in base.get('components') or [] if g.get('canvasGroupId') in ids]
            if ids:
                # Bind review decisions to individual Canvas rules, not only
                # their sum. A 20/20 -> 10/30 change must invalidate old consent.
                part['canvasRuleDigest'] = digest({'hidden': bool(base.get('hidden')), 'groups': [
                    {key: g.get(key) for key in ('canvasGroupId', 'weight', 'mode', 'complex')}
                    for g in groups]})
            if any(g.get('complex') for g in groups):
                part.update(complex=True, mode='show_only')
                schemes[code]['verified'] = False
            weights = [g.get('weight') for g in groups]
            if groups and part.get('weight') is not None and all(finite_number(w) is not None for w in weights):
                mismatch = abs(sum(weights) - part['weight']) > .001
                if part.get('equalItems') and len(groups) == part.get('expectedCount'):
                    mismatch = mismatch or any(abs(w - part['weight'] / len(groups)) > .001 for w in weights)
                if mismatch:
                    part['conflict'] = True
                    part['canvasConflict'] = True
                    part['sources'] = (part.get('sources') or []) + [s for g in groups for s in g.get('sources') or []]
                    schemes[code].update(verified=False, conflict=True)
    return schemes


def candidates(documents, snapshot, state, config, schemes):
    evidence = documents.get('evidence') or {}
    result = []
    assignments = snapshot.get('assignments') or {}
    courses = {c.get('code') for c in config.get('courses') or []}
    pending = {}
    for row in evidence.get('pending') or []:
        if row.get('course') not in courses: continue
        identifier = 'date:document:' + str(row.get('id'))
        if identifier in pending:
            pending[identifier]['sources'] += copy.deepcopy(row.get('sources') or [])
            continue
        item = {**copy.deepcopy(row), 'id': identifier, 'evidenceId': row.get('id'), 'type': 'date'}
        item['assignmentIds'] = []
        for source in row.get('sources') or []:
            match = re.fullmatch(r'(?:canvas-assignment:|\d+:assignment:)([^:]+)', str(source.get('documentId', '')))
            if match and assignments.get(match[1], {}).get('course') == row.get('course'):
                item['assignmentIds'].append(match[1])
        item['assignmentIds'] = sorted(set(item['assignmentIds']))
        related = []
        if '现有安排' in str(row.get('reason')):
            for i, manual in enumerate(state.get('manual_deadlines') or []):
                if manual.get('course') == row.get('course') and (manual.get('item') == row.get('title') or
                        row.get('pattern') and re.search(row['pattern'], manual.get('item') or '', re.I)):
                    related.append(i)
        item['manualIndex'] = related[0] if len(related) == 1 else None
        item['manualSource'] = (state.get('manual_deadlines') or [])[related[0]] if len(related) == 1 else None
        if item['manualSource']:
            manual_row = item['manualSource']
            item['assignmentIds'] = sorted(set(item['assignmentIds']) | {str(aid) for aid, a in assignments.items()
                if str(aid) == str(manual_row.get('assignment_id')) or
                manual_row.get('url') and a.get('html_url') == manual_row['url']})
        item['canvasSources'] = {aid: {k: assignments.get(aid, {}).get(k) for k in ('name', 'due_at', 'html_url')}
                                 for aid in item['assignmentIds']}
        pending[identifier] = item
    for item in pending.values():
        if len(item['assignmentIds']) > 1 or any(sum(aid in p['assignmentIds'] for p in pending.values()) > 1 for aid in item['assignmentIds']):
            item['lockedReason'] = '原文涉及多个作业或阶段，对应关系尚未明确；请分别核对 Canvas 原文，不能合并录入一个日期。'
    represented = {aid for item in pending.values() if not item.get('lockedReason') for aid in item['assignmentIds']}
    result += list(pending.values())
    manual_urls = {r.get('url') for r in state.get('manual_deadlines') or [] if r.get('url')}
    manual_ids = {str(r.get('assignment_id')) for r in state.get('manual_deadlines') or [] if r.get('assignment_id')}
    from assessment_types import EXAM_RE, classify
    for aid, row in assignments.items():
        if (str(aid) in represented or row.get('course') not in courses or row.get('due_at') or row.get('lock_at') or
                row.get('sub_state') in {'submitted', 'graded', 'pending_review'} or
                row.get('html_url') in manual_urls or str(aid) in manual_ids): continue
        if not (row.get('is_quiz') or (row.get('points') or 0) > 0 or EXAM_RE.search(row.get('name') or '')): continue
        result.append({'id': 'date:canvas:' + str(aid), 'type': 'date', 'course': row.get('course'),
            'title': row.get('name'), 'kind': classify(row.get('name') or ''), 'assignmentIds': [str(aid)],
            'reason': 'Canvas 尚未公布截止日期；可按已核对的原文录入。', 'needsReview': False,
            'sources': [{'documentId': 'canvas-assignment:' + str(aid), 'title': row.get('name'),
                         'url': row.get('html_url'), 'locator': 'Canvas 作业', 'quote': '尚未公布截止日期'}]})
    for row in evidence.get('detailConflicts') or []:
        if row.get('course') not in courses: continue
        result.append({**copy.deepcopy(row), 'id': 'detail:' + digest([row.get('course'), row.get('title'), row.get('field')])[:24],
                       'type': 'detail', 'reason': '来源中的' + ('时间' if row.get('field') == 'time' else '地点') + '不一致'})
    for code, scheme in schemes.items():
        parts = scheme.get('components') or []
        if code not in courses or not parts or scheme.get('verified') and not scheme.get('conflict'): continue
        # Stable keys identify full components; regex and Canvas group mapping remain server-controlled.
        parts = [{**copy.deepcopy(p), 'key': str(p.get('key') or 'component:' + str(i))} for i, p in enumerate(parts)]
        locked = ''
        if scheme.get('hidden'): locked = '老师尚未开放课程总成绩，人工确认不能解锁折算。'
        elif any(p.get('complex') or p.get('unequalItems') or p.get('mode') == 'show_only' for p in parts):
            locked = '评分规则或单项对应关系尚不完整；请核对来源并等待完整规则，暂不能人工解锁折算。'
        elif any(p.get('canvasConflict') or any(str(s.get('documentId', '')).startswith('canvas-grade:') for s in p.get('sources') or []) and p.get('conflict') for p in parts):
            locked = '文件与 Canvas 评分设置仍有冲突，请先核对并更新来源，不能仅凭确认覆盖。'
        result.append({'id': 'grading:' + code, 'type': 'grading', 'course': code, 'title': '课程评分方案',
                       'reason': scheme.get('note') or '评分方案尚未核实完整。', 'components': parts,
                       'lockedReason': locked, 'sources': [s for p in parts for s in p.get('sources') or []]})
    for item in result: item['digest'] = digest(item)
    return result


def history_entry(item, value):
    entry = {**item, **value}
    if item.get('type') == 'grading':
        weights = {p['key']: p['weight'] for p in value.get('components') or []}
        entry['components'] = [{**p, 'weight': weights.get(p['key'], p.get('weight'))} for p in item.get('components') or []]
    return entry


def project(profile, config, documents, snapshot, state, schemes):
    documents = copy.deepcopy(documents)
    schemes = protected_schemes(profile, schemes)
    items = candidates(documents, snapshot, state, config, schemes)
    saved = load(profile, config)['decisions']
    pending, history, events, suppressed, hidden_canvas, hidden_manual = [], [], [], set(), set(), set()
    hidden_canvas.update(aid for item in items if item.get('evidenceId') and not item.get('lockedReason') for aid in item.get('assignmentIds') or [])
    detail_values = []
    evidence = documents.setdefault('evidence', {})
    live = {item['id']: item for item in items}
    for item in items:
        decision = saved.get(item['id'])
        if not isinstance(decision, dict) or decision.get('digest') != item['digest']:
            pending.append({**item, 'stale': bool(decision)})
            continue
        action, value = decision.get('action'), decision.get('value') or {}
        if action not in {'confirm', 'dismiss'}: pending.append(item); continue
        history.append({**history_entry(item, value), 'action': action, 'at': decision.get('at'), 'resolved': True, 'stale': False})
        if item['type'] == 'date':
            eid = item.get('evidenceId')
            evidence['pending'] = [r for r in evidence.get('pending') or [] if not eid or r.get('id') != eid]
            for aid in item.get('assignmentIds') or []:
                hidden_canvas.add(aid)
                (evidence.get('assignmentConflicts') or {}).pop(aid, None)
            if action == 'confirm':
                suppressed.update(item.get('assignmentIds') or [])
                if item.get('manualIndex') is not None: hidden_manual.add(item['manualIndex'])
                aid = next(iter(item.get('assignmentIds') or []), None)
                assignment = (snapshot.get('assignments') or {}).get(aid, {})
                if assignment.get('sub_state') in {'submitted', 'graded', 'pending_review'}: continue
                events.append({**value, 'id': 'review:' + item['id'], 'source': 'canvas' if aid else 'documents',
                    'course': item['course'], 'title': value['title'], 'rawTitle': value['title'],
                    'sources': item.get('sources') or [], 'sourceLabel': '原文核实 · 本机记录', 'editable': False,
                    'reviewId': item['id'], 'pending': False, 'weight': '—', 'status': '待处理',
                    'url': assignment.get('html_url') or next((s.get('url') for s in item.get('sources') or [] if s.get('url')), ''),
                    'submissionState': assignment.get('sub_state'),
                    'submissionStateObserved': assignment.get('submission_state_observed') is True,
                    'cached': bool(assignment.get('cached')), 'timeText': '具体时间待确认' if not value.get('time') else '',
                    'originalCanvasDueAt': assignment.get('due_at')})
        elif item['type'] == 'detail':
            detail_values.append((item, value))
            for row in (evidence.get('deadlines') or []) + (evidence.get('pending') or []):
                if row.get('course') == item['course'] and row.get('title') == item['title']:
                    fields = ('time', 'endTime') if item['field'] == 'time' else ('location',)
                    row.update({k: value.get(k, '') for k in fields})
                    if item['field'] == 'time': row['timeText'] = value['time'] + ('–' + value['endTime'] if value.get('endTime') else '')
                    word = '时间' if item['field'] == 'time' else '地点'
                    row['warnings'] = [w for w in row.get('warnings') or [] if word not in w or '不一致' not in w]
            evidence['detailConflicts'] = [r for r in evidence.get('detailConflicts') or [] if not
                (r.get('course') == item['course'] and r.get('title') == item['title'] and r.get('field') == item['field'])]
        elif item['type'] == 'grading':
            weights = {p['key']: p['weight'] for p in value['components']}
            parts = [{**p, 'weight': weights[p['key']], 'conflict': False} for p in item['components']]
            schemes[item['course']].update(components=parts, verified=True, conflict=False,
                note='按本机已核实的完整评分方案折算；仍仅计入已公布且对应明确的成绩。')
            evidence.setdefault('grading', {})[item['course']] = parts
    # Date and detail decisions are independent. Apply the more specific field
    # review last, even when the date review changed the visible event title.
    for event in events + [row for row in pending if row['type'] == 'date']:
        original = live[event.get('reviewId') or event['id']]
        for item, value in detail_values:
            if original.get('course') != item['course'] or original.get('title') != item['title']: continue
            fields = ('time', 'endTime') if item['field'] == 'time' else ('location',)
            event.update({k: value.get(k, '') for k in fields})
            if item['field'] == 'time': event['timeText'] = value['time'] + ('–' + value['endTime'] if value.get('endTime') else '')
    for identifier, decision in saved.items():
        if identifier in live and decision.get('digest') == live[identifier]['digest']: continue
        history.append({**history_entry(decision.get('entry', {}), decision.get('value', {})), 'id': identifier,
                        'digest': decision.get('digest'), 'action': decision.get('action'), 'at': decision.get('at'),
                        'resolved': False, 'stale': True})
    documents['reviewCount'] = sum(bool(p.get('needsReview')) for p in evidence.get('pending') or []) + sum(
        bool(c.get('conflict')) for parts in (evidence.get('grading') or {}).values() for c in parts) + len(evidence.get('detailConflicts') or [])
    return {'documents': documents, 'schemes': schemes, 'reviews': {'items': pending, 'history': sorted(history, key=lambda r: r.get('at') or '', reverse=True)},
            'events': events, 'suppressed': suppressed, 'hiddenCanvas': hidden_canvas, 'hiddenManual': hidden_manual}


def text(value, limit=160):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value): raise ValueError('请填写有效的简短文字。')
    return value.strip()


def time(value):
    value = text(value or '', 5)
    if value and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value): raise ValueError('时间格式应为 HH:MM。')
    return value


def mutate(profile, config, documents, snapshot, state, schemes, payload):
    allowed = {'id', 'digest', 'action', 'date', 'time', 'endTime', 'location', 'title', 'kind', 'note', 'components'}
    if set(payload) - allowed: raise ValueError('核实请求包含无效字段。')
    action, identifier = payload.get('action'), payload.get('id')
    if action not in {'confirm', 'dismiss', 'reset'} or not isinstance(identifier, str): raise ValueError('核实操作无效。')
    saved = load(profile, config)
    if not saved['accountId']: raise ValueError('请先连接当前账号。')
    if action == 'reset':
        old = saved['decisions'].get(identifier)
        if not old or payload.get('digest') != old.get('digest'): raise ValueError('核实记录已变化，请刷新后重试。')
        saved['decisions'].pop(identifier)
    else:
        item = next((r for r in candidates(documents, snapshot, state, config, protected_schemes(profile, schemes)) if r['id'] == identifier), None)
        if not item or item['digest'] != payload.get('digest'): raise ValueError('来源已更新，请刷新并重新核对后再保存。')
        if action == 'confirm' and item.get('lockedReason'): raise ValueError(item['lockedReason'])
        raw_note = payload.get('note') or ''
        note = text(re.sub(r'[\r\n\t]+', ' ', raw_note) if isinstance(raw_note, str) else raw_note, 500)
        value = {'note': note}
        if action == 'dismiss':
            if item['type'] != 'date' or not note: raise ValueError('请说明此日期事项不适用的原因。')
        elif item['type'] == 'date':
            date = payload.get('date')
            try:
                if not isinstance(date, str) or dt.date.fromisoformat(date).isoformat() != date: raise ValueError()
            except (ValueError, TypeError): raise ValueError('请填写核实后的有效日期（YYYY-MM-DD）。') from None
            title = text(payload.get('title') or item['title'], 120)
            kind = payload.get('kind') or item.get('kind')
            if not title or kind not in KINDS: raise ValueError('请填写名称并选择有效分类。')
            value.update(date=date, title=title, kind=kind, time=time(payload.get('time')),
                         endTime=time(payload.get('endTime')), location=text(payload.get('location') or ''))
        elif item['type'] == 'detail':
            if item['field'] == 'time':
                value.update(time=time(payload.get('time')), endTime=time(payload.get('endTime')))
                if not value['time']: raise ValueError('请填写核实后的开始时间。')
            else:
                value['location'] = text(payload.get('location') or '')
                if not value['location']: raise ValueError('请填写核实后的地点。')
        elif item['type'] == 'grading':
            if item.get('lockedReason'): raise ValueError(item['lockedReason'])
            rows = payload.get('components')
            if not isinstance(rows, list) or len(rows) != len(item['components']): raise ValueError('请核实完整评分方案。')
            weights = {}
            for row in rows:
                if not isinstance(row, dict) or set(row) != {'key', 'weight'}: raise ValueError('评分方案格式无效。')
                key, weight = row['key'], finite_number(row['weight'])
                if not isinstance(key, str) or key in weights or weight is None or not 0 < weight <= 100: raise ValueError('每项权重须为 0–100 之间的正数。')
                weights[key] = weight
            if set(weights) != {p['key'] for p in item['components']} or abs(sum(weights.values()) - 100) > .001: raise ValueError('完整评分方案的权重须合计 100%。')
            for part in item['components']:
                if (any(str(s.get('documentId', '')).startswith('canvas-grade:') for s in part.get('sources') or [])
                        and part.get('weight') is not None and weights[part['key']] != part['weight']):
                    raise ValueError('已明确的 Canvas 权重不能由本机核实改写，请先核对并更新来源。')
            value['components'] = [{'key': k, 'weight': v} for k, v in weights.items()]
        if value.get('endTime') and (not value.get('time') or value['endTime'] <= value['time']): raise ValueError('结束时间须晚于开始时间。')
        if identifier not in saved['decisions'] and len(saved['decisions']) >= LIMIT: raise ValueError('核实记录已达到上限，请先撤销不再需要的记录。')
        saved['decisions'][identifier] = {'digest': item['digest'], 'action': action, 'value': value,
            'at': dt.datetime.now(HK).isoformat(timespec='seconds'), 'entry': item}
    save_private(Path(profile) / FILE, saved)
    return '已撤销核实，恢复当前来源。' if action == 'reset' else '已记录不适用原因，可在核实记录中撤销。' if action == 'dismiss' else '核实结果已录入，相关安排和统计已更新。'
