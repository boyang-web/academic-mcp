from academic_data_mcp.config import AppConfig
from academic_data_mcp.gateways.whu.routing import access_plan, database_entries, record_routes
from academic_data_mcp.registry import get


def test_whu_is_primary_and_legacy_config_resolves_to_it(monkeypatch, tmp_path):
    monkeypatch.setenv('ADM_PROVIDERS', 'luojia,whu,cnki,csmar')
    monkeypatch.setenv('ADM_LUOJIA_TIMEOUT', '12')
    monkeypatch.setenv('ADM_WHU_TIMEOUT', '18')
    old = tmp_path / 'luojia'; old.mkdir()
    config = AppConfig.from_env(data_dir=tmp_path)
    assert config.enabled == ('whu', 'cnki', 'csmar')
    assert config.for_provider('whu').integer('TIMEOUT', 30) == 18
    assert config.provider_data_dir('whu') == old
    assert get('luojia').name == 'whu'
    assert get('whu').info.role == 'primary'


def test_current_whu_profile_takes_precedence_over_legacy(tmp_path):
    (tmp_path / 'luojia').mkdir(); (tmp_path / 'whu').mkdir()
    assert AppConfig.from_env(data_dir=tmp_path).provider_data_dir('whu') == tmp_path / 'whu'


def test_library_route_is_preserved_and_cnki_is_explicit_supplement():
    url = 'https://whu.metaersp.cn/middle?url=https://kns.cnki.net/kcms2/article/abstract?v=test'
    plan = access_plan(url)
    assert plan['url'] == url
    assert plan['next_tool'] == 'whu_read_page'
    assert plan['next_arguments'] == {'url': url}
    assert plan['supplementary']['provider'] == 'cnki'
    assert not plan['permission_verified']


def test_csmar_plan_does_not_trigger_quota_consumption():
    plan = access_plan('https://data.csmar.com/')
    assert plan['primary_provider'] == 'whu'
    assert plan['supplementary']['provider'] == 'csmar'
    assert plan['route_kind'] == 'publisher_source'
    assert not plan['permission_verified']


def test_prefer_actual_library_routes_without_fabricating_urls():
    direct = 'https://publisher.example.org/article'
    proxy = 'https://whu.metaersp.cn/middle?url=' + direct
    paper = {'access_links': [
        {'url': 'https://whu-primo.hosted.exlibrisgroup.com.cn/primo-explore/fulldisplay?a=b'},
        {'url': proxy}], 'source_links': [{'url': direct}, {'url': proxy}]}
    routes = record_routes(paper)
    assert [r['url'] for r in routes] == [proxy, direct]
    assert routes[0]['route_kind'] == 'library'


def test_database_navigation_uses_published_ids_and_urls():
    url = 'https://whu.metaersp.cn/databaseDetail?databaseNum=DB_123'
    rows = database_entries([{'name': '中文', 'children': [
        {'name': '知网', 'link': url}, {'name': 'duplicate', 'link': url}]},
        {'name': 'other', 'link': 'https://example.org/'}])
    assert len(rows) == 1
    assert rows[0]['name'] == '知网'
    assert rows[0]['id'] == 'DB_123'
    assert rows[0]['url'] == url


def test_doctor_masks_tokens_and_proxy_credentials(monkeypatch):
    monkeypatch.setenv('ADM_WHU_API_TOKEN', 'private-token')
    monkeypatch.setenv('ADM_WHU_PROXY', 'http://username:private-password@proxy.example.org:8080')
    result = AppConfig.from_env().for_provider('whu').redacted()
    assert 'private-token' not in str(result)
    assert 'private-password' not in str(result)
    assert 'username' not in str(result)
    assert result['PROXY'].endswith('proxy.example.org:8080')


def test_article_route_precedes_pdf_for_session_establishment():
    article = 'https://whu.metaersp.cn/middle?url=https://link.springer.com/article/10.1/test'
    pdf = 'https://whu.metaersp.cn/middle?url=https://link.springer.com/content/pdf/10.1/test.pdf'
    rows = record_routes({'access_links': [{'url': pdf}, {'url': article}]})
    assert rows[0]['url'] == article
    assert access_plan(pdf)['next_tool'] == 'whu_download_pdf'
