"""WHU's current Primo discovery service, observed from the library homepage.

Use the browser's own PNX response and retain the institution's actual resolver
links. A catalogue's 'fulltext' flag is never treated as proof of download rights.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlencode, urlsplit

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
        self.directory_url = settings.get('DIRECTORY_URL', 'https://whu.metaersp.cn/databaseList')
        self.library_code = settings.get('LIBRARY_CODE', '131001')
        self._catalogue = None
        self._catalogue_time = 0
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
        from .catalog import catalogue_rows
        if self._catalogue is None or time.monotonic() - self._catalogue_time > 900:
            page = await self.browser()
            async with page.expect_response(
                    lambda r: urlsplit(r.url).path.endswith('/portalLdbguideList'),
                    timeout=self.timeout * 1000) as pending:
                await self.navigate(self.directory_url)
            response = await pending.value
            payload = await response.json()
            if response.status != 200 or not payload.get('flag') or not isinstance(payload.get('data'), list):
                raise AccessError('catalogue_unavailable', '完整数据库目录未返回有效元数据。')
            self._catalogue = catalogue_rows(payload['data'], self.directory_url)
            self._catalogue_time = time.monotonic()
        rows = self._catalogue
        if keyword:
            rows = [r for r in rows if keyword.casefold() in (r['name'] + ' ' + r['id']).casefold()]
        return {'success': True, 'source_url': self.directory_url,
                'count': len(rows), 'catalogue_total': len(self._catalogue), 'databases': rows,
                'scope': '完整官网目录（含订阅候选、试用、免费和自建），非全部已购；详情需单独核实。'}

    async def _metadata(self, endpoint, body):
        await self.browser()
        parsed = urlsplit(self.directory_url)
        url = f'{parsed.scheme}://{parsed.netloc}/metaapi/meta-bc-resource/edge/ldb/v1.5/{endpoint}'
        response = await self.context.request.post(url, data=body, headers={'librarycode': self.library_code},
                                                   timeout=self.timeout * 1000)
        if response.status != 200:
            raise AccessError('catalogue_unavailable', f'目录元数据返回 HTTP {response.status}。')
        payload = await response.json()
        if not payload.get('flag'):
            raise AccessError('catalogue_unavailable', '目录元数据服务未返回成功。')
        return payload

    async def get_database(self, name: str):
        async def op():
            from .catalog import database_detail
            catalogue = await self._database_catalog(name.strip())
            entries = catalogue['databases']
            exact = [r for r in entries if name.casefold() in (r['name'].casefold(), r['id'].casefold())]
            entries = exact or entries
            if len(entries) != 1:
                raise AccessError('database_not_identified', '请指定唯一数据库名称或目录 ID。', candidates=entries)
            row = entries[0]
            detail = (await self._metadata('portalLdbDetail', {'databaseNum': row['id']})).get('data')
            if not isinstance(detail, dict) or detail.get('databaseNum') != row['id']:
                raise AccessError('parse_error', '目录详情身份不匹配。')
            if str(detail.get('libraryCode', '')) != self.library_code:
                raise AccessError('metadata_scope_mismatch', '该详情属于其他机构，不能据此判定武大订阅权限。')
            children, total = [], 0
            for page in range(1, 11):
                payload = await self._metadata('getChildDatabaseList',
                    {'currentPage': page, 'pageSize': 100, 'parentNum': row['id']})
                total = int(payload.get('total', 0))
                children.extend({'id': v.get('databaseNum') or v.get('id'),
                                 'name': v.get('customName') or v.get('databaseName') or v.get('name'),
                                 'subscription_status': v.get('otherTagName') or '未标明',
                                 'source_library_code': str(v.get('libraryCode', '')),
                                 'directory_relation_only': True}
                                for v in payload.get('data', []))
                if len(children) >= total or not payload.get('data'):
                    break
            return {'success': True, 'database': database_detail(detail, row, children, total, len(children) >= total)}
        return await self.run(op)

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
