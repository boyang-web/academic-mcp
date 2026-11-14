"""数据源注册表。

内置 provider 随包导入自动注册；第三方包可通过 entry point
``academic_data_mcp.providers`` 注册自己的工厂，实现即插即用。
"""
from __future__ import annotations

import importlib
import importlib.metadata
import logging
from dataclasses import dataclass

from .base import ProviderFactory, ProviderInfo

ENTRY_POINT_GROUP = "academic_data_mcp.providers"
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegisteredProvider:
    info: ProviderInfo
    factory: ProviderFactory

    @property
    def name(self) -> str:
        return self.info.name


_REGISTRY: dict[str, RegisteredProvider] = {}
_DISCOVERED = False


def provider(*, name: str, title: str = "", summary: str = "",
             requires_login: bool = True, role: str = "supplementary"):
    """把一个工厂函数注册为数据源。"""

    def decorate(factory: ProviderFactory) -> ProviderFactory:
        info = ProviderInfo(
            name=name.lower(),
            title=title or name,
            summary=summary or title or name,
            requires_login=requires_login,
            role=role,
        )
        _REGISTRY[info.name] = RegisteredProvider(info=info, factory=factory)
        return factory

    return decorate


def discover(force: bool = False) -> dict[str, RegisteredProvider]:
    """加载内置 provider 与已安装的第三方 provider。"""
    global _DISCOVERED
    if _DISCOVERED and not force:
        return dict(_REGISTRY)
    _DISCOVERED = True

    for module in ("academic_data_mcp.gateways.whu",
                   "academic_data_mcp.connectors.cnki",
                   "academic_data_mcp.connectors.csmar"):
        try:
            importlib.import_module(module)
        except Exception:
            _LOGGER.exception("内置数据源 %s 加载失败", module)

    try:
        entries = importlib.metadata.entry_points()
        candidates = (entries.select(group=ENTRY_POINT_GROUP)
                      if hasattr(entries, "select")
                      else entries.get(ENTRY_POINT_GROUP, []))
    except Exception:
        candidates = []
    for entry in candidates:
        if entry.name in _REGISTRY:
            continue
        try:
            loaded = entry.load()
            if isinstance(loaded, RegisteredProvider):
                _REGISTRY[entry.name] = loaded
            elif hasattr(loaded, "info") and hasattr(loaded, "factory"):
                _REGISTRY[entry.name] = loaded
            elif callable(loaded):
                _REGISTRY[entry.name] = RegisteredProvider(
                    info=ProviderInfo(name=entry.name, title=entry.name,
                                      summary=f"外部数据源 {entry.name}"),
                    factory=loaded,
                )
        except Exception:
            _LOGGER.exception("外部数据源 %s 加载失败", entry.name)
    return dict(_REGISTRY)


def get(name: str) -> RegisteredProvider:
    name = "whu" if name.lower() == "luojia" else name.lower()
    entry = discover().get(name)
    if entry is None:
        available = ", ".join(sorted(discover())) or "(无)"
        raise KeyError(f"未知数据源 {name!r}；可用：{available}")
    return entry


def names() -> list[str]:
    return sorted(discover())
