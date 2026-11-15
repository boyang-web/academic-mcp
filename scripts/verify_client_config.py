#!/usr/bin/env python3
"""Verify an installed stdio server using its real MCP client configuration.

Unlike verify_stdio.py, this does not inject PYTHONPATH or assume a repository
working directory. It never opens a browser or calls quota-consuming operations.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify(path: Path, name: str, timeout: float) -> None:
    config = json.loads(path.expanduser().read_text(encoding='utf-8'))
    entry = config['mcpServers'][name]
    if entry.get('disabled'):
        raise ValueError('该 MCP 服务在客户端配置中已禁用。')
    if not entry.get('command'):
        raise ValueError('此检查仅支持配置 command 的 stdio 服务。')
    env = os.environ.copy()
    env.update(entry.get('env', {}))
    # A temporary directory exposes missing installations hidden by repo cwd.
    with tempfile.TemporaryDirectory(prefix='academic-mcp-client-') as cwd:
        params = StdioServerParameters(command=entry['command'],
            args=entry.get('args', []), env=env, cwd=entry.get('cwd') or cwd,
            encoding='utf-8')
        async with asyncio.timeout(timeout):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    tools = await session.list_tools()
                    names = [tool.name for tool in tools.tools]
                    if len(names) != 13 or any(not n.startswith('whu_') for n in names):
                        raise ValueError('工具列表与当前 WHU 统一接口不符，请确认安装版本并重启客户端。')
                    result = await session.call_tool('whu_database_capabilities', {'database': 'csmar'})
                    if result.isError:
                        raise RuntimeError('能力查询返回 MCP 工具错误。')
                    payload = next(json.loads(c.text) for c in result.content if c.type == 'text')
                    if not payload.get('success'):
                        raise RuntimeError('库内能力查询失败。')
                    print(json.dumps({'success': True, 'server': name,
                        'protocol_version': init.protocolVersion, 'tool_count': len(names),
                        'capabilities_call': 'success', 'cwd': 'outside repository'},
                        ensure_ascii=False, indent=2))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path, help='MCP 客户端 JSON 配置路径')
    parser.add_argument('--server', default='academic-data', help='mcpServers 中的名称')
    parser.add_argument('--timeout', type=float, default=20, help='连接和能力调用的总时限（秒）')
    args = parser.parse_args()
    if not 0 < args.timeout <= 120:
        parser.error('--timeout 应为 0–120 秒（不含 0）')
    try:
        asyncio.run(verify(args.config, args.server, args.timeout))
        return 0
    except Exception:  # Client stderr already reports startup errors; do not dump env.
        print('连接验证失败。请检查上方服务错误、解释器路径、安装依赖及 ADM_PROVIDERS=whu。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
