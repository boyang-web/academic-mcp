"""数据源插件契约。

新增一个数据库时，实现 ProviderRuntime（提供工具列表 + 生命周期），写一个
工厂函数并用 @provider(...) 注册即可；server / cli / config 都不需要改动。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from .config import AppConfig

Handler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Handler


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    title: str
    summary: str
    requires_login: bool = True
    role: str = "supplementary"


@runtime_checkable
class ProviderRuntime(Protocol):
    """一个已实例化的数据源，负责暴露工具与释放资源。"""

    @property
    def info(self) -> ProviderInfo: ...

    def tools(self) -> list[ToolSpec]: ...

    async def aclose(self) -> None: ...


ProviderFactory = Callable[[AppConfig], ProviderRuntime]
