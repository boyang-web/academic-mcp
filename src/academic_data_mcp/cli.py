"""命令行入口：serve / providers / doctor。

stdio 模式下的 stdout 属于 MCP 协议通道，任何诊断输出都写到 stderr。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from importlib.util import find_spec

from . import platform as plat
from .config import AppConfig
from .registry import discover, get


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="academic-data-mcp",
        description="武大图书馆主入口，知网与国泰安补充直连",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动 MCP 服务器（默认）")
    serve.add_argument("--transport", choices=["stdio", "http"], default=None)
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--data-dir", default=None, help="登录态/profile 根目录")
    serve.add_argument("--downloads-dir", default=None, help="下载文件根目录")
    serve.add_argument("--browser-channel", default=None,
                       help="auto / chrome / msedge / chromium")
    serve.add_argument("--show-browser", action="store_true", default=None)
    serve.add_argument("--providers", default=None,
                       help="逗号分隔，例如 whu,cnki,csmar")

    sub.add_parser("providers", help="列出已注册的数据源")
    sub.add_parser("doctor", help="检查运行环境与配置")
    return parser


def _config_from(args) -> AppConfig:
    overrides = {
        "transport": getattr(args, "transport", None),
        "host": getattr(args, "host", None),
        "port": getattr(args, "port", None),
        "data_dir": getattr(args, "data_dir", None),
        "downloads_dir": getattr(args, "downloads_dir", None),
        "browser_channel": getattr(args, "browser_channel", None),
        "enabled": (tuple(p.strip().lower() for p in args.providers.split(",") if p.strip())
                    if getattr(args, "providers", None) else None),
    }
    if getattr(args, "show_browser", None):
        overrides["headless"] = False
    return AppConfig.from_env(**overrides)


def _doctor(config: AppConfig) -> int:
    report: dict[str, object] = {"config": config.describe()}
    report["dependencies"] = {
        name: find_spec(name) is not None
        for name in ("mcp", "playwright", "httpx", "dotenv", "uvicorn")
    }
    report["provider_options"] = {
        name: config.for_provider(name).redacted() for name in config.enabled
    }
    report["browser_candidates"] = list(plat.browser_candidates())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    missing = [k for k, v in report["dependencies"].items()  # type: ignore[union-attr]
               if not v and k != "uvicorn"]
    return 1 if missing else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = build_parser().parse_args(argv)
    command = args.command or "serve"
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    if command == "providers":
        entries = discover()
        print(json.dumps(
            [{"name": e.name, "title": e.info.title,
              "requires_login": e.info.requires_login, "role": e.info.role, "summary": e.info.summary}
             for e in entries.values()],
            ensure_ascii=False, indent=2))
        return 0

    config = _config_from(args)
    if command == "doctor":
        return _doctor(config)

    for name in config.enabled:
        get(name)  # 提前校验，避免服务启动后才发现未知数据源

    runtimes = [get(name).factory(config) for name in config.enabled]
    from .server import create_server

    server = create_server(config, runtimes)
    if config.transport == "http":
        try:
            import uvicorn
        except ImportError:
            print("HTTP 模式需要安装可选依赖：pip install 'academic-data-mcp[http]'",
                  file=sys.stderr)
            return 2
        uvicorn.run(server.streamable_http_app(), host=config.host, port=config.port)
    else:
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
