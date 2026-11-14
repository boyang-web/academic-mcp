"""CNKI（知网）数据源插件：期刊检索与有权限的 PDF 下载。"""
from __future__ import annotations

from datetime import datetime

from academic_data_mcp.base import ProviderInfo, ProviderRuntime, ToolSpec
from academic_data_mcp.config import AppConfig

from .service import Service, busy_safe

SUMMARY = (
    "Search CNKI journals with strict year filtering (first results page, up to "
    "20 entries) and download authorised PDFs to a local folder. Use "
    "cnki_search to obtain detail URLs, then cnki_download_pdf, which verifies "
    "the PDF magic and SHA256 and never overwrites existing files. Institution "
    "or CARSI login and CAPTCHA must be completed by the user in the browser. "
    "Never interpret errors as zero hits and never bypass verification."
)

INFO = ProviderInfo(name="cnki", title="CNKI 知网", summary=SUMMARY,
                    requires_login=True)


class CnkiRuntime:
    def __init__(self, service: Service) -> None:
        self._service = service

    @property
    def info(self) -> ProviderInfo:
        return INFO

    def tools(self) -> list[ToolSpec]:
        service = self._service
        prefix = INFO.name

        def safe(method):
            return busy_safe(service, method)

        async def search(query: str, year_start: int = 2000,
                         year_end: int = datetime.now().year,
                         limit: int = 5) -> dict:
            return await safe(service.find)(query, year_start, year_end, limit)

        async def download_pdf(detail_url: str, title: str = "",
                               output_dir: str = "",
                               captcha_wait: int = 0) -> dict:
            return await safe(service.get_pdf)(detail_url, title, output_dir,
                                               captcha_wait)

        return [
            ToolSpec(f"{prefix}_login_status",
                     "Check the local CNKI institution login and CAPTCHA state; "
                     "may open the tool browser.",
                     safe(service.status)),
            ToolSpec(f"{prefix}_open_login_page",
                     "Open a visible browser for the user to complete CNKI/CARSI "
                     "authentication; then call cnki_finish_login.",
                     safe(service.login)),
            ToolSpec(f"{prefix}_finish_login",
                     "Verify that the user finished authentication and save it "
                     "locally for future launches.",
                     safe(service.finish_login)),
            ToolSpec(f"{prefix}_close_browser",
                     "Save the current authentication and close the tool browser.",
                     safe(service.close)),
            ToolSpec(f"{prefix}_search",
                     "Search CNKI journals with strict year filtering; at most "
                     "the first 20 results. Never interpret errors as zero hits.",
                     search),
            ToolSpec(f"{prefix}_download_pdf",
                     "Download an authorised CNKI PDF to the requested folder. "
                     "Returns a verified path, size and SHA256; avoids "
                     "overwriting. Authentication/CAPTCHA must be handled by "
                     "the user.",
                     download_pdf),
        ]

    async def aclose(self) -> None:
        try:
            await self._service.close()
        except Exception:
            pass


def create(config: AppConfig) -> ProviderRuntime:
    settings = config.for_provider("cnki")
    service = Service(
        data_dir=str(config.provider_data_dir("cnki")),
        output_dir=str(config.provider_output_dir("cnki")),
        channel=settings.get("BROWSER_CHANNEL") or config.browser_channel,
        headless=config.headless and not settings.flag("SHOW_BROWSER"),
    )
    return CnkiRuntime(service)
