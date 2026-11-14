import asyncio
from types import SimpleNamespace

from academic_data_mcp.config import AppConfig
from academic_data_mcp.gateways.whu.client import WhuGateway


def test_metadata_requests_include_whu_institution_header(tmp_path):
    gateway = WhuGateway(AppConfig.from_env(data_dir=tmp_path))
    calls = []
    class Response:
        status = 200
        async def json(self):
            return {'flag': True, 'data': {}}
    class Request:
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()
    async def browser():
        gateway.context = SimpleNamespace(request=Request())
    gateway.browser = browser
    asyncio.run(gateway._metadata('portalLdbDetail', {'databaseNum': 'DB_A'}))
    assert calls[0][1]['headers'] == {'librarycode': '131001'}
    assert calls[0][1]['data'] == {'databaseNum': 'DB_A'}


def test_other_institution_details_are_rejected_before_child_lookup(tmp_path):
    gateway = WhuGateway(AppConfig.from_env(data_dir=tmp_path))
    async def catalogue(keyword):
        return {'databases': [{'id': 'DB_A', 'name': 'A'}]}
    async def metadata(endpoint, body):
        assert endpoint == 'portalLdbDetail'
        return {'data': {'databaseNum': 'DB_A', 'libraryCode': 'OTHER', 'otherTagName': '订阅'}}
    gateway._database_catalog = catalogue
    gateway._metadata = metadata
    result = asyncio.run(gateway.get_database('DB_A'))
    assert not result['success']
    assert result['error_code'] == 'metadata_scope_mismatch'
