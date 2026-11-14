"""Shared, bounded browser sessions for discovery and authorised PDF downloads.

No automatic CAPTCHA solving or unbounded retries. Each provider owns a persistent
profile; manual authentication and subsequent requests use that same profile.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import Error as BrowserError
from playwright.async_api import TimeoutError as BrowserTimeout
from playwright.async_api import async_playwright

from academic_data_mcp import platform as plat
from academic_data_mcp.config import AppConfig
from academic_data_mcp.browser.locking import InterProcessLock


class AccessError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.details = code, details


def web_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ''
    if parts.scheme not in ('https', 'http') or not host or parts.username or parts.password:
        raise ValueError('需要不含账号密码的 HTTP(S) 网址。')
    if host.lower() == 'localhost' or host.lower().endswith('.localhost'):
        raise ValueError('不支持本机地址。')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('不支持私有网络 IP 地址。')
    return url


def page_problem(url: str, text: str, status: int = 200) -> None:
    lowered = text.lower()
    if url.startswith(('chrome-error:', 'chrome://network-error')):
        raise AccessError('network_error', '浏览器显示网络错误页；请检查 VPN、代理或目标站点连接。', retryable=True)
    if ('/sorry/' in url or status == 429 or
            any(s in lowered for s in ('unusual traffic', 'our systems have detected',
                                      '人机验证', '请输入验证码', 'just a moment...', 'checking your browser'))):
        raise AccessError('verification_required', '请在可见浏览器中完成人工验证，再重试。',
                          verification_required=True, url=url, http_status=status)
    if any(s in url.lower() for s in ('/login', 'metaauth.com', '/authserver/', '/idp/profile/')):
        raise AccessError('authentication_required', '请在可见浏览器中完成机构身份认证，再重试。',
                          login_required=True, url=url, http_status=status)
    if ('there was a problem providing the content you requested' in lowered or
            'cpe00001' in lowered):
        raise AccessError('access_denied', '出版商返回访问失败页；请在可见浏览器中验证身份或检查机构权限。',
                          url=url, http_status=status)
    if status in (401, 403):
        raise AccessError('access_denied', '站点拒绝访问；请检查机构权限或在浏览器中完成验证。',
                          url=url, http_status=status)
    if status >= 400:
        raise AccessError('http_error', f'站点返回 HTTP {status}。', url=url, http_status=status)


# This script reads ordinary article metadata and links. It does not execute site
# callbacks, submit forms, click purchase buttons, or rewrite institutional URLs.
PAGE_DATA = r'''() => {
 const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({
   text:(a.innerText || a.getAttribute('aria-label') || '').trim(), url:a.href
 })).filter(a => /^https?:/.test(a.url));
 const meta = {};
 document.querySelectorAll('meta[name],meta[property]').forEach(m => {
   const key=m.getAttribute('name') || m.getAttribute('property');
   if (/^(citation_|dc\.|og:)/i.test(key)) (meta[key] ||= []).push(m.content);
 });
 const pdfs = [...links.filter(a => /pdf|下载全文|全文下载/i.test(a.text) || /\.pdf(?:[?#]|$)/i.test(a.url)).map(a=>a.url),
   ...(meta.citation_pdf_url || [])];
 return {title:document.title, text:(document.body?.innerText || '').slice(0,20000),
   metadata:meta, links:links.slice(0,150), pdf_urls:[...new Set(pdfs.map(u=>new URL(u,location.href).href))]};
}'''


class WebSession:
    def __init__(self, config: AppConfig, name: str, home: str):
        settings = config.for_provider(name)
        self.name, self.home = name, web_url(home)
        self.data = config.provider_data_dir(name)
        self.output = config.provider_output_dir(name)
        self.channel = settings.get('BROWSER_CHANNEL') or config.browser_channel
        self.headless = config.headless and not settings.flag('SHOW_BROWSER')
        self.timeout = max(1, min(settings.integer('TIMEOUT', 30), 120))
        self.download_timeout = max(1, min(settings.integer('DOWNLOAD_TIMEOUT', 60), 300))
        self.max_bytes = max(1, settings.integer('MAX_DOWNLOAD_BYTES', 100 * 1024 * 1024))
        self.proxy = settings.get('PROXY')
        self.lock = asyncio.Lock()
        self.profile_lock = InterProcessLock(self.data / '.runtime.lock', timeout=1)
        self.pw = self.context = self.page = None
        self.visible = not self.headless
        self.pending: set[asyncio.Task] = set()
        self.last_search_url = self.home

    def _track(self, task):
        self.pending.add(task)
        def done(t):
            self.pending.discard(t)
            if not t.cancelled():
                t.exception()  # retrieve failures even after a timed-out MCP call
        task.add_done_callback(done)

    async def run(self, operation, *, download=False):
        """Deadline includes lock waits, launch, redirects, parsing and transfer.

        asyncio.wait is intentional: unlike wait_for it cannot wait indefinitely
        for cancellation cleanup. A timed-out task keeps the serial lock until
        cancellation completes, and the next call has its own deadline too.
        """
        budget = self.download_timeout if download else self.timeout
        deadline = asyncio.get_running_loop().time() + budget
        async def locked():
            async with self.lock:
                self.deadline = deadline
                return await operation()
        task = asyncio.create_task(locked())
        try:
            done, _ = await asyncio.wait({task}, timeout=budget)
            if not done:
                task.cancel()
                self._track(task)
                return {'success': False, 'error_code': 'timeout',
                        'timeout_seconds': budget, 'retryable': True,
                        'message': f'操作超过 {budget} 秒，已停止等待；请检查网络、VPN 或代理后重试。'}
            return task.result()
        except asyncio.CancelledError:
            task.cancel()
            self._track(task)
            raise
        except AccessError as error:
            return {'success': False, 'error_code': error.code,
                    'message': str(error), **error.details}
        except (BrowserTimeout, httpx.TimeoutException):
            return {'success': False, 'error_code': 'timeout', 'timeout_seconds': budget,
                    'retryable': True, 'message': '站点响应超时；请检查网络、VPN 或代理后重试。'}
        except (BrowserError, httpx.HTTPError) as error:
            # Do not expose exception strings: proxy credentials and session URLs
            # can occur in Playwright / HTTP diagnostics.
            return {'success': False, 'error_code': 'network_error', 'retryable': True,
                    'message': f'网络或浏览器连接失败（{type(error).__name__}）；请检查网络或代理。'}
        except ValueError as error:
            return {'success': False, 'error_code': 'invalid_argument', 'message': str(error)}
        except Exception as error:
            return {'success': False, 'error_code': 'unexpected_error',
                    'message': f'操作失败（{type(error).__name__}）。'}

    async def browser(self, visible: bool | None = None):
        if visible is not None and self.context is not None and visible != self.visible:
            await self.context.close()
            self.context = self.page = None
        if visible is not None:
            self.visible = visible
        if self.context is None:
            if not self.profile_lock.held and not await self.profile_lock.acquire():
                raise AccessError('browser_busy', '该数据源浏览器正由另一个进程使用；请先关闭另一会话。')
            self.data.mkdir(parents=True, exist_ok=True)
            if self.pw is None:
                self.pw = await async_playwright().start()
            channels = list(plat.browser_candidates()) if self.channel == 'auto' else [self.channel]
            last = None
            for channel in channels:
                options = {'headless': not self.visible, 'accept_downloads': True,
                           'locale': 'zh-CN'}
                if channel != 'chromium':
                    options['channel'] = channel
                if self.proxy:
                    options['proxy'] = {'server': self.proxy}
                try:
                    self.context = await self.pw.chromium.launch_persistent_context(
                        str(self.data / 'profile'), **options)
                    break
                except BrowserError as error:
                    last = error
            if self.context is None:
                raise last or RuntimeError('No browser available')
            state = self.data / 'storage.json'
            if state.exists():
                try:
                    saved = json.loads(state.read_text(encoding="utf-8"))
                    await self.context.add_cookies(saved.get('cookies', []))
                except (ValueError, OSError, BrowserError):
                    pass  # stale state must not prevent a new manual login
            owned = self.context
            def closed(_):
                if self.context is owned:
                    self.context = self.page = None
            owned.on('close', closed)
            self.context.set_default_timeout(self.timeout * 1000)
            self.context.set_default_navigation_timeout(self.timeout * 1000)
        if self.page is None or self.page.is_closed():
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self.page

    async def navigate(self, url):
        page = await self.browser()
        response = await page.goto(web_url(url), wait_until='domcontentloaded')
        text = await page.locator('body').inner_text(timeout=min(self.timeout, 5) * 1000)
        page_problem(page.url, text, response.status if response else 200)
        return page

    async def open_login(self, url: str = ''):
        async def op():
            page = await self.browser(visible=True)
            # A verification page already open must survive this call.
            target = web_url(url) if url else self.last_search_url
            if url or page.url == 'about:blank':
                await page.goto(target, wait_until='domcontentloaded')
            return {'success': True, 'opened': True, 'url': page.url,
                    'message': '请在此窗口完成身份认证或人工验证，然后调用 finish_login。'}
        return await self.run(op)

    async def status(self):
        async def op():
            page = await self.navigate(self.home)
            return {'success': True, 'reachable': True, 'url': page.url,
                    'logged_in': None, 'institution_access': 'not_verified',
                    'message': '搜索入口可访问；全文权限须以目标论文实际访问和下载结果为准。'}
        return await self.run(op)

    async def finish_login(self):
        async def op():
            if self.context is None:
                raise AccessError('authentication_required', '请先调用 open_login_page 打开认证窗口。')
            # SSO may have opened a new tab. Select the most recent live page.
            pages = [p for p in self.context.pages if not p.is_closed()]
            if pages:
                self.page = pages[-1]
            page = await self.browser()
            text = await page.locator('body').inner_text()
            page_problem(page.url, text)
            if page.url == 'about:blank' or len(text.strip()) < 20:
                raise AccessError('authentication_required', '页面尚未完成加载或认证，请在窗口中完成后再试。')
            if await page.locator('input[type=password]:visible').count():
                raise AccessError('authentication_required', '页面仍在等待登录，请先完成认证。')
            await self._save_state()
            return {'success': True, 'session_saved': True, 'logged_in': None,
                    'institution_access': 'not_verified', 'url': page.url,
                    'message': '当前浏览器会话已保存在本机；请重试检索或下载以验证权限。'}
        return await self.run(op)

    async def read_page(self, url: str):
        async def op():
            page = await self.navigate(url)
            await page.wait_for_function("""() =>
                (document.body?.innerText || '').trim().length > 100 ||
                document.querySelector('meta[name="citation_title"],input[type="password"]')""",
                timeout=min(self.timeout, 12) * 1000)
            page_problem(page.url, await page.locator('body').inner_text())
            pending_pdf = False
            # ScienceDirect renders its PDF anchor before asynchronously adding
            # href. Wait for that observed state, never invent a /pdfft URL.
            if await page.locator('a[aria-label^="View PDF"][aria-disabled="true"]').count():
                try:
                    await page.locator('a[aria-label^="View PDF"][href]').first.wait_for(
                        state='attached', timeout=min(self.timeout, 10) * 1000)
                except BrowserTimeout:
                    pending_pdf = True
            data = await page.evaluate(PAGE_DATA)
            data['pdf_link_pending'] = pending_pdf
            return {'success': True, 'url': page.url, **data,
                    'content_kind': 'web_page', 'fulltext_verified': False}
        return await self.run(op)

    def _pdf_path(self, title, output_dir):
        target = Path(output_dir).expanduser().resolve() if output_dir else self.output
        target.mkdir(parents=True, exist_ok=True)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title or 'paper').strip(' .')[:120] or 'paper'
        if name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}:
            name = 'paper_' + name
        path = target / (name.removesuffix('.pdf') + '.pdf')
        for i in range(1, 10000):
            if not path.exists():
                break
            path = target / f'{name} ({i}).pdf'
        else:
            raise ValueError('无法分配下载文件名。')
        return path

    async def _stream_pdf(self, url, title, output_dir):
        """Stream with browser cookies, retaining exact library resolver URLs."""
        page = await self.browser()
        cookies = httpx.Cookies()
        for c in await self.context.cookies():
            cookies.set(c['name'], c['value'], domain=c['domain'], path=c['path'])
        headers = {'User-Agent': await page.evaluate('navigator.userAgent')}
        # Referer only for same-origin requests; never leak a session URL to a publisher.
        if urlsplit(page.url).netloc == urlsplit(url).netloc:
            headers['Referer'] = page.url
        async with httpx.AsyncClient(cookies=cookies, headers=headers, proxy=self.proxy or None,
                                     timeout=min(self.download_timeout, 30), follow_redirects=True,
                                     max_redirects=10) as client:
            async with client.stream('GET', web_url(url)) as response:
                web_url(str(response.url))
                if response.status_code >= 400:
                    page_problem(str(response.url), '', response.status_code)
                content_length = response.headers.get('content-length', '')
                if content_length.isdigit() and int(content_length) > self.max_bytes:
                    raise AccessError('file_too_large', '文件超过配置的下载大小上限。')
                chunks = response.aiter_bytes(64 * 1024)
                head = await anext(chunks, b'')
                if not head.startswith(b'%PDF-'):
                    text = head.decode('utf-8', 'replace')
                    page_problem(str(response.url), text, response.status_code)
                    raise AccessError('pdf_link_required', '返回内容不是 PDF；请先用 read_page 打开论文或机构跳转页，取得实际 PDF 链接；需要认证时用 open_login_page。',
                                      url=url, resolved_url=str(response.url))
                path = self._pdf_path(title, output_dir)
                target = path.parent
                temp = target / ('.' + uuid.uuid4().hex + '.part')
                size, digest = 0, hashlib.sha256()
                try:
                    with temp.open('xb') as f:
                        for chunk in (head,):
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise AccessError('file_too_large', '文件超过配置的下载大小上限。')
                            digest.update(chunk); f.write(chunk)
                        async for chunk in chunks:
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise AccessError('file_too_large', '文件超过配置的下载大小上限。')
                            digest.update(chunk); f.write(chunk)
                    # Same-directory hard link atomically publishes a complete
                    # file and fails if another process already owns the name.
                    os.link(temp, path)
                finally:
                    temp.unlink(missing_ok=True)
                return {'success': True, 'file_path': str(path), 'bytes': size,
                        'sha256': digest.hexdigest(), 'format': 'pdf',
                        'source_url': url, 'resolved_url': str(response.url)}

    async def _browser_pdf(self, url, title, output_dir):
        """A same-origin browser fetch for publishers that reject HTTP clients.

        The browser supplies its normal credentials and network stack. CORS and
        access checks remain enabled, and bytes are capped while streaming.
        """
        page = await self.browser()
        if urlsplit(page.url).netloc != urlsplit(url).netloc:
            raise AccessError('pdf_link_required', '请先用 read_page 打开该 PDF 所属的机构或出版商页面，再重试下载。')
        remaining = max(1, int((self.deadline - asyncio.get_running_loop().time()) * 1000))
        result = await page.evaluate(r"""async ({url,limit,timeout}) => {
            const abort=new AbortController();
            const timer=setTimeout(()=>abort.abort(),timeout);
            let reader;
            try {
                const r=await fetch(url,{credentials:'include',signal:abort.signal});
                if(!r.ok) return {status:r.status,url:r.url};
                const n=Number(r.headers.get('content-length'));
                if(n>limit) {abort.abort();return {too_large:true};}
                reader=r.body.getReader();const chunks=[];let size=0;
                let prefix=[];
                for(;;){
                    const {done,value}=await reader.read();if(done) break;
                    size+=value.length;
                    if(size>limit){await reader.cancel();return {too_large:true};}
                    if(prefix.length<5){
                        prefix.push(...value.slice(0,5-prefix.length));
                        if(prefix.length===5 && String.fromCharCode(...prefix)!=='%PDF-'){
                            const text=new TextDecoder().decode(value.slice(0,4096));
                            await reader.cancel();return {not_pdf:true,status:r.status,url:r.url,text};
                        }
                    }
                    chunks.push(value);
                }
                let binary='';
                for(const chunk of chunks)
                    for(let i=0;i<chunk.length;i+=8192)
                        binary+=String.fromCharCode(...chunk.subarray(i,i+8192));
                return {status:r.status,url:r.url,data:btoa(binary),size};
            } catch(e){return {failure:e.name};}
            finally {clearTimeout(timer);if(reader) reader.releaseLock();}
        }""", {'url': web_url(url), 'limit': self.max_bytes, 'timeout': remaining})
        if result.get('failure'):
            code = 'timeout' if result['failure'] == 'AbortError' else 'network_error'
            raise AccessError(code, '浏览器下载失败；请检查网络或在可见窗口完成验证。')
        if result.get('too_large'):
            raise AccessError('file_too_large', '文件超过配置的下载大小上限。')
        page_problem(result.get('url', url), result.get('text', ''), result.get('status', 200))
        data = base64.b64decode(result.get('data', ''))
        if len(data) > self.max_bytes:
            raise AccessError('file_too_large', '文件超过配置的下载大小上限。')
        if not data.startswith(b'%PDF-'):
            raise AccessError('pdf_link_required', '浏览器也未收到 PDF；请检查下载链接或完成身份验证。')
        path = self._pdf_path(title, output_dir)
        temp = path.parent / ('.' + uuid.uuid4().hex + '.part')
        try:
            temp.write_bytes(data)
            os.link(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        return {'success': True, 'file_path': str(path), 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest(), 'format': 'pdf',
                'source_url': url, 'resolved_url': result['url'], 'transfer': 'browser'}

    async def download_pdf(self, url: str, title: str = '', output_dir: str = ''):
        async def op():
            try:
                return await self._stream_pdf(url, title, output_dir)
            except AccessError as error:
                if error.code not in ('access_denied', 'pdf_link_required'):
                    raise
                page = await self.browser()
                if urlsplit(page.url).netloc != urlsplit(url).netloc:
                    raise error
                return await self._browser_pdf(url, title, output_dir)
        return await self.run(op, download=True)

    async def _save_state(self):
        state = self.data / 'storage.json'
        await self.context.storage_state(path=str(state))
        if os.name != 'nt':
            state.chmod(0o600)

    async def _close(self):
        try:
            if self.context is not None:
                try:
                    await self._save_state()
                finally:
                    await self.context.close()
        finally:
            self.context = self.page = None
            try:
                if self.pw is not None:
                    await self.pw.stop()
            finally:
                self.pw = None
                self.profile_lock.release()

    async def close_browser(self):
        async def op():
            await self._close()
            return {'success': True, 'closed': True}
        return await self.run(op)

    async def aclose(self):
        for task in list(self.pending):
            task.cancel()
        task = asyncio.create_task(self._close())
        done, _ = await asyncio.wait({task}, timeout=3)
        if not done:
            task.cancel()
            self._track(task)
