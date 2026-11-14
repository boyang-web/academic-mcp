"""Regression tests for deadlines, real response shapes and download integrity."""
import asyncio
import base64
import hashlib
import time

import httpx
import pytest

from academic_data_mcp.config import AppConfig
from academic_data_mcp.browser import session as web
from academic_data_mcp.gateways.whu.client import compact_record
from academic_data_mcp.gateways.whu.client import WhuGateway


def session(tmp_path):
    config = AppConfig.from_env(data_dir=tmp_path / 'state', downloads_dir=tmp_path / 'out')
    return web.WebSession(config, 'test', 'https://example.org/')


@pytest.mark.parametrize('url,text,status,code', [
    ('https://example.org/sorry/index', 'captcha', 200, 'verification_required'),
    ('https://example.org/search', 'Our systems have detected unusual traffic', 200, 'verification_required'),
    ('https://example.org/search', '', 429, 'verification_required'),
    ('https://www.metaauth.com/242010/login.html', '', 200, 'authentication_required'),
    ('https://auth.whu.edu.cn/authserver/login', '', 200, 'authentication_required'),
    ('https://publisher.example.org/article', '', 403, 'access_denied'),
    ('https://publisher.example.org/article', '', 500, 'http_error'),
    ('https://example.org/article', 'There was a problem providing the content you requested CPE00001', 200, 'access_denied'),
    ('chrome-error://chromewebdata/', 'Site cannot be reached', 200, 'network_error'),
])
def test_access_errors_are_distinct(url, text, status, code):
    with pytest.raises(web.AccessError) as error:
        web.page_problem(url, text, status)
    assert error.value.code == code


def test_deadline_includes_queue_wait(tmp_path):
    async def run():
        s = session(tmp_path); s.timeout = 0.03
        await s.lock.acquire()
        called = False
        async def op():
            nonlocal called
            called = True
        started = time.monotonic()
        result = await s.run(op)
        assert time.monotonic() - started < 0.5
        assert result['error_code'] == 'timeout'
        assert not called
        s.lock.release()
        await asyncio.sleep(0)
    asyncio.run(run())


def test_deadline_does_not_wait_for_slow_cancellation(tmp_path):
    async def run():
        s = session(tmp_path); s.timeout = 0.03
        gate = asyncio.Event()
        async def hung():
            try:
                await asyncio.Event().wait()
            finally:
                await gate.wait()
        started = time.monotonic()
        result = await s.run(hung)
        assert result['error_code'] == 'timeout'
        assert time.monotonic() - started < 0.5
        gate.set()
        await asyncio.sleep(0.01)
        assert not s.pending
    asyncio.run(run())


def test_network_failure_does_not_leak_proxy_credentials(tmp_path):
    async def run():
        s = session(tmp_path)
        async def op():
            raise httpx.ConnectError('https://user:secret@proxy.example.org')
        result = await s.run(op)
        assert result['error_code'] == 'network_error'
        assert 'secret' not in str(result)
        assert 'count' not in result
    asyncio.run(run())


def test_primo_record_preserves_identifiers_and_actual_links():
    d = {'context': 'PC', 'pnx': {
        'control': {'recordid': ['TN_123']},
        'display': {'title': ['A real title'], 'type': ['article'], 'description': ['Abstract']},
        'search': {'creationdate': ['2024']},
        'addata': {'doi': ['10.1234/example'], 'au': ['An Author']},
        'delivery': {'fulltext': ['fulltext']},
        'links': {'linktopdf': ['$$Uhttps://whu.metaersp.cn/middle?url=https://publisher.example.org/a.pdf$$DDownload'],
                  'thumbnail': ['$$Uhttps://example.org/image.jpg$$Dthumbnail']}}}
    record = compact_record(d, 'https://whu-primo.hosted.exlibrisgroup.com.cn', '86WHU')
    assert record['record_id'] == 'TN_123'
    assert record['abstract'] == 'Abstract'
    assert record['doi'] == '10.1234/example'
    assert record['source_links'] == [{'kind': 'linktopdf', 'url': 'https://whu.metaersp.cn/middle?url=https://publisher.example.org/a.pdf'}]
    assert not record['download_permission_verified']
    assert 'docid=TN_123' in record['detail_url']


def test_changed_primo_shape_is_not_zero_hits():
    with pytest.raises(web.AccessError) as error:
        compact_record({'pnx': {}}, 'https://example.org', '86WHU')
    assert error.value.code == 'parse_error'


class FakePage:
    async def evaluate(self, _, arguments=None):
        if arguments is not None:
            return getattr(self, 'fetch_result', {'not_pdf': True, 'status': 200,
                'url': arguments['url'], 'text': '<html>Not a PDF</html>'})
        return 'test-browser'


class FakeContext:
    async def cookies(self):
        return [{'name': 'institution', 'value': 'session', 'domain': 'example.org', 'path': '/'}]


