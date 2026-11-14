"""不启动浏览器、不联网的冒烟测试：配置、平台适配、注册表、工具挂载。"""
from __future__ import annotations

import pytest

from academic_data_mcp import platform as plat
from academic_data_mcp.config import AppConfig
from academic_data_mcp.registry import discover

BUILTIN = ("whu",)


def test_config_uses_absolute_platform_paths():
    config = AppConfig.from_env()
    assert config.data_dir.is_absolute()
    assert config.downloads_dir.is_absolute()
    assert "lenovo" not in str(config.data_dir).lower()
    assert config.provider_data_dir("cnki") != config.provider_data_dir("csmar")
    assert config.enabled == BUILTIN


def test_platform_user_agent_matches_running_os():
    ua = plat.user_agent(141, "msedge")
    assert "Chrome/141.0.0.0" in ua
    assert "Edg/141.0.0.0" in ua
    if plat.is_macos():
        assert "Macintosh" in ua
        assert plat.capture_headless_default() is True
    elif plat.is_windows():
        assert "Windows NT" in ua
        assert plat.capture_headless_default() is False
    assert len(plat.browser_candidates()) == 3


def test_provider_settings_read_scoped_env(monkeypatch):
    monkeypatch.setenv("ADM_CSMAR_BASE_URL", "https://example.test")
    monkeypatch.setenv("ADM_CSMAR_CAPTURE_HEADLESS", "0")
    settings = AppConfig.from_env().for_provider("csmar")
    assert settings.get("BASE_URL") == "https://example.test"
    assert settings.flag("CAPTURE_HEADLESS", True) is False


def test_registry_contains_all_builtin_providers():
    entries = discover(force=True)
    assert set(BUILTIN) <= set(entries)
    assert entries["whu"].info.requires_login is True
    assert entries["whu"].info.role == "primary"
    assert "cnki" not in entries and "csmar" not in entries


def test_gateway_registers_thirteen_whu_tools():
    pytest.importorskip("mcp")
    from academic_data_mcp.server import create_server

    config = AppConfig.from_env()
    entries = discover()
    runtimes = [entries[name].factory(config) for name in BUILTIN]
    specs = [spec for runtime in runtimes for spec in runtime.tools()]
    names = [spec.name for spec in specs]
    assert len(names) == 13
    assert len(set(names)) == 13
    assert all(name.startswith("whu_") for name in names)
    assert all(spec.description and spec.handler for spec in specs)

    server = create_server(config, runtimes)
    assert server is not None
    for name in BUILTIN:
        assert name in server._mcp_server.instructions
    assert "requires manual login" in server._mcp_server.instructions
