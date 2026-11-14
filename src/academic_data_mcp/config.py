"""统一配置。

优先级：CLI 参数 > 环境变量 > 平台默认值。代码里没有任何写死的用户路径。
环境变量统一前缀 ADM_；单个数据源的专属配置使用 ADM_<PROVIDER>_<KEY>，
因此新增数据源无需修改配置层。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import platform as plat

ENV_PREFIX = "ADM_"
DEFAULT_PROVIDERS = ("whu",)

_TRUE = {"1", "true", "yes", "on"}


def _flag(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in _TRUE


def _env(key: str, default: str = "") -> str:
    return os.getenv(ENV_PREFIX + key, default)


@dataclass(frozen=True)
class ProviderSettings:
    """某个数据源的专属设置，键名来自 ADM_<NAME>_<KEY> 环境变量。"""

    name: str
    options: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        return self.options.get(key.upper(), default)

    def redacted(self) -> dict[str, str]:
        from urllib.parse import urlsplit, urlunsplit
        safe = {}
        for key, value in self.options.items():
            if any(part in key.upper() for part in ('TOKEN', 'PASSWORD', 'SECRET', 'COOKIE', 'API_KEY', 'AUTHORIZATION')):
                safe[key] = '***' if value else ''
            elif key.upper().endswith('PROXY'):
                parsed = urlsplit(value)
                safe[key] = urlunsplit((parsed.scheme, '***@' + parsed.netloc.rsplit('@', 1)[-1], parsed.path, '', '')) if parsed.username else value
            else:
                safe[key] = value
        return safe

    def flag(self, key: str, default: bool = False) -> bool:
        return _flag(self.options.get(key.upper()), default)

    def integer(self, key: str, default: int) -> int:
        raw = self.options.get(key.upper())
        try:
            return int(raw) if raw else default
        except ValueError:
            return default


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    downloads_dir: Path
    browser_channel: str
    headless: bool
    transport: str
    host: str
    port: int
    enabled: tuple[str, ...]

    @classmethod
    def from_env(cls, **overrides) -> "AppConfig":
        """读取环境变量；overrides 中 None 表示「未指定」，保留环境值。"""
        values: dict[str, object] = {
            "data_dir": Path(_env("DATA_DIR", str(plat.default_data_dir()))).expanduser(),
            "downloads_dir": Path(
                _env("DOWNLOADS_DIR", str(plat.default_downloads_dir()))).expanduser(),
            "browser_channel": _env("BROWSER_CHANNEL", "auto"),
            "headless": not _flag(_env("SHOW_BROWSER"), False),
            "transport": _env("TRANSPORT", "stdio"),
            "host": _env("HOST", "127.0.0.1"),
            "port": int(_env("PORT", "7791") or "7791"),
            "enabled": tuple(
                p.strip().lower()
                for p in _env("PROVIDERS", ",".join(DEFAULT_PROVIDERS)).split(",")
                if p.strip()
            ),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        enabled = tuple(dict.fromkeys("whu" if str(v).lower() in {"luojia", "cnki", "csmar"} else str(v).lower() for v in values["enabled"]))  # type: ignore[union-attr]
        return cls(
            data_dir=Path(str(values["data_dir"])),
            downloads_dir=Path(str(values["downloads_dir"])),
            browser_channel=str(values["browser_channel"]),
            headless=bool(values["headless"]),
            transport=str(values["transport"]),
            host=str(values["host"]),
            port=int(values["port"]),
            enabled=enabled,
        )

    def provider_data_dir(self, name: str) -> Path:
        path = self.data_dir / name
        # Keep existing manually authenticated sessions after the 0.3 rename.
        if name == 'whu' and not path.exists() and (self.data_dir / 'luojia').exists():
            return self.data_dir / 'luojia'
        return path

    def provider_output_dir(self, name: str) -> Path:
        return self.downloads_dir / plat.APP_SLUG / name

    def for_provider(self, name: str) -> ProviderSettings:
        prefix = f"{ENV_PREFIX}{name.upper().replace('-', '_')}_"
        options = {
            key[len(prefix):]: value
            for key, value in os.environ.items()
            if key.startswith(prefix)
        }
        if name.lower() == 'whu':
            legacy = self.for_provider('luojia').options
            options = {**legacy, **options}
        return ProviderSettings(name=name.lower(), options=options)

    def describe(self) -> dict[str, str]:
        return {
            "data_dir": str(self.data_dir),
            "downloads_dir": str(self.downloads_dir),
            "browser_channel": self.browser_channel,
            "browser_candidates": ",".join(plat.browser_candidates()),
            "platform": "windows" if plat.is_windows() else "macos" if plat.is_macos() else "linux",
            "headless": str(self.headless),
            "transport": self.transport,
            "host": self.host,
            "port": str(self.port),
            "providers": ",".join(self.enabled),
        }