def download_session(tmp_path, monkeypatch, handler):
    s = session(tmp_path)
    s.context = FakeContext()
    page = FakePage(); page.url = 'https://example.org/article'
    async def browser(*_):
        return page
    s.browser = browser
    original = httpx.AsyncClient
    monkeypatch.setattr(web.httpx, 'AsyncClient', lambda **kw: original(
        **kw, transport=httpx.MockTransport(handler)))
    return s


def test_pdf_download_uses_cookies_verifies_bytes_and_preserves_existing(tmp_path, monkeypatch):
    pdf = b'%PDF-1.7\nactual content\n%%EOF'
    def handler(request):
        assert 'institution=session' in request.headers['cookie']
        return httpx.Response(200, content=pdf, headers={'content-type': 'application/pdf'})
    s = download_session(tmp_path, monkeypatch, handler)
    s.output.mkdir(parents=True); (s.output / 'paper.pdf').write_bytes(b'original')
    result = asyncio.run(s.download_pdf('https://example.org/a.pdf'))
    assert result['success']
    assert result['sha256'] == hashlib.sha256(pdf).hexdigest()
    assert (s.output / 'paper.pdf').read_bytes() == b'original'
    assert (s.output / 'paper (1).pdf').read_bytes() == pdf
    assert not list(s.output.glob('*.part'))


def test_html_is_never_saved_as_pdf(tmp_path, monkeypatch):
    s = download_session(tmp_path, monkeypatch, lambda _: httpx.Response(
        200, text='<html>Publisher login required</html>'))
    result = asyncio.run(s.download_pdf('https://example.org/a.pdf'))
    assert not result['success'] and result['error_code'] == 'pdf_link_required'
    assert not s.output.exists()


def test_size_limit_rejects_transfer_and_removes_partial_file(tmp_path, monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'%PDF-' + b'x' * (64 * 1024 - 5)
            yield b'x' * (64 * 1024)
    s = download_session(tmp_path, monkeypatch, lambda _: httpx.Response(200, stream=Stream()))
    s.max_bytes = 70 * 1024
    result = asyncio.run(s.download_pdf('https://example.org/a.pdf'))
    assert result['error_code'] == 'file_too_large'
    assert not list(s.output.iterdir())


def test_stalled_transfer_is_cancelled_and_partial_removed(tmp_path, monkeypatch):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'%PDF-' + b'x' * (64 * 1024 - 5)
            await asyncio.Event().wait()
    s = download_session(tmp_path, monkeypatch, lambda _: httpx.Response(200, stream=Stream()))
    s.download_timeout = 0.03
    async def run():
        result = await s.download_pdf('https://example.org/a.pdf')
        await asyncio.sleep(0.01)
        assert result['error_code'] == 'timeout'
        assert not list(s.output.iterdir())
    asyncio.run(run())


def test_whu_invalid_arguments_fail_without_network(tmp_path):
    s = WhuGateway(AppConfig.from_env(data_dir=tmp_path))
    result = asyncio.run(s.search('test', limit=0))
    assert result['error_code'] == 'invalid_argument'
    assert s.context is None


def test_browser_download_after_http_client_rejection(tmp_path, monkeypatch):
    pdf = b'%PDF-1.7\nfrom authenticated browser\n%%EOF'
    s = download_session(tmp_path, monkeypatch, lambda _: httpx.Response(403))
    async def run():
        page = await s.browser()
        page.fetch_result = {'status': 200, 'url': 'https://example.org/a.pdf',
                             'data': base64.b64encode(pdf).decode()}
        result = await s.download_pdf('https://example.org/a.pdf')
        assert result['success'] and result['transfer'] == 'browser'
        assert result['sha256'] == hashlib.sha256(pdf).hexdigest()
        assert (s.output / 'paper.pdf').read_bytes() == pdf
    asyncio.run(run())


def test_browser_fallback_does_not_ignore_access_denial(tmp_path, monkeypatch):
    s = download_session(tmp_path, monkeypatch, lambda _: httpx.Response(403))
    async def run():
        page = await s.browser()
        page.fetch_result = {'status': 403, 'url': 'https://example.org/a.pdf'}
        result = await s.download_pdf('https://example.org/a.pdf')
        assert result['error_code'] == 'access_denied'
        assert not s.output.exists()
    asyncio.run(run())


def test_cross_origin_denial_does_not_trigger_browser_fetch(tmp_path, monkeypatch):
    s = download_session(tmp_path, monkeypatch, lambda _: httpx.Response(403))
    async def forbidden(*_):
        raise AssertionError('Cross-origin browser fetch must not run')
    s._browser_pdf = forbidden
    result = asyncio.run(s.download_pdf('https://other.example.org/a.pdf'))
    assert result['error_code'] == 'access_denied'
    assert not s.output.exists()
