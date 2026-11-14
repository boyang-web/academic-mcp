#!/usr/bin/env python3
"""Low-volume, end-to-end WHU publisher sampling through real MCP calls.

One identified paper per publisher, no fabricated resolver/PDF URLs and no
automatic verification retries. A successful public-source download is reported
separately from a verified WHU proxy route. Artifacts stay under verification/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from verify_stdio import StdioClient, _parse, handshake

SAMPLES = [
    ('Springer', '10.1007/s00146-025-02700-4', ('springer.com', 'springernature.com')),
    ('ScienceDirect', '10.1016/j.irfa.2023.102876', ('sciencedirect.com',)),
    ('Wiley', '10.1002/asi.23552', ('wiley.com',)),
    ('Taylor & Francis', '10.1080/15481603.2024.2380126', ('tandfonline.com',)),
    ('Nature', '10.1038/nature14539', ('nature.com',)),
    ('Oxford', '10.1093/bib/bbad467', ('oup.com',)),
    ('IEEE', '10.1109/TIFS.2026.3725564', ('ieee.org',)),
    ('JSTOR', '10.2307/1879431', ('jstor.org',)),
]


TITLES = {
    'Wiley': 'The sharing economy: Why people participate in collaborative consumption',
    'Springer': 'Attention is all you need? When responsiveness short-circuits responsibility',
    'Taylor & Francis': 'Cross attention is all you need: relational remote sensing change detection with transformer',
}


def is_proxy(url):
    host = urlsplit(url).hostname or ''
    return host.endswith('.lib.whu.edu.cn') or host in ('whu.metaersp.cn', 'whu.metaersp.com')


def matches_domain(url, domains):
    host = urlsplit(url).hostname or ''
    return any(host == d or host.endswith('.' + d) or d in url or
               (is_proxy(url) and '/s/' + '/'.join(reversed(d.split('.'))) + '/' in url)
               for d in domains)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', default='', help='只运行匹配的出版商，逗号分隔')
    parser.add_argument('--output', type=Path, default=Path('verification/2026-11-14/coverage-whu'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    client = StdioClient(argparse.Namespace(providers='whu', browser_channel='', show_browser=True))
    report = {'run_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
              'method': 'One paper per publisher; current WHU session/network; no CAPTCHA bypass or indefinite retry.',
              'samples': [], 'calls': []}

    def save():
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = ['# 武大图书馆通路抽样', '',
                 '仅代表本次网络、账号与具体论文，不代表整库权限；公开来源下载单独标记。', '',
                 '| 平台 | 检索定位 | 目标页 | 武大代理 | PDF | 结果 |',
                 '|---|---|---|---|---|---|']
        for row in report['samples']:
            lines.append('| ' + ' | '.join(str(row.get(k, '—')).replace('|', '/') for k in
                ('publisher', 'search_found', 'page_read', 'whu_proxy', 'pdf_saved', 'outcome')) + ' |')
        (args.output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def call(tool, arguments):
        started = time.monotonic()
        try:
            result = _parse(client.call_tool(tool, arguments, timeout=75))
        except Exception as error:
            result = {'success': False, 'error_code': 'probe_error', 'message': type(error).__name__}
        if not isinstance(result, dict):
            result = {'success': False, 'error_code': 'unexpected_response'}
        report['calls'].append({'tool': tool, 'arguments': arguments,
                                'seconds': round(time.monotonic() - started, 2), 'result': result})
        print(tool, json.dumps({k: result[k] for k in ('success', 'error_code', 'count', 'message', 'file_path') if k in result}, ensure_ascii=False), flush=True)
        save()
        return result

    try:
        report['handshake'] = handshake(client)
        report['navigation'] = call('whu_list_databases', {})
        filters = [x.strip().lower() for x in args.only.split(',') if x.strip()]
        for publisher, doi, domains in SAMPLES:
            if filters and not any(x in publisher.lower() for x in filters):
                continue
            print('\nSAMPLE', publisher, doi, flush=True)
            row = {'publisher': publisher, 'doi': doi, 'search_found': False,
                   'page_read': False, 'whu_proxy': False, 'pdf_saved': False}
            report['samples'].append(row)
            found = call('whu_search', {'query': doi, 'limit': 5})
            if not found.get('success'):
                row['outcome'] = '检索失败：' + found.get('error_code', 'unknown'); save(); continue
            paper = next((p for p in found.get('papers', []) if p.get('doi', '').lower() == doi.lower()), None)
            if not paper and publisher in TITLES:
                alternate = call('whu_search', {'query': '"' + TITLES[publisher] + '"', 'limit': 5})
                paper = next((p for p in alternate.get('papers', []) if p.get('doi', '').lower() == doi.lower()), None)
            if not paper:
                row['outcome'] = '未定位到目标 DOI，不能判断目标库权限'; save(); continue
            row.update(search_found=True, title=paper['title'], record_id=paper['record_id'])
            links = paper.get('access_links', [])
            routes = [l['url'] for l in links if is_proxy(l['url']) and matches_domain(l['url'], domains)]
            if not routes:
                detail = call('whu_read_page', {'url': paper['detail_url']})
                routes = [l['url'] for l in detail.get('links', []) if is_proxy(l['url']) and matches_domain(l['url'], domains)]
            # Prefer article/HTML routes over PDF endpoints for metadata & cookies.
            routes.sort(key=lambda u: ('/pdf' in u.lower() or '.pdf' in u.lower()))
            if routes:
                article_url = routes[0]
                row['route_kind'] = 'WHU resolver'
            else:
                direct = [l['url'] for l in paper.get('source_links', []) if matches_domain(l['url'], domains)]
                direct.sort(key=lambda u: ('/pdf' in u.lower() or '.pdf' in u.lower()))
                if not direct:
                    row['outcome'] = '未发现该平台全文入口（不能用其他平台的副本充数）'; save(); continue
                article_url = direct[0]
                row['route_kind'] = 'direct publisher; institution access not established'
            row['entry_url'] = article_url
            page = call('whu_read_page', {'url': article_url})
            row['resolved_url'] = page.get('url', '')
            if not page.get('success'):
                row['outcome'] = '页面失败：' + page.get('error_code', 'unknown'); save(); continue
            row['page_read'] = bool(page.get('text', '').strip())
            row['whu_proxy'] = is_proxy(page.get('url', ''))
            pdfs = page.get('pdf_urls', [])
            pdfs = [u for u in pdfs if matches_domain(u, domains)]
            if not pdfs:
                pdfs = [u for u in routes if '/pdf' in u.lower() or '.pdf' in u.lower()]
            if not pdfs:
                row['outcome'] = '页面可读，但未取得 PDF 链接' + ('（按钮仍在加载）' if page.get('pdf_link_pending') else '')
                save(); continue
            pdfs.sort(key=lambda u: not is_proxy(u))
            # One PDF request only; failures stay failures.
            pdf = call('whu_download_pdf', {'url': pdfs[0], 'title': publisher + ' - ' + doi.replace('/', '_'),
                       'output_dir': str((args.output / 'files').resolve())})
            if pdf.get('success'):
                path = Path(pdf['file_path']); data = path.read_bytes()
                row['pdf_saved'] = data.startswith(b'%PDF-') and hashlib.sha256(data).hexdigest() == pdf['sha256']
                row['pdf_path'] = str(path); row['bytes'] = len(data)
                row['whu_proxy'] = row['whu_proxy'] or is_proxy(pdf.get('resolved_url', ''))
                row['outcome'] = '已下载并校验' if row['whu_proxy'] else '公开/直连来源下载成功；未证实武大授权通路'
            else:
                row['outcome'] = 'PDF 失败：' + pdf.get('error_code', 'unknown')
            save()
    finally:
        client.close()
        save()
        print('\nREPORT', (args.output / 'report.md').resolve(), flush=True)


if __name__ == '__main__':
    main()
