"""武汉大学图书馆·珞珈学术搜索 provider。"""
from academic_data_mcp.base import ProviderInfo, ToolSpec
from academic_data_mcp.registry import provider
from .client import WhuGateway
from .routing import access_plan

SUMMARY = ('PRIMARY ENTRY POINT: start academic literature tasks here. Search WHU Primo, '
           'list/open library database entries, read publisher pages and download authorised PDFs. Campus IP or a manually authenticated institution '
           'session may provide access; preserve WHU resolver links. Manual verification '
           'is supported. Fulltext labels alone do not prove download permission.')
INFO = ProviderInfo('whu', '武大图书馆主入口', SUMMARY, True, 'primary')


class WhuRuntime:
    info = INFO

    def __init__(self, config):
        self.service = WhuGateway(config)

    def tools(self):
        s = self.service
        async def plan(url: str) -> dict:
            return access_plan(url)

        return [
            ToolSpec('whu_list_databases', 'List/search actual database quick links published by WHU Library; not a full subscription inventory or proof of permission.', s.list_databases),
            ToolSpec('whu_open_database', 'Open a database by its exact name or ID returned by whu_list_databases, using the library' + "'s real entry and the same visible authentication session.", s.open_database),
            ToolSpec('whu_access_plan', 'Plan library-first access for an actual result URL, retaining its proxy address; identify CNKI/CSMAR supplementary tools without automatically using credentials or quota.', plan),
            ToolSpec('whu_login_status', 'Check discovery reachability; institutional fulltext access is verified only by actual reading/downloading.', s.status),
            ToolSpec('whu_open_login_page', 'Open a visible persistent browser. Pass a resolver/publisher URL returned by search or read_page to complete WHU/CARSI/verification manually.', s.open_login),
            ToolSpec('whu_finish_login', 'Save the current manual authentication session locally, then retry the original operation. Does not assert institution-wide permissions.', s.finish_login),
            ToolSpec('whu_close_browser', 'Close the provider browser and retain its local persistent profile.', s.close_browser),
            ToolSpec('whu_search', 'Search WHU Primo by query, limit (1–20) and start offset. Return titles, abstracts, DOI, record links and institution resolver links.', s.search),
            ToolSpec('whu_read_page', 'Read a search result/detail/resolver/publisher page; return text, metadata and actual PDF links. Follow returned institution URLs without stripping the proxy.', s.read_page),
            ToolSpec('whu_download_pdf', 'Download an actual authorised PDF URL returned by search/read_page using the same session cookies. Bounded time/size, PDF magic and SHA256 checks; never overwrite. For HTML/login responses use read_page or open_login_page first.', s.download_pdf),
        ]

    async def aclose(self):
        await self.service.aclose()


@provider(name='whu', title=INFO.title, summary=SUMMARY, requires_login=True, role='primary')
def create(config):
    return WhuRuntime(config)
