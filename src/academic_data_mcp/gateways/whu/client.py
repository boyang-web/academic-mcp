"""WHU's current Primo discovery service, observed from the library homepage.

Use the browser's own PNX response and retain the institution's actual resolver
links. A catalogue's 'fulltext' flag is never treated as proof of download rights.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from academic_data_mcp.browser.session import AccessError, WebSession, page_problem

DEFAULT_BASE = 'https://whu-primo.hosted.exlibrisgroup.com.cn'


def first(values, default=''):
    return values[0] if isinstance(values, list) and values else default


def compact_record(doc: dict, base: str, vid: str) -> dict:
    pnx = doc.get('pnx') or {}
    display, control, add = (pnx.get(k) or {} for k in ('display', 'control', 'addata'))
    rid = first(control.get('recordid'))
    if not rid:
        raise AccessError('parse_error', '珞珈返回的记录缺少 recordid。')
    links = []
    for kind, values in (pnx.get('links') or {}).items():
        if kind not in ('linktohtml', 'linktopdf', 'linkunpaywall', 'backlink'):
            continue
        for value in values:
            match = re.search(r'\$\$U(https?://.*?)(?=\$\$|$)', value)
            if match:
                links.append({'kind': kind, 'url': match.group(1)})
    return {
        'record_id': rid, 'title': first(display.get('title')),
        'authors': add.get('au') or display.get('creator') or [],
        'year': first((pnx.get('search') or {}).get('creationdate')),
        'publication': first(display.get('ispartof')),
        'abstract': first(add.get('abstract') or display.get('description')),
        'doi': first(add.get('doi')), 'resource_type': first(display.get('type')),
        'availability': (pnx.get('delivery') or {}).get('fulltext', []),
        'download_permission_verified': False, 'source_links': links,
        'detail_url': base + '/primo-explore/fulldisplay?' + urlencode({
            'docid': rid, 'context': doc.get('context', 'PC'), 'vid': vid,
            'lang': 'zh_CN', 'adaptor': doc.get('adaptor', 'primo_central_multiple_fe'),
            'tab': 'default_tab', 'search_scope': 'default_scope'}),
    }


class WhuGateway(WebSession):
    def __init__(self, config):
        settings = config.for_provider('whu')
        self.base = settings.get('BASE_URL', DEFAULT_BASE).rstrip('/')
        self.vid = settings.get('VIEW_ID', '86WHU')
        self.library_url = settings.get('LIBRARY_URL', 'https://www.lib.whu.edu.cn/')
        super().__init__(config, 'whu', self.base + '/primo-explore/search?' +
                         urlencode({'vid': self.vid, 'lang': 'zh_CN'}))
        # Publisher workflows often require an interactive browser, even after
        # institutional authentication. Never spoof a headless browser as visible.
        self.headless = not settings.flag('SHOW_BROWSER', True)
        self.visible = not self.headless

    async def search(self, query: str, limit: int = 5, start: int = 0):
        async def op():
            if not query.strip() or not 1 <= limit <= 20 or start < 0:
                raise ValueError('query 不能为空；limit 应为 1–20，start 不得小于 0。')
            url = self.base + '/primo-explore/search?' + urlencode({
                'vid': self.vid, 'institution': self.vid, 'query': 'any,contains,' + query,
                'tab': 'default_tab', 'search_scope': 'default_scope',
                'lang': 'zh_CN', 'offset': start, 'bulkSize': limit})
            self.last_search_url = url
            page = await self.browser()
            # Register before navigation, so fast/cached API responses are not missed.
            async with page.expect_response(
                    lambda r: '/primo-explore/v1/pnxs?' in r.url,
                    timeout=self.timeout * 1000) as pending:
                await self.navigate(url)
            response = await pending.value
            if response.status != 200:
                page_problem(response.url, '', response.status)
            payload = await response.json()
            if not isinstance(payload.get('docs'), list) or 'total' not in payload.get('info', {}):
                raise AccessError('parse_error', '珞珈检索响应结构变化，无法判断检索结果。', url=page.url)
            papers = [compact_record(d, self.base, self.vid) for d in payload['docs'][:limit]]
            if papers:
                await page.locator('[data-recordid]').first.wait_for(state='attached')
            visible = await page.locator('[data-recordid]').evaluate_all('''els => els.map(e => ({
                id:e.getAttribute('data-recordid'), links:Array.from(e.querySelectorAll('a[href]'))
                  .map(a=>({text:(a.innerText||'').trim(),url:a.href}))
                  .filter(a=>/^https?:/.test(a.url))}))''')
            by_id = {row['id']: row['links'] for row in visible}
            from .routing import record_routes
            for paper in papers:
                paper['access_links'] = by_id.get(paper['record_id'], [])
                paper['access_routes'] = record_routes(paper)
                paper['preferred_url'] = (paper['access_routes'][0]['url']
                                          if paper['access_routes'] else paper['detail_url'])
            return {'success': True, 'query': query, 'count': len(papers),
                    'total_count': payload['info']['total'], 'start': start,
                    'papers': papers, 'url': page.url,
                    'note': '保留 access_links 中的武大机构跳转地址；全文标签不等于已获得下载权限。'}
        return await self.run(op)

    async def _database_catalog(self, keyword=''):
        from .routing import database_entries
        library_url = self.library_url
        page = await self.navigate(library_url)
        await page.wait_for_function('''() => window.searchTemp40 &&
            Array.isArray(window.searchTemp40.searchFastLinkList)''')
        groups = await page.evaluate('searchTemp40.searchFastLinkList')
        rows = database_entries(groups)
        if keyword:
            rows = [r for r in rows if keyword.casefold() in (r['name'] + ' ' + r['id']).casefold()]
        return {'success': True, 'source_url': library_url, 'count': len(rows), 'databases': rows,
                'scope': '官网常用数据库导航，非完整订购清单；不代表已验证访问权限。'}

    async def list_databases(self, keyword: str = ''):
        return await self.run(lambda: self._database_catalog(keyword))

    async def open_database(self, name: str):
        async def op():
            if not name.strip():
                raise ValueError('请指定数据库名称或官网返回的数据库 ID。')
            catalogue = await self._database_catalog(name.strip())
            entries = catalogue['databases']
            exact = [r for r in entries if name.casefold() in (r['name'].casefold(), r['id'].casefold())]
            entries = exact or entries
            if len(entries) != 1:
                raise AccessError('database_not_identified', '请从官网返回的候选项中明确选择一个数据库。',
                                  candidates=entries)
            entry = entries[0]
            page = await self.browser(visible=True)
            await page.goto(entry['url'], wait_until='domcontentloaded')
            return {'success': True, 'opened': True, 'database': entry,
                    'url': page.url, 'permission_verified': False,
                    'message': '已打开图书馆提供的数据库入口；需要时在窗口认证，再用 whu_read_page 读取目标页。'}
        return await self.run(op)
