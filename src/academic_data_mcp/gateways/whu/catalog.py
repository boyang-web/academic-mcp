"""Normalize WHU's published catalogue and explicit subscription statements."""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

CATEGORIES = {'中文库', '外文库', '试用库', '免费库', '自建库'}


def plain(value):
    text = re.sub(r'</(?:p|li|div)>|<br\s*/?>', '\n', value or '', flags=re.I)
    text = html.unescape(re.sub(r'<[^>]*>', '', text))
    return '\n'.join(re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip())


def catalogue_rows(payload, directory_url):
    origin = '{0.scheme}://{0.netloc}'.format(urlsplit(directory_url))
    rows, seen = [], set()
    for layout in payload:
        for group in layout.get('tagSet', []):
            category = group.get('customTagName', '')
            if category not in CATEGORIES:
                continue
            for item in group.get('portalGuideVoList', []):
                identifier = item.get('databaseNum', '')
                if not identifier or identifier in seen:
                    continue
                seen.add(identifier)
                rows.append({'id': identifier, 'name': item.get('customName', ''),
                             'category': category, 'url': origin + '/databaseDetail?databaseNum=' + identifier,
                             'platform_url': item.get('databaseUrl', ''),
                             'subscription_status': '未核实',
                             'subscription_candidate': category in ('中文库', '外文库'),
                             'suspension_notice': plain(item.get('suspensionExplain')),
                             'access_verified': False})
    return rows


def purchased_mentions(intro):
    """Only label numbered items after an explicit library purchase statement.

    Supplier claims about hundreds of products are never treated as WHU purchases.
    """
    text = plain(intro)
    markers = ('我校已采购的数据库内容包括', '下列中国知网的核心子库我馆已购买',
               '我馆已购买的子库', '我馆购买的子库', '我校已购买的子库')
    starts = [text.find(m) for m in markers if m in text]
    if not starts:
        return []
    segment = text[min(starts):]
    matches = re.findall(r'(?:^|[\n；;：:])\s*\d+\s*[.．、]\s*([^\n；;]+)', segment)
    result = []
    for value in matches:
        name = re.split(r'[:：]', value, maxsplit=1)[0].strip().rstrip('。.')
        if name and len(name) <= 140 and name not in result:
            result.append(name)
    return result


def database_detail(data, row, children, children_total, children_complete):
    return {**row, 'library_code': str(data.get('libraryCode', '')), 'name': data.get('customName') or row['name'],
            'subscription_status': data.get('otherTagName') or '未标明',
            'subscription_verified': data.get('otherTagName') in ('订阅', '采购'),
            'platform_url': data.get('databaseUrl') or row.get('platform_url', ''),
            'purchase_note': plain(data.get('commonNote')),
            'declared_years': plain(data.get('recordYear')),
            'record_range': plain(data.get('recordRange')),
            'special_notice': plain(data.get('specialNotice')),
            'suspension_notice': plain(data.get('suspensionExplain')),
            'coverage_label': data.get('level', ''),
            'record_types': data.get('recordType', ''),
            'purchased_scope_mentions': purchased_mentions(data.get('intro')),
            'scope_evidence': '官网简介明确列出的已购项目，可能是系列/子库；不等于逐项可下载实证。',
            'subscription_scope_text': plain(data.get('intro')),
            'children': children, 'structured_children_total': children_total,
            'structured_children_complete': children_complete,
            'child_scope_verified': False,
            'child_scope_warning': ('包含其他机构记录，仅作候选，不能据此判断武大购买范围。'
                                    if any(c.get('source_library_code') not in ('', str(data.get('libraryCode', ''))) for c in children)
                                    else '子库关系不等于购买授权；以武大明确购买说明和实际账号权限为准。'),
            'access_verified': False,
            'note': '订阅、子库和实际访问分别记录；结构化子库为空不代表没有子库。'}
