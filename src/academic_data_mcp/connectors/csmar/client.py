"""CSMAR (data.csmar.com) client for the unified academic-data MCP.

Session model
-------------
CSMAR's SPA sends session material in per-request headers (``Token``,
``signCode``, ``Lang``, ``belong``) that are derived in page memory, so copying
cookies alone is not enough. A short-lived headful (offscreen) Playwright launch
observes one real API request, captures those headers plus cookies, and closes
again; subsequent API calls run over httpx. The captured material is kept in
memory only and is never logged.

Download flow (verified 2026-10-26)
-----------------------------------
POST /api/csmar-main/single/saveOutline -> outlineId
POST /api/csmar-main/singleData/pack     -> recordId (small exports finish here)
GET  /api/csmar-main/userCenter/recordDownload/{recordId} -> filePath/specFileName
GET  https://file.csmar.com/{filePath}?attname=...      -> zip
Large exports may return no recordId; progress then arrives over
``wss://data.csmar.com/ws`` and is handled through a hidden browser page.
"""
from __future__ import annotations

import asyncio
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from playwright.async_api import async_playwright

from academic_data_mcp import platform as plat

# 站点地址可通过 ADM_CSMAR_BASE_URL / ADM_CSMAR_FILE_BASE 覆盖（代理、镜像或测试）。
DEFAULT_BASE = "https://data.csmar.com"
DEFAULT_FILE_BASE = "https://file.csmar.com"
DEFAULT_UA = plat.user_agent("141")
SESSION_TTL = 25 * 60
CACHE_TTL = 3600

# Values from the fileTypes list in the CSMAR single-table page.
FORMATS = {
    "xlsx": "2", "excel2007": "2",
    "xls": "6", "excel2003": "6",
    "dta": "19", "stata": "19",
    "csv": "4",
    "txt": "0",
    "json": "20",
    "sav": "13",
    "xml": "14",
}

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class CsmarError(RuntimeError):
    """A CSMAR request failed for a reason worth showing the user."""


class SessionExpired(CsmarError):
    """The captured CSMAR session was rejected; refresh it."""


class LoginRequired(CsmarError):
    """No valid CSMAR login is available; the user must log in."""


def default_output_dir() -> Path:
    # 未显式配置时的导出目录；正常路径由 AppConfig 统一提供。
    return plat.default_downloads_dir() / plat.APP_SLUG / "csmar"


def _safe_name(name: str, fallback: str = "csmar_data") -> str:
    name = _INVALID_NAME.sub("_", (name or "").strip()).strip(" .")
    return name or fallback


def _norm_codes(codes) -> str:
    if not codes:
        return ""
    if isinstance(codes, (list, tuple)):
        tokens = [str(c) for c in codes]
    else:
        tokens = re.split(r"[,;\s]+", str(codes))
    return ",".join(t.strip() for t in tokens if t.strip())


def _norm_date(value, fallback: str) -> str:
    value = (value or "").strip()
    if not value:
        return fallback or ""
    if not re.fullmatch(r"\d{4}(?:-\d{1,2})?(?:-\d{1,2})?", value):
        raise CsmarError("日期格式应为 YYYY、YYYY-MM 或 YYYY-MM-DD。")
    return value


