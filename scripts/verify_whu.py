#!/usr/bin/env python3
"""Verify WHU discovery through real stdio MCP, optionally with manual login.

Example:
  python scripts/verify_whu.py --query '"Attention is all you need? When responsiveness short-circuits responsibility"' --login

Reports and downloaded samples stay under ignored verification/, never in a package.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from verify_stdio import StdioClient, _parse, handshake

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', default='"Attention is all you need? When responsiveness short-circuits responsibility"')
    parser.add_argument('--login', action='store_true', help='允许打开可见窗口供用户认证')
    parser.add_argument('--wait', type=int, default=240, help='人工认证最大等待秒数（1–600）')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification' / 'whu')
    parser.add_argument('--pdf-url', default='', help='可选：实际 PDF 或武大 PDF 全文入口')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    client = StdioClient(argparse.Namespace(providers='whu', browser_channel='', show_browser=False))
    report = {'run_at': datetime.now().astimezone().isoformat(), 'steps': []}

    def call(tool, arguments=None, timeout=70):
        started = time.monotonic()
        result = _parse(client.call_tool(tool, arguments or {}, timeout=timeout))
        entry = {'tool': tool, 'arguments': arguments or {},
                 'elapsed_seconds': round(time.monotonic() - started, 2), 'result': result}
        report['steps'].append(entry)
        brief = {k: result[k] for k in ('success', 'error_code', 'message', 'count', 'file_path') if k in result}
        print(tool, json.dumps(brief, ensure_ascii=False), flush=True)
        return result

    def authenticate(url):
        if not args.login:
            return False
        opened = call('whu_open_login_page', {'url': url})
        if not opened.get('success'):
            return False
        print('请在窗口完成人工认证。脚本将在限定时间内检查状态。', flush=True)
        deadline = time.monotonic() + max(1, min(args.wait, 600))
        while time.monotonic() < deadline:
            result = call('whu_finish_login')
            if result.get('success'):
                return True
            if result.get('error_code') not in ('authentication_required', 'verification_required'):
                return False
            time.sleep(min(3, max(0, deadline - time.monotonic())))
        return False

    try:
        report['handshake'] = handshake(client)
        found = call('whu_search', {'query': args.query, 'limit': 3})
        if not found.get('success') or not found.get('papers'):
            return 1
        paper = found['papers'][0]
        links = paper.get('access_links', [])
        article_url = paper.get('preferred_url') or next((x['url'] for x in links if 'whu.metaersp.cn/middle?' in x['url']
                            or '全文' in x.get('text', '')), paper['detail_url'])
        read = call('whu_read_page', {'url': article_url})
        if not read.get('success') and read.get('error_code') in ('authentication_required', 'verification_required', 'access_denied'):
            if authenticate(article_url):
                read = call('whu_read_page', {'url': article_url})
        pdf_url = args.pdf_url or next(iter(read.get('pdf_urls', [])), '')
        if not pdf_url:
            pdf_url = next((x['url'] for x in links if 'PDF' in x.get('text', '').upper()), '')
        if not pdf_url:
            pdf_url = next((x['url'] for x in paper.get('source_links', []) if x.get('kind') == 'linktopdf'), '')
        if not pdf_url:
            report['download'] = {'success': False, 'message': '未发现可实证的 PDF 链接。'}
            return 1
        downloaded = call('whu_download_pdf', {'url': pdf_url, 'title': paper['title'],
                            'output_dir': str(args.output.resolve())}, timeout=310)
        report['download'] = downloaded
        return 0 if downloaded.get('success') else 1
    finally:
        client.close()
        report_path = args.output / 'report.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print('报告：', report_path, flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
