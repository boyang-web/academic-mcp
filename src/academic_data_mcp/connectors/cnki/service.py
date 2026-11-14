"""CNKI 服务层（检索/下载后端源自 wuruiqi/cnki-mcp）。

平台相关的路径、UA 与浏览器通道统一由 academic_data_mcp.platform 提供，
本模块不再直接判断操作系统或写入包内目录。
"""
import asyncio
import functools
import hashlib
import json
import os
import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from academic_data_mcp import platform as plat
from academic_data_mcp.browser.locking import InterProcessLock

from .upstream import browser, search, download

# CNKI 的反爬网关会拒绝 Playwright 默认的无头指纹；UA 版本通常从真实浏览器
# 探测并缓存，下面这个版本号只在探测失败时兜底。
_FALLBACK_VERSION = "141"


class ServiceBusy(RuntimeError):
    """Another process is driving the shared CNKI browser profile."""


def default_output_dir():
    """未显式配置时的 PDF 输出目录；正常路径由 AppConfig 统一提供。"""
    return plat.default_downloads_dir() / plat.APP_SLUG / "cnki"


async def _probe_browser_version(channel):
    """Read the real version of a browser channel via a throwaway launch."""
    try:
        kwargs = {} if channel == 'chromium' else {'channel': channel}
        stub = await browser._playwright.chromium.launch(headless=True, **kwargs)
        try:
            return stub.version
        finally:
            await stub.close()
    except Exception:
        return ''


async def resolve_channel_and_ua(data_dir, requested):
    """Pick a browser channel and build a UA matching its real version.

    A hard-coded UA version drifts from the actual browser after upgrades,
    which is itself a fingerprint; the probed version is cached so steady-state
    startup costs nothing. Returns (channel, user_agent, meta).
    """
    meta_path = Path(data_dir) / 'browser-meta.json'
    cached = {}
    try:
        cached = json.loads(meta_path.read_text(encoding='utf-8'))
    except Exception:
        pass
    candidates = ([requested] if requested != 'auto'
                  else list(plat.browser_candidates()))
    for channel in candidates:
        if cached.get('channel') == channel and cached.get('ua'):
            return channel, cached['ua'], {'channel': channel,
                'version': cached.get('version', ''), 'ua_source': 'cache'}
        version = await _probe_browser_version(channel)
        if version:
            major = version.split('.')[0]
            ua = plat.user_agent(major, channel)
            meta = {'channel': channel, 'version': version, 'ua': ua,
                    'ua_source': 'probe', 'probed_at': datetime.now().isoformat()}
            try:
                meta_path.parent.mkdir(parents=True, exist_ok=True)
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                     encoding='utf-8')
            except Exception:
                pass
            return channel, ua, {'channel': channel, 'version': version,
                                 'ua_source': 'probe'}
        if requested != 'auto':
            break
    if requested != 'auto':
        return requested, plat.user_agent(_FALLBACK_VERSION, requested), {
            'channel': requested, 'version': '', 'ua_source': 'fallback'}
    return 'chromium', plat.user_agent(_FALLBACK_VERSION), {
        'channel': 'chromium', 'version': '', 'ua_source': 'fallback'}



def strict_years(papers, year_start, year_end, max_n):
    return [p for p in papers if str(p.get('year', '')).isdigit()
            and year_start <= int(p['year']) <= year_end][:max_n]


def validate_detail_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'kns.cnki.net'
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not parsed.path.startswith(('/kcms2/article/', '/kcms/detail/'))):
        raise ValueError('Use a CNKI article URL returned by cnki_search.')


def save_pdf(source, output, title):
    source = Path(source)
    with source.open('rb') as stream:
        if stream.read(5) != b'%PDF-':
            raise ValueError('Downloaded content is not a PDF; no output file was created.')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = download._safe_filename(title or 'paper')
    if not Path(name).stem or Path(name).stem.upper() in {
            'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)],
            *[f'LPT{i}' for i in range(1, 10)]}:
        name = 'paper.pdf'
    destination = output / name
    suffix = 0
    while True:
        try:
            with destination.open('xb') as target, source.open('rb') as stream:
                shutil.copyfileobj(stream, target)
            break
        except FileExistsError:
            suffix += 1
            destination = output / f'{Path(name).stem} ({suffix}).pdf'
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {'success': True, 'file_path': str(destination),
            'bytes': destination.stat().st_size, 'sha256': digest, 'format': 'pdf'}


