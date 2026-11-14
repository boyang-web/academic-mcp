import asyncio
from academic_data_mcp.config import AppConfig
from academic_data_mcp.gateways.whu import WhuRuntime
from academic_data_mcp.registry import discover


def test_only_unified_public_tools_are_exposed(tmp_path):
    runtime=WhuRuntime(AppConfig.from_env(data_dir=tmp_path))
    assert all(t.name.startswith('whu_') for t in runtime.tools())
    assert len(runtime.tools())==13
    assert not runtime.adapters.runtimes
    entries=discover(force=True)
    assert 'cnki' not in entries and 'csmar' not in entries


def test_capabilities_provide_typed_arguments_without_network(tmp_path):
    runtime=WhuRuntime(AppConfig.from_env(data_dir=tmp_path))
    async def run():
        caps=await runtime.adapters.capabilities('csmar')
        assert caps['success']
        preview=next(o for o in caps['operations'] if o['operation']=='preview')
        assert 'table_id' in preview['parameters']['properties']
        assert next(o for o in caps['operations'] if o['operation']=='download')['consumes_quota']
        assert runtime.service.context is None
    asyncio.run(run())


def test_invalid_database_operations_and_arguments_do_not_start_browser(tmp_path):
    runtime=WhuRuntime(AppConfig.from_env(data_dir=tmp_path))
    async def run():
        assert (await runtime.adapters.call('unknown','preview'))['error_code']=='invalid_argument'
        assert (await runtime.adapters.call('csmar','__dict__'))['error_code']=='invalid_argument'
        assert (await runtime.adapters.call('csmar','preview',{'unexpected':'value'}))['error_code']=='invalid_argument'
        assert (await runtime.adapters.call('csmar','preview',{}))['error_code']=='invalid_argument'
        assert runtime.adapters.runtimes['csmar']._service._pw is None
    asyncio.run(run())
