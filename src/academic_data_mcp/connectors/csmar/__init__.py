"""CSMAR（国泰安）数据源插件：单表目录浏览、预览与导出。"""
from __future__ import annotations

from academic_data_mcp import platform as plat
from academic_data_mcp.base import ProviderInfo, ProviderRuntime, ToolSpec
from academic_data_mcp.config import AppConfig
from academic_data_mcp.registry import provider

from .client import (CsmarError, CsmarService, LoginRequired,
                     DEFAULT_BASE, DEFAULT_FILE_BASE)

SUMMARY = (
    "Browse the CSMAR single-table catalogue at data.csmar.com, inspect field "
    "dictionaries, preview rows (capped at 100 by CSMAR), and export full "
    "tables as xlsx/xls/dta/csv/txt/json/sav/xml. Walk the catalogue with "
    "csmar_list_series -> csmar_list_databases -> csmar_list_tables -> "
    "csmar_get_table_fields, then csmar_preview before csmar_download. A full "
    "export consumes the institution's download quota. Never invent figures; "
    "report errors verbatim. Requires a one-time manual login."
)

INFO = ProviderInfo(name="csmar", title="CSMAR 国泰安", summary=SUMMARY,
                    requires_login=True)


def _safe(coro):
    async def run():
        try:
            return await coro
        except LoginRequired as error:
            return {"success": False, "login_required": True,
                    "message": str(error)}
        except CsmarError as error:
            return {"success": False, "message": str(error)}
        except Exception as error:  # noqa: BLE001
            return {"success": False,
                    "message": f"{type(error).__name__}: {error}"}
    return run()


class CsmarRuntime:
    def __init__(self, service: CsmarService) -> None:
        self._service = service

    @property
    def info(self) -> ProviderInfo:
        return INFO

    def tools(self) -> list[ToolSpec]:
        service = self._service
        prefix = INFO.name

        async def login_status() -> dict:
            return await _safe(service.status())

        async def open_login_page() -> dict:
            return await _safe(service.open_login())

        async def finish_login() -> dict:
            return await _safe(service.finish_login())

        async def close_browser() -> dict:
            return await _safe(service.close_browser())

        async def list_series() -> dict:
            return await _safe(service.list_series())

        async def list_databases(series_id: str) -> dict:
            return await _safe(service.list_databases(series_id))

        async def list_tables(database_id: str) -> dict:
            return await _safe(service.list_tables(database_id))

        async def get_table_fields(table_id: str, keyword: str = "",
                                   limit: int = 300) -> dict:
            return await _safe(service.get_fields(table_id, keyword, limit))

        async def preview(table_id: str, fields: list[str] | str | None = None,
                          start_date: str = "", end_date: str = "",
                          codes: str = "") -> dict:
            return await _safe(service.preview(table_id, fields, start_date,
                                               end_date, codes))

        async def download(table_id: str, fields: list[str] | str | None = None,
                           start_date: str = "", end_date: str = "",
                           codes: str = "", file_format: str = "xlsx",
                           output_dir: str = "",
                           timeout_seconds: int = 600) -> dict:
            return await _safe(service.download(
                table_id, fields, start_date, end_date, codes, file_format,
                output_dir, timeout_seconds))

        return [
            ToolSpec(f"{prefix}_login_status",
                     "Check the CSMAR login session saved in the tool browser "
                     "profile.", login_status),
            ToolSpec(f"{prefix}_open_login_page",
                     "Open a visible CSMAR login window; the user completes "
                     "account or QR login, then call csmar_finish_login.",
                     open_login_page),
            ToolSpec(f"{prefix}_finish_login",
                     "Verify the user finished CSMAR login in the browser and "
                     "save the session.", finish_login),
            ToolSpec(f"{prefix}_close_browser",
                     "Save the CSMAR login state and close the tool browser "
                     "window.", close_browser),
            ToolSpec(f"{prefix}_list_series",
                     "List CSMAR database series and hot databases.",
                     list_series),
            ToolSpec(f"{prefix}_list_databases",
                     "List the databases inside one CSMAR series; use IDs from "
                     "csmar_list_series.", list_databases),
            ToolSpec(f"{prefix}_list_tables",
                     "List data tables in one CSMAR database, grouped by "
                     "category, with numeric table IDs.", list_tables),
            ToolSpec(f"{prefix}_get_table_fields",
                     "List a CSMAR table's fields (physical name, Chinese "
                     "title, type, explanation, default selection). Requires a "
                     "table ID from csmar_list_tables.", get_table_fields),
            ToolSpec(f"{prefix}_preview",
                     "Preview up to 100 CSMAR rows. fields accepts field names, "
                     "Chinese titles or IDs; codes is a comma-separated stock "
                     "code list (empty = all); dates as YYYY, YYYY-MM or "
                     "YYYY-MM-DD.", preview),
            ToolSpec(f"{prefix}_download",
                     "Download a full CSMAR table export to a local folder and "
                     "unzip it. This consumes the institution's quota and may "
                     "take minutes. Formats: xlsx (default), xls, dta, csv, "
                     "txt, json, sav, xml.", download),
        ]

    async def aclose(self) -> None:
        close = getattr(self._service, "aclose", None)
        if close is not None:
            await close()


@provider(name="csmar", title="CSMAR 国泰安", summary=SUMMARY,
          requires_login=True)
def create(config: AppConfig) -> ProviderRuntime:
    settings = config.for_provider("csmar")
    service = CsmarService(
        data_dir=str(config.provider_data_dir("csmar")),
        output_dir=str(config.provider_output_dir("csmar")),
        channel=settings.get("BROWSER_CHANNEL") or config.browser_channel,
        base_url=settings.get("BASE_URL") or DEFAULT_BASE,
        file_base=settings.get("FILE_BASE") or DEFAULT_FILE_BASE,
        capture_headless=settings.flag(
            "CAPTURE_HEADLESS", plat.capture_headless_default()),
    )
    return CsmarRuntime(service)