class Service:
    def __init__(self, data_dir, output_dir, channel, headless=True):
        self.data = Path(data_dir).resolve()
        self.output = Path(output_dir).resolve()
        self.channel = channel
        self.headless = headless
        self.visible = not headless
        self.lock = asyncio.Lock()
        self.process_lock = InterProcessLock(self.data / '.runtime.lock')
        self.browser_meta = {}
        self.login_pending = False
        browser.PROFILE_DIR = str(self.data / 'profile')
        browser.COOKIE_FILE = str(self.data / 'cookies.json')
        browser._launch_context = self.launch
        search._filter_by_year = strict_years
        # Advertise PDF only; upstream's CAJ fallback is not verified here.
        download._DL_BUTTON_SELECTORS = ['a#pdfDown', 'li.btn-dlpdf a', "a:has-text('PDF下载')"]
        download._http_fetch = self.fetch
        download.print = functools.partial(print, file=sys.stderr)

    @asynccontextmanager
    async def exclusive(self):
        """Serialise browser use within this process and across processes."""
        if not self.process_lock.held:
            if not await self.process_lock.acquire():
                raise ServiceBusy(
                    'The CNKI browser is in use by another session on this computer '
                    '(it may be mid-download or waiting for a manual login). Retry '
                    'shortly, or close it there with cnki_close_browser.')
        async with self.lock:
            yield

    async def launch(self):
        self.channel, user_agent, self.browser_meta = await resolve_channel_and_ua(
            self.data, self.channel)
        kwargs = {'headless': not self.visible, 'accept_downloads': True, 'locale': 'zh-CN',
                  'downloads_path': str(self.data / 'browser-downloads'),
                  'user_agent': user_agent,
                  # Upstream tolerates CNKI's CDN SNI mismatches and campus-network
                  # TLS interception this way; downloads stay verified by the host
                  # allowlist, the %PDF- magic and SHA256.
                  'ignore_https_errors': True,
                  'args': ['--disable-blink-features=AutomationControlled', '--no-first-run',
                           '--ignore-certificate-errors']}
        if self.channel != 'chromium':
            kwargs['channel'] = self.channel
        # Leftover singleton locks from a killed browser block the next launch.
        # Clearing them is only safe while holding the process lock (we do), so
        # no live browser from another process can be shadowed here.
        browser._clear_singleton_locks()
        try:
            context = await browser._playwright.chromium.launch_persistent_context(
                browser.PROFILE_DIR, **kwargs)
        except Exception as error:
            text = f'{type(error).__name__}: {error}'
            if any(marker in text.lower() for marker in
                   ('singleton', 'processsingleton', 'lock', 'cookies', 'profile')):
                raise ServiceBusy(
                    'Could not open the CNKI browser profile — another browser instance '
                    'is probably using the same login directory '
                    f'({text[:200]}). Close other CNKI tool sessions, then retry.'
                ) from error
            raise
        if not context.pages:
            await context.new_page()
        return context

    async def fetch(self, url, cookie_header, referer):
        import httpx
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.cnki.net'):
            return None, 'Unexpected download host.'
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
                response = await client.get(url, headers={
                    'Cookie': cookie_header, 'Referer': referer,
                    'User-Agent': (self.browser_meta.get('ua')
                                   or plat.user_agent(_FALLBACK_VERSION))})
                if response.status_code == 200 and response.content.startswith(b'%PDF-'):
                    return response.content, None
                return None, f'Download did not return a PDF (HTTP {response.status_code}).'
        except Exception as error:
            return None, f'{type(error).__name__}: download request failed.'

    async def status(self):
        """Check local CNKI institution login and CAPTCHA state; may open the tool browser."""
        async with self.exclusive():
            result = await browser.check_login_status()
            payload = {key: result.get(key) for key in ('logged_in', 'org', 'captcha', 'detail')}
            payload['browser'] = dict(self.browser_meta)
            return payload

    async def login(self):
        """Open a dedicated browser for the user to complete CNKI/CARSI authentication; then call cnki_finish_login."""
        async with self.exclusive():
            if not self.visible:
                await browser.close_context()
                self.visible = True
            context = await browser.get_context()
            page = await context.new_page()
            await page.goto('https://www.cnki.net/', wait_until='domcontentloaded')
            # The login page must survive between tool calls until the user
            # finishes; settle() keeps the browser (and the lock) for this case.
            self.login_pending = True
            return {'opened': True, 'next_step': 'Complete institution/CARSI login in the browser, then call cnki_finish_login.'}

    async def finish_login(self):
        """Check the user's completed authentication and save it locally for future launches."""
        async with self.exclusive():
            result = await browser.check_login_status()
            if not result.get('logged_in') or result.get('captcha'):
                return {'success': False, 'message': 'Complete authentication or verification in the browser first.'}
            await browser.save_cookies(await browser.get_context())
            self.login_pending = False
            # Back to background mode for future calls when the service default
            # is headless; settle() closes this (visible) browser afterwards.
            if self.headless:
                self.visible = False
            return {'success': True, 'org': result.get('org'), 'message': 'Authentication saved locally.'}

    async def find(self, query, year_start, year_end, limit):
        if not query.strip() or not 1 <= limit <= 20 or year_start > year_end:
            raise ValueError('Provide a query, ordered year range, and limit between 1 and 20.')
        async with self.exclusive():
            return await search.search_papers(query, year_start, year_end, limit)

    async def get_pdf(self, detail_url, title='', output_dir='', captcha_wait=0):
        validate_detail_url(detail_url)
        if not 0 <= captcha_wait <= 600:
            raise ValueError('captcha_wait must be between 0 and 600 seconds.')
        target = Path(output_dir).expanduser().resolve() if output_dir else self.output
        async with self.exclusive():
            staging = self.data / 'staging' / uuid.uuid4().hex
            staging.mkdir(parents=True)
            download.PDF_DIR = str(staging)
            result = await download.download_paper(detail_url, title or None, captcha_wait=captcha_wait)
            if not result.get('success'):
                return {'success': False, 'captcha': bool(result.get('captcha')),
                        'message': result.get('message', 'Download failed.')}
            source = Path(result['file_path']).resolve()
            if not source.is_relative_to(staging.resolve()):
                raise ValueError('Unexpected download output path.')
            verified = save_pdf(source, target, title or source.stem)
            source.unlink()
            staging.rmdir()
            return verified

    async def close(self):
        """Save the current authentication locally and close the tool browser."""
        self.login_pending = False
        async with self.lock:
            try:
                await browser.close_context()
            except Exception:
                pass
        self.process_lock.release()
        return {'closed': True, 'message': 'Saved authentication will be restored on next launch.'}

    async def settle(self):
        """Close the browser and release the cross-process lock after a tool call.

        Keeping one long-lived browser between calls let a second process (a
        second client, a CLI login, a stale instance) find the lock free, start
        a rival Chromium on the same profile and hit locked Cookies/Singleton
        errors — so the browser now lives exactly as long as one tool call.
        The institution-login page is the exception: it stays open (and keeps
        the lock) until cnki_finish_login or cnki_close_browser.
        """
        if self.login_pending:
            return
        async with self.lock:
            try:
                await browser.close_context()
            except Exception:
                pass
        self.process_lock.release()


def busy_safe(service, function):
    """Wrap a Service method for MCP: structured lock errors, settle after every call."""
    @functools.wraps(function)
    async def wrapper(*args, **kwargs):
        try:
            return await function(*args, **kwargs)
        except ServiceBusy as error:
            return {'success': False, 'busy': True, 'message': str(error)}
        finally:
            try:
                await service.settle()
            except Exception:
                pass
    return wrapper
