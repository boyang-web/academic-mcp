#!/usr/bin/env python3
"""Read catalogue subscription metadata; does not download any research content."""
import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from academic_data_mcp.config import AppConfig
from academic_data_mcp.gateways.whu.client import WhuGateway


async def inventory(args):
    service = WhuGateway(AppConfig.from_env())
    started = time.monotonic()
    report = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'details': [], 'failures': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        catalogue = await service.list_databases()
        report['catalogue'] = catalogue
        save()
        if not catalogue.get('success'):
            print(catalogue, flush=True);return
        rows = catalogue['databases']
        rows.sort(key=lambda r: (not any(x in r['name'] for x in ['知网', 'CSMAR', 'Springer']), r['name']))
        print('CATALOGUE', len(rows), dict(Counter(r['category'] for r in rows)), flush=True)
        for index, row in enumerate(rows[:args.details]):
            if time.monotonic() - started > args.max_seconds:
                report['stopped_by_time_budget'] = True;break
            result = await service.get_database(row['id'])
            if result.get('success'):
                d = result['database'];report['details'].append(d)
                print(index+1, d['name'], d['subscription_status'],
                      'children', d['structured_children_total'],
                      'purchased_mentions', len(d['purchased_scope_mentions']), flush=True)
            else:
                report['failures'].append({'id': row['id'], 'name': row['name'], 'result': result})
                print(index+1, row['name'], result.get('error_code'), flush=True)
            save()
            await asyncio.sleep(0.15)
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['status_counts'] = dict(Counter(d['subscription_status'] for d in report['details']))
        print('SUMMARY', report.get('status_counts'), 'details', len(report['details']),
              'failures', len(report['failures']), 'seconds', report['elapsed_seconds'], flush=True)
    finally:
        save();await service.aclose()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):stream.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path('verification') / datetime.now().strftime('%Y-%m-%d') / 'inventory.json')
    p.add_argument('--details', type=int, default=280)
    p.add_argument('--max-seconds', type=int, default=480)
    asyncio.run(inventory(p.parse_args()))


if __name__ == '__main__':main()
