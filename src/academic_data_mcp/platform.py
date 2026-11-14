"""平台差异适配层。

所有与操作系统相关的路径解析、浏览器通道选择、UA 构造、窗口参数都集中在
这里，业务代码只依赖本模块提供的函数，不再出现 sys.platform / winreg / 硬编码
用户目录。新增平台支持只需改这一个文件。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_SLUG = "academic-data-mcp"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def default_downloads_dir() -> Path:
    """返回系统下载目录，Windows 优先读注册表（可能被重定向到其它盘）。"""
    if is_windows():
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(
                    key, "{374DE290-123F-4565-9164-39C4925E467B}")
            folder = Path(os.path.expandvars(value))
            if folder.is_dir():
                return folder
        except Exception:
            pass
    for candidate in (Path.home() / "Downloads", Path.home() / "下载"):
        if candidate.is_dir():
            return candidate
    return Path.home()


def default_data_dir() -> Path:
    """返回应用数据目录（登录态、profile、缓存），遵循各平台惯例。"""
    if is_windows():
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    elif is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / APP_SLUG


def browser_candidates() -> tuple[str, ...]:
    """自动探测浏览器通道的顺序；各平台预装情况不同。"""
    if is_windows():
        return ("msedge", "chrome", "chromium")
    if is_macos():
        return ("chrome", "msedge", "chromium")
    return ("chromium", "chrome", "msedge")


def ua_platform_token() -> str:
    if is_windows():
        return "Windows NT 10.0; Win64; x64"
    if is_macos():
        return "Macintosh; Intel Mac OS X 10_15_7"
    return "X11; Linux x86_64"


def user_agent(major: str | int, channel: str = "") -> str:
    """按真实浏览器版本和当前平台构造 UA，避免固定 Windows 指纹。"""
    token = ua_platform_token()
    ua = (f"Mozilla/5.0 ({token}) AppleWebKit/537.36 "
          f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")
    if channel == "msedge":
        ua += f" Edg/{major}.0.0.0"
    return ua


def offscreen_window_args() -> list[str]:
    """把窗口移出屏幕的参数。

    macOS 的窗口坐标会被系统 clamp，负坐标不可靠；返回空列表由调用方改用
    无头模式处理。
    """
    return [] if is_macos() else ["--window-position=-32000,-32000"]


def capture_headless_default() -> bool:
    """抓取会话头时是否默认无头。macOS 无法可靠隐藏窗口，故默认无头。"""
    return is_macos()
