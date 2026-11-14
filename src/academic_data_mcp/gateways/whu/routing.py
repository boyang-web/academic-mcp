"""Plan access from real library links; never fabricate proxy or PDF URLs."""
import re
from urllib.parse import parse_qs, urlsplit

from academic_data_mcp.browser.session import web_url


def is_library_url(url):
    host = (urlsplit(url).hostname or '').lower()
    return (host.endswith('.lib.whu.edu.cn') or host == 'lib.whu.edu.cn' or
            host in ('whu.metaersp.cn', 'whu.metaersp.com', 'whu-primo.hosted.exlibrisgroup.com.cn'))


def connector_for(url):
    parsed = urlsplit(url)
    target = parse_qs(parsed.query).get('url', [''])[0] if is_library_url(url) else url
    host = (urlsplit(target).hostname or '').lower()
    # Classify WHU's observed rewritten URLs, without constructing new ones.
    rewritten = parsed.path.lower() if is_library_url(url) else ''
    if host.endswith('.cnki.net') or host == 'cnki.net' or '/s/net/cnki/' in rewritten:
        return 'cnki'
    if host.endswith('.csmar.com') or host == 'csmar.com' or '/s/com/csmar/' in rewritten:
        return 'csmar'
    return None


def is_pdf_url(url):
    return bool(re.search(r'\.pdf(?:[?&#]|$)|/content/pdf/|/doi/pdf/|/pdfft(?:[?&#]|$)', url, re.I))


def access_plan(url):
    web_url(url)
    connector = connector_for(url)
    fallback = None
    if connector == 'cnki':
        fallback = {'provider': 'cnki', 'role': 'internal_adapter', 'entry_tool': 'whu_call_database',
                    'steps': ['login_status', 'search', 'download_pdf'],
                    'note': '仅在图书馆通路不可用或需知网精确检索时显式使用；其登录态独立。'}
    elif connector == 'csmar':
        fallback = {'provider': 'csmar', 'role': 'internal_adapter', 'entry_tool': 'whu_call_database',
                    'steps': ['login_status', 'list_series', 'preview', 'download'],
                    'note': '国泰安使用网关内部数据适配器；完整导出消耗机构配额，不自动触发。'}
    return {'success': True, 'primary_provider': 'whu',
            'route_kind': 'library' if is_library_url(url) else 'publisher_source',
            'url': url, 'next_tool': 'whu_download_pdf' if is_pdf_url(url) else 'whu_read_page', 'next_arguments': {'url': url},
            'supplementary': fallback, 'permission_verified': False,
            'note': '保留原始链接，优先在武大会话读取，再使用真实 PDF 链接下载；失败不会自动切换身份或批量导出。'}


def record_routes(paper):
    seen, routes = set(), []
    for item in [*paper.get('access_links', []), *paper.get('source_links', [])]:
        url = item.get('url', '')
        host = (urlsplit(url).hostname or '').lower()
        if not url.startswith(('https://', 'http://')) or url in seen or 'whu-primo.' in host:
            continue
        seen.add(url)
        routes.append({'url': url, 'label': item.get('text') or item.get('kind', ''),
                       'route_kind': 'library' if is_library_url(url) else 'publisher_source',
                       'supplementary_connector': connector_for(url)})
    routes.sort(key=lambda r: (r['route_kind'] != 'library', is_pdf_url(r['url'])))
    return routes


def database_entries(groups):
    """Flatten the homepage's own published quick-link tree, not all subscriptions."""
    rows, seen = [], set()
    def walk(items, category=''):
        for item in items:
            name, url = item.get('name', ''), item.get('link') or ''
            database_id = parse_qs(urlsplit(url).query).get('databaseNum', [''])[0]
            if database_id and database_id not in seen:
                seen.add(database_id)
                rows.append({'id': database_id, 'name': name, 'category': category,
                             'url': url, 'supplementary_connector': connector_for(url)})
            walk(item.get('children') or [], name or category)
    walk(groups)
    return rows