class CsmarService:
    def __init__(self, data_dir, output_dir=None, channel="auto", *,
                 base_url=DEFAULT_BASE, file_base=DEFAULT_FILE_BASE,
                 user_agent=None, capture_headless=None,
                 session_ttl=SESSION_TTL):
        self.data = Path(data_dir).resolve()
        self.profile = self.data / "profile"
        self.state_file = self.data / "csmar-storage.json"
        self.output = (Path(output_dir).expanduser().resolve()
                       if output_dir else default_output_dir())
        self.channel = channel or "auto"
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self.file_base = (file_base or DEFAULT_FILE_BASE).rstrip("/")
        self.ws_url = (self.base_url.replace("https://", "wss://")
                       .replace("http://", "ws://") + "/ws")
        self.user_agent = user_agent or DEFAULT_UA
        self.capture_headless = (plat.capture_headless_default()
                                 if capture_headless is None else capture_headless)
        self.session_ttl = session_ttl
        self._pw = None
        self._session = None
        self._login_ctx = None
        self._lock = asyncio.Lock()
        self._init_cache = {}
        self._tree_cache = {}
        self._fields_cache = {}

    # ---------- browser helpers ----------

    async def _start_pw(self):
        if self._pw is None:
            self._pw = await async_playwright().start()
        return self._pw

    async def _stop_pw_if_idle(self):
        if self._pw is not None and self._login_ctx is None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def _clear_singleton_locks(self):
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            path = self.profile / name
            try:
                if path.is_symlink() or path.exists():
                    path.unlink()
            except Exception:
                pass

    async def _launch(self, visible: bool, offscreen: bool = False):
        self._clear_singleton_locks()
        args = ["--disable-blink-features=AutomationControlled", "--no-first-run",
                "--ignore-certificate-errors"]
        if offscreen:
            args.extend(plat.offscreen_window_args())
        # macOS 无法可靠把窗口移出屏幕，抓取会话头时改用无头模式；
        # 用户主动调用 open_login（visible=True, offscreen=False）时仍弹出可见窗口。
        headless = (not visible) or (offscreen and self.capture_headless)
        common = dict(
            headless=headless,
            accept_downloads=True,
            ignore_https_errors=True,
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
            args=args,
        )
        channels = ([self.channel] if self.channel not in ("", "auto")
                    else list(plat.browser_candidates()))
        last_error: Exception | None = None
        for channel in (*channels, ""):  # 最后回退到 Playwright 自带内核
            kwargs = dict(common)
            if channel:
                kwargs["channel"] = channel
            try:
                return await self._pw.chromium.launch_persistent_context(
                    str(self.profile), **kwargs)
            except Exception as error:
                last_error = error
        raise CsmarError(
            f"无法启动浏览器（已尝试 {', '.join(channels) or 'bundled'}）："
            f"{type(last_error).__name__}: {last_error}")

    async def aclose(self) -> None:
        # 释放浏览器资源，由服务器 lifespan 统一调用。
        if self._login_ctx is not None:
            try:
                await self._login_ctx.close()
            except Exception:
                pass
            self._login_ctx = None
        await self._stop_pw_if_idle()

    @staticmethod
    def _iframe(page):
        return next((f for f in page.frames if "csmar.html" in (f.url or "")), page.main_frame)

    # ---------- session ----------

    async def _capture_session_locked(self) -> None:
        await self._start_pw()
        ctx = await self._launch(visible=True, offscreen=True)
        captured: dict[str, str] = {}
        ready = asyncio.Event()

        def on_request(request):
            if "/api/" in request.url and not captured.get("token"):
                headers = {k.lower(): v for k, v in request.headers.items()}
                if headers.get("token"):
                    captured.update(headers)
                    ready.set()

        ctx.on("request", on_request)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(f"{self.base_url}/csmar.html#/index",
                            wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(4000)
            if not captured.get("token"):
                try:
                    if await self._login_button_visible(self._iframe(page)):
                        raise LoginRequired(
                            "CSMAR 未登录。请调用 csmar_open_login_page 在浏览器中登录，"
                            "然后调用 csmar_finish_login。")
                except LoginRequired:
                    raise
                except Exception:
                    pass
            try:
                await asyncio.wait_for(ready.wait(), timeout=20)
            except asyncio.TimeoutError:
                pass
            if not captured.get("token"):
                raise LoginRequired(
                    "未检测到有效的 CSMAR 登录会话。请调用 csmar_open_login_page "
                    "在浏览器中登录，然后调用 csmar_finish_login。")
            cookies = {c["name"]: c["value"] for c in await ctx.cookies()
                       if "csmar" in c.get("domain", "")}
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": f"{self.base_url}/csmar.html",
                "Origin": self.base_url,
                "Lang": captured.get("lang") or "zh",
                "belong": captured.get("belong") or "0",
                "Token": captured.get("token", ""),
                "signCode": captured.get("signcode", ""),
            }
            self._session = {"cookies": cookies, "headers": headers, "ts": time.time()}
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
            await self._stop_pw_if_idle()

    async def _ensure_session_locked(self) -> None:
        if self._login_ctx is not None:
            raise LoginRequired(
                "登录窗口已打开：请在窗口中完成登录后调用 csmar_finish_login。")
        if self._session and time.time() - self._session["ts"] < self.session_ttl:
            return
        await self._capture_session_locked()
        try:
            logged = await self._raw_api("GET", "/api/csmar-main/isLoggedIn")
        except SessionExpired as error:
            self._session = None
            raise LoginRequired(
                "CSMAR 会话已失效，请调用 csmar_open_login_page 重新登录。") from error
        if str(logged) != "1":
            self._session = None
            raise LoginRequired(
                "CSMAR 未登录（会话检查返回未登录）。请调用 csmar_open_login_page 登录。")

    async def _ensure_session(self) -> None:
        async with self._lock:
            await self._ensure_session_locked()

    async def _raw_api(self, method: str, path: str, params=None, json=None,
                       timeout: float = 120):
        if not self._session:
            raise SessionExpired("CSMAR session is not initialised.")
        url = path if path.startswith("http") else self.base_url + path
        query = {"t": int(time.time() * 1000)}
        if params:
            query.update(params)
        async with httpx.AsyncClient(
                cookies=self._session["cookies"],
                headers=self._session["headers"],
                timeout=timeout, follow_redirects=True) as client:
            response = await client.request(method, url, params=query, json=json)
        if response.status_code in (401, 403):
            raise SessionExpired("CSMAR session expired.")
        try:
            payload = response.json()
        except Exception as error:
            raise CsmarError(
                f"CSMAR 返回了非 JSON 响应（HTTP {response.status_code}）。") from error
        if payload.get("code") == -3004:
            raise SessionExpired("CSMAR session expired.")
        if response.status_code != 200 or payload.get("code") != 0:
            message = payload.get("msg") or f"HTTP {response.status_code}"
            raise CsmarError(f"CSMAR 请求失败：{message}")
        return payload.get("data")

    async def _api(self, method: str, path: str, **kwargs):
        await self._ensure_session()
        try:
            return await self._raw_api(method, path, **kwargs)
        except SessionExpired:
            async with self._lock:
                self._session = None
                await self._ensure_session_locked()
            return await self._raw_api(method, path, **kwargs)

    # ---------- login ----------

    async def status(self) -> dict:
        try:
            await self._ensure_session()
            ip = ""
            try:
                ip = await self._api("GET", "/api/csmar-main/getUserIp")
            except CsmarError:
                pass
            return {"success": True, "logged_in": True, "ip": ip,
                    "detail": "CSMAR 会话有效。"}
        except LoginRequired as error:
            return {"success": True, "logged_in": False, "detail": str(error)}
        except CsmarError as error:
            return {"success": False, "logged_in": False, "message": str(error)}

    async def open_login(self) -> dict:
        async with self._lock:
            if self._login_ctx is not None:
                return {"success": True, "opened": True, "already_open": True,
                        "next_step": "请在已打开的浏览器窗口中完成登录，然后调用 csmar_finish_login。"}
            await self._start_pw()
            try:
                ctx = await self._launch(visible=True, offscreen=False)
            except Exception as error:
                raise CsmarError(f"无法启动登录浏览器：{type(error).__name__}: {error}")
            self._login_ctx = ctx
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(f"{self.base_url}/", wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(5000)
                frame = self._iframe(page)
                try:
                    await frame.get_by_text("登录", exact=True).first.click(timeout=8000)
                except Exception:
                    pass
            except Exception as error:
                try:
                    await ctx.close()
                finally:
                    self._login_ctx = None
                    await self._stop_pw_if_idle()
                raise CsmarError(f"打开 CSMAR 登录页失败：{type(error).__name__}: {error}")
            return {"success": True, "opened": True,
                    "next_step": "请在浏览器窗口中完成账号或扫码登录，然后调用 csmar_finish_login。"}

    async def _login_button_visible(self, frame) -> bool:
        try:
            locator = frame.locator(".login-btn")
            if await locator.count():
                return await locator.first.is_visible()
        except Exception:
            pass
        return False

    async def finish_login(self) -> dict:
        async with self._lock:
            ctx = self._login_ctx
            if ctx is None:
                return {"success": False,
                        "message": "当前没有打开的登录窗口。请先调用 csmar_open_login_page。"}
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            frame = self._iframe(page)
            logged = not await self._login_button_visible(frame)
            if not logged:
                await asyncio.sleep(2)
                frame = self._iframe(page)
                logged = not await self._login_button_visible(frame)
            if not logged:
                return {"success": False,
                        "message": "尚未检测到登录完成：请在浏览器窗口中完成账号或扫码登录后再试。"}
            try:
                await ctx.storage_state(path=str(self.state_file))
            except Exception:
                pass
            await ctx.close()
            self._login_ctx = None
            self._session = None
            await self._stop_pw_if_idle()
            return {"success": True, "message": "CSMAR 登录成功，登录态已保存在本地。"}

    async def close_browser(self) -> dict:
        async with self._lock:
            ctx = self._login_ctx
            if ctx is not None:
                try:
                    await ctx.storage_state(path=str(self.state_file))
                except Exception:
                    pass
                try:
                    await ctx.close()
                except Exception:
                    pass
            self._login_ctx = None
            await self._stop_pw_if_idle()
            return {"success": True, "closed": True,
                    "message": "登录浏览器已关闭，已保存登录态。"}

    # ---------- catalog ----------

    async def list_series(self) -> dict:
        data = await self._api("GET", "/api/csmar-main/single/getSeriesTree/-1")
        series = [{"id": str(x.get("id")), "name": x.get("name")}
                  for x in (data or {}).get("seriesTrees", [])]
        hot = [{"id": str(x.get("id")), "name": x.get("databaseName"),
                "series_id": x.get("seriesId"), "useable": x.get("useable")}
               for x in ((data or {}).get("typeDatabaseVo") or {}).get("databaseList", [])]
        return {"success": True, "series": series, "hot_databases": hot}

    async def list_databases(self, series_id) -> dict:
        series_id = str(series_id).strip()
        data = await self._api(
            "GET", f"/api/csmar-main/single/getDatabaseList/{series_id}/-1")
        databases = [{"id": str(x.get("id")), "name": x.get("databaseName"),
                      "type": x.get("databaseType"), "useable": x.get("useable")}
                     for x in (data or {}).get("databaseList", [])]
        return {"success": True, "series_id": series_id, "databases": databases}

    async def _db_tree(self, database_id) -> dict:
        key = str(database_id)
        cached = self._tree_cache.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return cached[1]
        data = await self._api(
            "GET", f"/api/csmar-main/single/getSingleTableLeftTree/{key}")
        self._tree_cache[key] = (time.time(), data or {})
        return data or {}

    async def list_tables(self, database_id) -> dict:
        data = await self._db_tree(database_id)
        tables = []
        for node in data.get("tableTrees") or []:
            for table in node.get("children") or []:
                raw_id = str(table.get("id", ""))
                tables.append({
                    "table_id": raw_id.replace("table-", ""),
                    "name": table.get("name"),
                    "category": node.get("name"),
                    "mark_id": table.get("markId"),
                    "auth_pay_type": table.get("authPayType"),
                    "is_new": table.get("isNewTable"),
                })
        init = data.get("singleDataInitVo") or {}
        default = None
        if init.get("id") is not None:
            default = {"table_id": str(init.get("id")), "name": init.get("tableName"),
                       "start_date": init.get("startDate"), "end_date": init.get("endDate"),
                       "data_count": init.get("dataCount")}
        return {"success": True, "database_id": str(database_id),
                "series_name": data.get("seriesName"),
                "database_name": data.get("databaseName"),
                "default_table": default, "tables": tables}

    async def _table_init(self, table_id) -> dict:
        key = str(table_id).replace("table-", "")
        cached = self._init_cache.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return cached[1]
        data = await self._api(
            "GET", f"/api/csmar-main/single/getSingleTableInit/{key}")
        if not isinstance(data, dict) or data.get("id") is None:
            raise CsmarError(f"未找到表 ID {table_id} 的元数据；请先通过 csmar_list_tables 获取表 ID。")
        self._init_cache[key] = (time.time(), data)
        return data

    async def _table_context(self, table_id):
        init = await self._table_init(table_id)
        database_id = init.get("dataBaseId")
        series_name = ""
        try:
            tree = await self._db_tree(database_id)
            series_name = tree.get("seriesName") or ""
        except CsmarError:
            pass
        return init, series_name

    async def _field_pool(self, init: dict) -> list[dict]:
        """Full field list. Table init omits the code/time fields, so prefer
        getSampleData and merge back codeFieldVo/timeFieldVo."""
        key = str(init.get("id"))
        cached = self._fields_cache.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return cached[1]
        fields = []
        try:
            sample = await self._api(
                "GET", f"/api/csmar-main/single/getSampleData/{key}")
            fields = (sample or {}).get("fieldInfoVos") or []
        except CsmarError:
            fields = []
        if not fields:
            fields = list(init.get("fieldInfos") or [])
        for extra in (init.get("codeFieldVo"), init.get("timeFieldVo")):
            if extra and all(str(f.get("id")) != str(extra.get("id")) for f in fields):
                fields.insert(0, dict(extra))
        self._fields_cache[key] = (time.time(), fields)
        return fields

    async def get_fields(self, table_id, keyword: str = "", limit: int = 300) -> dict:
        init = await self._table_init(table_id)
        fields = await self._field_pool(init)
        default_ids = {str(f.get("id")) for f in (init.get("defaultFieldInfos") or [])}
        keyword = (keyword or "").strip().lower()
        rows = []
        for field in fields:
            title = field.get("fieldTitle") or ""
            name = field.get("field") or ""
            explain = field.get("fieldExplain") or ""
            if keyword and not any(keyword in str(x).lower()
                                   for x in (title, name, explain)):
                continue
            rows.append({
                "id": field.get("id"),
                "name": name,
                "title": title,
                "type": field.get("fieldType"),
                "unit": field.get("priceUnit"),
                "explain": explain,
                "default_selected": bool(
                    field.get("isChecked") == "1"
                    or str(field.get("id")) in default_ids),
                "is_time_field": field.get("fieldSign") == "1",
                "is_code_field": field.get("fieldSign") == "2",
            })
        total = len(rows)
        limit = max(1, min(int(limit or 300), 1000))
        return {"success": True, "table_id": str(init.get("id")),
                "table_name": init.get("tableName"), "total": total,
                "returned": min(total, limit), "fields": rows[:limit],
                "start_date": init.get("startDate"), "end_date": init.get("endDate"),
                "data_count": init.get("dataCount")}

    async def _resolve_fields(self, init: dict, requested) -> list[dict]:
        pool = await self._field_pool(init)
        if not requested:
            selected = init.get("defaultFieldInfos") or [
                f for f in pool if f.get("isChecked") == "1"]
            if not selected:
                selected = pool[:2]
            return [self._as_selected(f) for f in selected]
        if isinstance(requested, str):
            tokens = [t for t in re.split(r"[,;\s]+", requested) if t]
        else:
            tokens = [str(t) for t in requested if str(t).strip()]
        selected, missing = [], []
        for token in tokens:
            token = token.strip()
            found = None
            if token.isdigit():
                found = next((f for f in pool if str(f.get("id")) == token), None)
            if found is None:
                found = next((f for f in pool
                              if str(f.get("field", "")).lower() == token.lower()), None)
            if found is None:
                found = next((f for f in pool
                              if str(f.get("fieldTitle", "")) == token), None)
            if found is None:
                candidates = [f for f in pool
                              if token.lower() in str(f.get("fieldTitle", "")).lower()
                              or token.lower() in str(f.get("field", "")).lower()]
                if len(candidates) == 1:
                    found = candidates[0]
                elif len(candidates) > 1:
                    titles = "、".join(str(c.get("fieldTitle")) for c in candidates[:6])
                    missing.append(f"{token}（匹配到多个字段：{titles}）")
                    continue
            if found is None:
                missing.append(token)
                continue
            if all(str(s.get("id")) != str(found.get("id")) for s in selected):
                selected.append(found)
        if missing:
            raise CsmarError(
                "无法唯一识别以下字段：" + "；".join(missing)
                + "。请先调用 csmar_get_table_fields 查看准确的字段名或中文标题。")
        if not selected:
            raise CsmarError("未选中任何字段。")
        return [self._as_selected(f) for f in selected]

    @staticmethod
    def _as_selected(field: dict) -> dict:
        item = dict(field)
        item["isChecked"] = "1"
        return item

    def _query_payload(self, init, selected, start, end, codes, fmt_code, series_name):
        code_field = init.get("codeFieldVo") or {}
        time_field = init.get("timeFieldVo") or {}
        has_time = bool(time_field)
        return {
            "codeStr": codes,
            "codeId": "",
            "fileOutType": fmt_code,
            "tableName": init.get("tableName"),
            "tableNamePhy": init.get("tableNamePhy"),
            "codeSetField": code_field.get("field", ""),
            "timeSetField": time_field.get("field", "") if has_time else "",
            "databaseName": init.get("databaseName", ""),
            "id": init.get("id"),
            "startDate": start if has_time else "",
            "endDate": end if has_time else "",
            "fieldInfoDtos": selected,
            "filterConditionDtos": [],
            "seriesName": series_name or "",
            "databaseId": init.get("dataBaseId"),
            "frequency": str(init.get("frequency", "0")),
            "showCount": "1",
        }

    async def preview(self, table_id, fields=None, start_date: str = "",
                      end_date: str = "", codes: str = "") -> dict:
        init, series_name = await self._table_context(table_id)
        selected = await self._resolve_fields(init, fields)
        start = _norm_date(start_date, init.get("startDate") or "")
        end = _norm_date(end_date, init.get("endDate") or "")
        payload = self._query_payload(init, selected, start, end,
                                      _norm_codes(codes), "2", series_name)
        token = await self._api(
            "POST", "/api/csmar-single/single/cacheCondition", json=payload)
        if not token:
            raise CsmarError("CSMAR 未返回预览令牌。")
        data = await self._api(
            "GET", f"/api/csmar-single/single/preview/{token}") or {}
        rows = data.get("previewDatas") or []
        for row in rows:
            row.pop("updateid", None)
        columns = [{"name": f.get("field"), "title": f.get("fieldTitle"),
                    "type": f.get("fieldType"), "unit": f.get("priceUnit")}
                   for f in (data.get("fieldInfoVos") or [])]
        return {
            "success": True,
            "table_id": str(init.get("id")),
            "table_name": init.get("tableName"),
            "start_date": data.get("startDate") or start,
            "end_date": data.get("endDate") or end,
            "total_rows": data.get("dataCount"),
            "preview_rows": data.get("previewCount") or len(rows),
            "columns": columns,
            "rows": rows,
            "note": "预览由 CSMAR 限制为最多 100 行；下载完整数据请调用 csmar_download。",
        }

    # ---------- download ----------

    async def download(self, table_id, fields=None, start_date: str = "",
                       end_date: str = "", codes: str = "",
                       file_format: str = "xlsx", output_dir: str = "",
                       timeout_seconds: int = 600) -> dict:
        fmt_key = (file_format or "xlsx").strip().lower()
        fmt_code = FORMATS.get(fmt_key)
        if not fmt_code:
            raise CsmarError(
                "不支持的文件格式：" + fmt_key
                + "。可用格式：xlsx、xls、dta、csv、txt、json、sav、xml。")
        init, series_name = await self._table_context(table_id)
        selected = await self._resolve_fields(init, fields)
        start = _norm_date(start_date, init.get("startDate") or "")
        end = _norm_date(end_date, init.get("endDate") or "")
        codes = _norm_codes(codes)
        payload = self._query_payload(init, selected, start, end,
                                      codes, fmt_code, series_name)
        payload.update({
            "fieldStr": ",".join(str(f.get("id")) for f in selected),
            "planName": "",
            "mail": "",
            "conditionDtos": [],
            "startTime": start,
            "endTime": end,
            "estimateTime": int(init.get("estimateTime") or 1),
            "tableId": init.get("id"),
            "downloadType": "0",
        })
        outline_id = await self._api(
            "POST", "/api/csmar-main/single/saveOutline", json=payload)
        if not outline_id:
            raise CsmarError("CSMAR 未返回下载任务编号。")
        code_field = (init.get("codeFieldVo") or {}).get("field", "")
        time_field = (init.get("timeFieldVo") or {}).get("field", "")
        pack_payload = {
            "codeSetField": code_field,
            "mail": "",
            "outlineId": str(outline_id),
            "timeSetField": time_field,
            "planName": "",
        }
        record_id = await self._api(
            "POST", "/api/csmar-main/singleData/pack", json=pack_payload)
        record = None
        if record_id:
            for _ in range(3):
                try:
                    record = await self._api(
                        "GET",
                        f"/api/csmar-main/userCenter/recordDownload/{record_id}")
                    if record and record.get("filePath"):
                        break
                except CsmarError:
                    pass
                await asyncio.sleep(3)
        if not (record and record.get("filePath")):
            record = await self._ws_wait(str(outline_id), timeout_seconds)
        if not record or not record.get("filePath"):
            raise CsmarError("CSMAR 未返回下载文件路径。")

        spec_name = _safe_name(
            record.get("specFileName")
            or f"{init.get('tableName')}_{datetime.now():%Y%m%d_%H%M%S}")
        target = Path(output_dir).expanduser().resolve() if output_dir else self.output
        target.mkdir(parents=True, exist_ok=True)
        zip_path = target / f"{spec_name}.zip"
        suffix = 0
        while zip_path.exists():
            suffix += 1
            zip_path = target / f"{spec_name} ({suffix}).zip"
        file_path = str(record.get("filePath", "")).lstrip("/")
        url = f"{self.file_base}/{file_path}"
        url += "?attname=" + quote(f"{spec_name}.zip")
        headers = dict(self._session["headers"])
        headers["Referer"] = f"{self.base_url}/"
        async with httpx.AsyncClient(
                cookies=self._session["cookies"], headers=headers,
                timeout=600, follow_redirects=True) as client:
            response = await client.get(url)
        if response.status_code != 200 or not response.content.startswith(b"PK"):
            raise CsmarError(
                f"下载文件失败（HTTP {response.status_code}），CSMAR 可能已删除临时文件。")
        zip_path.write_bytes(response.content)
        extract_dir = target / spec_name
        extract_dir.mkdir(parents=True, exist_ok=True)
        files = []
        root = extract_dir.resolve()
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member = (extract_dir / info.filename).resolve()
                if not member.is_relative_to(root):
                    continue
                member.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, member.open("wb") as dest:
                    dest.write(source.read())
                files.append({"path": str(member), "bytes": member.stat().st_size})
        return {
            "success": True,
            "table_id": str(init.get("id")),
            "table_name": init.get("tableName"),
            "start_date": start,
            "end_date": end,
            "codes": codes or "全部代码",
            "format": fmt_key,
            "data_count": record.get("dataCount"),
            "file_size": record.get("fileSize"),
            "zip_path": str(zip_path),
            "extract_dir": str(extract_dir),
            "files": files,
        }

    async def _ws_wait(self, outline_id: str, timeout_seconds: int = 600) -> dict:
        await self._ensure_session()
        token = self._session["headers"].get("Token", "")
        sign_code = self._session["headers"].get("signCode", "")
        timeout_seconds = max(60, min(int(timeout_seconds or 600), 3600))
        async with self._lock:
            await self._start_pw()
            ctx = await self._launch(visible=True, offscreen=True)
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(f"{self.base_url}/csmar.html#/index",
                                wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(1500)
                script = """
                ({outlineId, token, signCode, timeout, wsUrl}) => new Promise((resolve, reject) => {
                  const ws = new WebSocket(wsUrl);
                  let done = false;
                  const finish = (fn, value) => {
                    if (done) return;
                    done = true;
                    try { ws.close(); } catch (e) {}
                    fn(value);
                  };
                  const timer = setTimeout(
                    () => finish(reject, new Error('CSMAR 下载进度等待超时。')),
                    timeout);
                  ws.onopen = () => ws.send(JSON.stringify(
                    {outlineId, status: 'start', token, signCode}));
                  ws.onmessage = (event) => {
                    try {
                      const msg = JSON.parse(event.data);
                      if (msg.code === 0 && msg.data) {
                        if (Number(msg.data.percentage) >= 100 && msg.data.filePath) {
                          clearTimeout(timer);
                          finish(resolve, msg.data);
                        }
                      } else if (msg.code !== 0) {
                        clearTimeout(timer);
                        finish(reject, new Error(msg.msg || 'CSMAR 下载失败。'));
                      }
                    } catch (e) {}
                  };
                  ws.onerror = () => {
                    clearTimeout(timer);
                    finish(reject, new Error('CSMAR 下载 WebSocket 连接失败。'));
                  };
                })
                """
                return await asyncio.wait_for(
                    page.evaluate(script, {"outlineId": outline_id,
                                           "token": token,
                                           "signCode": sign_code,
                                           "timeout": timeout_seconds * 1000,
                                           "wsUrl": self.ws_url}),
                    timeout=timeout_seconds + 60)
            finally:
                try:
                    await ctx.close()
                except Exception:
                    pass
                await self._stop_pw_if_idle()
