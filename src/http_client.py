"""Bounded response reads for check-ins and notification endpoints."""
from dataclasses import dataclass
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit
import zlib

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from .settings import HTTP_BODY_LIMIT, HTTP_DEADLINE

exceptions = requests.exceptions
RequestException = requests.RequestException


@dataclass
class Response:
    status_code: int
    text: str
    truncated: bool = False


class _Deadline:
    """Interrupt socket reads, including slow headers and drip-fed bodies."""
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds
        self.lock = threading.Lock()
        self.sockets = []
        self.expired = False
        self.timer = threading.Timer(seconds, self.abort)
        self.timer.daemon = True

    def track(self, sock):
        with self.lock:
            if self.expired:
                sock.close()
                raise requests.Timeout('HTTP 总时长超限')
            self.sockets.append(sock)

    def abort(self):
        with self.lock:
            self.expired = True
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def check(self):
        if self.expired or time.monotonic() >= self.end:
            raise requests.Timeout('HTTP 总时长超限')


class _Session(requests.Session):
    def resolve_redirects(self, *args, **kwargs):
        # Requests' default implementation consumes redirect bodies, even with
        # allow_redirects=False. Handle redirects below without reading them.
        return iter(())


def _adapter(deadline):
    class TimedHTTP(HTTPConnection):
        def connect(self):
            super().connect()
            deadline.track(self.sock)

    class TimedHTTPS(HTTPSConnection):
        def connect(self):
            super().connect()
            deadline.track(self.sock)

    class HTTPPool(HTTPConnectionPool):
        ConnectionCls = TimedHTTP

    class HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = TimedHTTPS

    class TimedAdapter(HTTPAdapter):
        def proxy_manager_for(self, *args, **kwargs):
            manager = super().proxy_manager_for(*args, **kwargs)
            manager.pool_classes_by_scheme = {'http': HTTPPool, 'https': HTTPSPool}
            return manager

    adapter = TimedAdapter(pool_connections=2, pool_maxsize=1, max_retries=0)
    adapter.poolmanager.pool_classes_by_scheme = {'http': HTTPPool, 'https': HTTPSPool}
    return adapter


def _read(response, limit, deadline):
    encoding = response.headers.get('Content-Encoding', '').lower().strip()
    if encoding not in ('', 'identity', 'gzip', 'deflate'):
        raise requests.RequestException(f'不支持的响应压缩格式: {encoding}')
    decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15) if encoding in ('gzip', 'deflate') else None
    output = bytearray()
    wire_bytes = 0
    while len(output) <= limit:
        deadline.check()
        chunk = response.raw.read1(min(4096, limit + 1 - min(wire_bytes, limit)), decode_content=False)
        deadline.check()
        if not chunk:
            break
        wire_bytes += len(chunk)
        if decoder:
            try:
                decoded = decoder.decompress(chunk, limit + 1 - len(output))
                output.extend(decoded)
                # Concatenated gzip members must also count toward the limit.
                while decoder.unused_data and len(output) <= limit:
                    rest = decoder.unused_data
                    decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15)
                    output.extend(decoder.decompress(rest, limit + 1 - len(output)))
            except zlib.error as exc:
                raise requests.RequestException('响应压缩内容无效') from exc
        else:
            output.extend(chunk)
        if wire_bytes > limit or len(output) > limit:
            break
    truncated = len(output) > limit or wire_bytes > limit
    if decoder and not truncated and not decoder.eof:
        raise requests.exceptions.ContentDecodingError('压缩响应未完整结束')
    # Never invoke response.text / apparent_encoding on a whole remote body.
    charset = requests.utils.get_encoding_from_headers(response.headers) or 'utf-8'
    try:
        text = bytes(output[:limit]).decode(charset, errors='replace')
    except LookupError:
        text = bytes(output[:limit]).decode('utf-8', errors='replace')
    if truncated:
        text = text[:4950] + '\n[响应超过读取上限，已截断]'
    return Response(response.status_code, text[:5000], truncated)


def request(method, url, *, timeout=None, body_limit=HTTP_BODY_LIMIT, deadline_seconds=HTTP_DEADLINE, **kwargs):
    deadline = _Deadline(deadline_seconds)
    deadline.timer.start()
    headers = dict(kwargs.pop('headers', None) or {})
    # Avoid unbounded/unsupported content decoding; explicit gzip/deflate is supported.
    if not any(key.lower() == 'accept-encoding' for key in headers):
        headers['Accept-Encoding'] = 'identity'
    kwargs.pop('stream', None)
    kwargs.pop('allow_redirects', None)
    phase_timeout = timeout if timeout is not None else (5, 20)
    with _Session() as session:
        cookies = kwargs.pop('cookies', None)
        if isinstance(cookies, dict):
            for name, value in cookies.items():
                session.cookies.set(name, value, domain=urlsplit(url).hostname, path='/')
        elif cookies:
            session.cookies.update(cookies)
        adapter = _adapter(deadline)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        try:
            for _ in range(6):
                deadline.check()
                with session.request(method, url, headers=headers, timeout=phase_timeout,
                                     stream=True, allow_redirects=False, **kwargs) as response:
                    deadline.check()
                    if response.status_code not in (301, 302, 303, 307, 308) or not response.headers.get('Location'):
                        return _read(response, body_limit, deadline)
                    target = urljoin(url, response.headers['Location'])
                    if session.should_strip_auth(url, target):
                        headers = {k: v for k, v in headers.items() if k.lower() != 'authorization'}
                        kwargs.pop('auth', None)
                    headers = {k: v for k, v in headers.items() if k.lower() != 'cookie'}
                    kwargs.pop('cookies', None)
                    kwargs.pop('params', None)
                    if ((response.status_code in (302, 303) and method.upper() != 'HEAD')
                            or (response.status_code == 301 and method.upper() == 'POST')):
                        method = 'GET'
                        for key in ('data', 'json', 'files'):
                            kwargs.pop(key, None)
                        headers = {k: v for k, v in headers.items() if k.lower() not in ('content-length', 'content-type', 'transfer-encoding')}
                    url = target
            raise requests.TooManyRedirects('重定向超过 5 次')
        finally:
            deadline.timer.cancel()
            deadline.timer.join()


def get(url, **kwargs):
    return request('GET', url, **kwargs)


def post(url, **kwargs):
    return request('POST', url, **kwargs)
