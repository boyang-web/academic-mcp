#!/usr/bin/env python3
"""启动 stdio MCP 服务并验证协议握手 / 工具注册 / 可选的登录链路。

用法：
  python scripts/verify_stdio.py
  python scripts/verify_stdio.py --login csmar --wait 240
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "verify-stdio.log"

EXPECTED_TOOLS = {
    "whu_list_databases", "whu_open_database", "whu_access_plan",
    *{f"{prefix}_{name}" for prefix in ("whu",)
      for name in ("login_status", "open_login_page", "finish_login", "close_browser",
                   "search", "read_page", "download_pdf")},
    "cnki_login_status", "cnki_open_login_page", "cnki_finish_login",
    "cnki_close_browser", "cnki_search", "cnki_download_pdf",
    "csmar_login_status", "csmar_open_login_page", "csmar_finish_login",
    "csmar_close_browser", "csmar_list_series", "csmar_list_databases",
    "csmar_list_tables", "csmar_get_table_fields",     "csmar_preview",
    "csmar_download",
}


class StdioClient:
    def __init__(self, args: argparse.Namespace) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        if args.providers:
            env["ADM_PROVIDERS"] = args.providers
        if args.browser_channel:
            env["ADM_BROWSER_CHANNEL"] = args.browser_channel
        if args.show_browser:
            env["ADM_SHOW_BROWSER"] = "1"
        self.log = LOG.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "academic_data_mcp", "serve"],
            cwd=str(ROOT), env=env, text=True, encoding="utf-8", bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log,
        )
        self._q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self._id = 0

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._q.put(line)

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None,
                timeout: float = 60) -> dict:
        request_id = self._next_id()
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method,
                    "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{method} 超时")
            try:
                line = self._q.get(timeout=remaining)
            except queue.Empty as error:
                raise TimeoutError(f"{method} 超时") from error
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method} 返回错误：{message['error']}")
                return message.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call_tool(self, name: str, arguments: dict | None = None,
                  timeout: float = 180) -> dict:
        return self.request("tools/call",
                            {"name": name, "arguments": arguments or {}},
                            timeout=timeout)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()


def _text(result: dict) -> str:
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item.get("text", "")
    return json.dumps(result, ensure_ascii=False)


def _parse(result: dict):
    try:
        return json.loads(_text(result))
    except (ValueError, TypeError):
        return _text(result)


def handshake(client: StdioClient) -> dict:
    last_error: Exception | None = None
    for version in ("2025-06-18", "2025-03-26", "2024-11-05"):
        try:
            result = client.request("initialize", {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "verify-stdio", "version": "0.3.0"},
            })
            client.notify("notifications/initialized")
            return result
        except Exception as error:  # noqa: BLE001
            last_error = error
    raise RuntimeError(f"MCP 握手失败：{last_error}")


def login_flow(client: StdioClient, provider: str, wait: int) -> None:
    prefix = provider.lower()
    opened = _parse(client.call_tool(f"{prefix}_open_login_page"))
    print(json.dumps({"step": "open_login_page", "result": opened},
                     ensure_ascii=False, indent=2), flush=True)
    print(f"\n>>> 浏览器窗口已弹出，请在窗口中完成登录；脚本最多等待 {wait} 秒 <<<\n",
          flush=True)
    deadline = time.monotonic() + wait
    finished = None
    while time.monotonic() < deadline:
        finished = _parse(client.call_tool(f"{prefix}_finish_login"))
        if isinstance(finished, dict) and finished.get("success"):
            break
        time.sleep(8)
    print(json.dumps({"step": "finish_login", "result": finished},
                     ensure_ascii=False, indent=2), flush=True)
    status = _parse(client.call_tool(f"{prefix}_login_status"))
    print(json.dumps({"step": "login_status", "result": status},
                     ensure_ascii=False, indent=2), flush=True)
    probe = ("csmar_list_series" if prefix == "csmar" else "cnki_login_status")
    data = _parse(client.call_tool(probe))
    print(json.dumps({"step": "data_probe", "tool": probe, "result": data},
                     ensure_ascii=False, indent=2)[:2500], flush=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="验证 academic-data-mcp 的 stdio 链路")
    parser.add_argument("--providers", default="", help="逗号分隔，默认全部")
    parser.add_argument("--browser-channel", default="", help="覆盖浏览器通道")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--login", default="", help="执行登录验证：cnki 或 csmar")
    parser.add_argument("--wait", type=int, default=240, help="登录等待秒数")
    args = parser.parse_args()

    client = StdioClient(args)
    try:
        init = handshake(client)
        print("=== MCP 握手 ===", flush=True)
        print(json.dumps({
            "protocolVersion": init.get("protocolVersion"),
            "serverInfo": init.get("serverInfo"),
            "capabilities": init.get("capabilities"),
        }, ensure_ascii=False, indent=2), flush=True)

        tools = client.request("tools/list").get("tools", [])
        names = [tool["name"] for tool in tools]
        print("\n=== 工具注册 ===", flush=True)
        print(json.dumps({
            "count": len(names),
            "by_prefix": {
                prefix: [n for n in names if n.startswith(prefix + "_")]
                for prefix in sorted({n.split("_")[0] for n in names})
            },
            "missing": sorted(EXPECTED_TOOLS - set(names)),
        }, ensure_ascii=False, indent=2), flush=True)

        if args.login:
            print("\n=== 登录链路 ===", flush=True)
            login_flow(client, args.login, args.wait)
        missing = EXPECTED_TOOLS - set(names)
        return 0 if not missing else 1
    except Exception as error:  # noqa: BLE001
        print(f"验证失败：{type(error).__name__}: {error}", file=sys.stderr)
        print(f"服务日志：{LOG}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
