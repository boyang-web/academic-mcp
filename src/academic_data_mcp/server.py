"""MCP 服务器组装：按注册表动态挂载所有数据源的工具。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .base import ProviderRuntime
from .config import AppConfig

SERVER_NAME = "academic-data"
_LOGGER = logging.getLogger(__name__)

GENERAL_INSTRUCTIONS = (
    "Use Wuhan University Library (whu_*) as the PRIMARY academic literature gateway: "
    "whu_search -> the result's preferred_url/access_routes -> whu_read_page -> "
    "a returned PDF URL -> whu_download_pdf. whu_list_databases and "
    "whu_open_database provide the library's actual database entries. Preserve "
    "institution resolver/proxy links. A catalogue label is not proof of fulltext permission. "
    "cnki_* and csmar_* are SUPPLEMENTARY direct connectors, selected explicitly "
    "when library access is unsuitable or specialist database operations are needed; "
    "their authentication is separate. Never automatically trigger quota-consuming exports. "
    "For manual authentication/verification use the provider's visible browser, then "
    "finish_login and retry the original operation. Network errors, timeouts and CAPTCHA "
    "are failures, never zero hits. Never retry indefinitely or invent data."
)


def build_instructions(runtimes: list[ProviderRuntime]) -> str:
    blocks = [GENERAL_INSTRUCTIONS]
    for runtime in runtimes:
        info = runtime.info
        login = "requires manual login" if info.requires_login else "no login required"
        blocks.append(f"{info.title} ({info.name}, role={info.role}, {login}): {info.summary}")
    return "\n\n".join(blocks)


def create_server(config: AppConfig, runtimes: list[ProviderRuntime]) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_):
        try:
            yield {}
        finally:
            for runtime in reversed(runtimes):
                try:
                    await runtime.aclose()
                except Exception:
                    _LOGGER.exception("关闭数据源 %s 失败", runtime.info.name)

    mcp = FastMCP(
        SERVER_NAME,
        host=config.host,
        port=config.port,
        lifespan=lifespan,
        instructions=build_instructions(runtimes),
    )

    seen: set[str] = set()
    for runtime in runtimes:
        for spec in runtime.tools():
            if spec.name in seen:
                raise ValueError(f"工具名冲突：{spec.name}")
            seen.add(spec.name)
            mcp.tool(name=spec.name, description=spec.description)(spec.handler)
    return mcp
