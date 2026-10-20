"""Bounded HTTP without proxies, automatic redirects, cookies or payload logging."""

import asyncio
import http.client
import ipaddress
import json
import math
import os
import socket
import ssl
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Awaitable, Callable, Mapping, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

import certifi


class FailureCategory(StrEnum):
    DNS = "dns_failure"
    TLS = "tls_failure"
    NETWORK = "network_failure"
    TIMEOUT = "timeout"
    UNSAFE_DESTINATION = "unsafe_destination"
    RESPONSE_TOO_LARGE = "response_too_large"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    PROTOCOL = "http_protocol_failure"
    INCOMPLETE_RESPONSE = "incomplete_response"
    REQUEST_LIMIT = "request_limit"
    AUTHENTICATION = "authentication_http_status"
    RATE_LIMIT = "rate_limited"
    BILLING = "billing_http_status"
    REDIRECT = "redirect_rejected"
    PROVIDER = "provider_http_status"
    HTTP_STATUS = "unexpected_http_status"
    PARSING = "response_parsing"
    VALIDATION = "response_validation"
    INTERNAL = "internal_error"


class TransportStage(StrEnum):
    WORKER_QUEUE = "worker_queue"
    DNS = "dns"
    TCP = "tcp_connect"
    TLS = "tls_handshake"
    SEND = "send_request"
    HEADERS = "response_headers"
    BODY = "response_body"


class TransportProgress:
    """Per-request, thread-safe diagnostics containing only fixed stages/status."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stage: TransportStage | None = None
        self._status: int | None = None

    def update(self, stage: TransportStage, status: int | None = None) -> None:
        if not isinstance(stage, TransportStage):
            raise ValueError("Invalid transport stage")
        with self._lock:
            self._stage = stage
            if type(status) is int and 100 <= status <= 599:
                self._status = status

    def snapshot(self) -> tuple[TransportStage | None, int | None]:
        with self._lock:
            return self._stage, self._status


def status_category(status: int) -> FailureCategory:
    if status in (401, 403):
        return FailureCategory.AUTHENTICATION
    if status == 402:
        return FailureCategory.BILLING
    if status == 429:
        return FailureCategory.RATE_LIMIT
    if 300 <= status < 400:
        return FailureCategory.REDIRECT
    if 500 <= status < 600:
        return FailureCategory.PROVIDER
    return FailureCategory.HTTP_STATUS


class HTTPError(RuntimeError):
    """Safe diagnostics only: never include URLs, headers or upstream bodies."""

    default_category = FailureCategory.PROTOCOL

    def __init__(self, message: str = "", *, category: FailureCategory | None = None,
                 http_status: int | None = None, stage: TransportStage | None = None):
        super().__init__(message)
        self.category = category if isinstance(category, FailureCategory) else self.default_category
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.stage = stage if isinstance(stage, TransportStage) else None


class UnsafeURL(HTTPError):
    default_category = FailureCategory.UNSAFE_DESTINATION


class TransportError(HTTPError):
    default_category = FailureCategory.NETWORK


class ResponseTooLarge(HTTPError):
    default_category = FailureCategory.RESPONSE_TOO_LARGE


class RequestLimitExceeded(HTTPError):
    default_category = FailureCategory.REQUEST_LIMIT


def _transport_category(error: BaseException) -> FailureCategory:
    """Classify by type only; never parse or retain an upstream error message."""
    if isinstance(error, TimeoutError):
        return FailureCategory.TIMEOUT
    if isinstance(error, socket.gaierror):
        return FailureCategory.DNS
    if isinstance(error, ssl.SSLError):
        return FailureCategory.TLS
    if isinstance(error, http.client.HTTPException):
        return FailureCategory.PROTOCOL
    return FailureCategory.NETWORK


def positive_int(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (address.is_global and not address.is_multicast
            and not address.is_unspecified and not address.is_reserved
            and not getattr(address, "ipv4_mapped", None)
            and not (address.version == 6 and address in ipaddress.ip_network("64:ff9b::/96"))
            and not (address.version == 6 and (address.sixtofour or address.teredo)))


def validate_url(url: str) -> str:
    """Normalize an HTTP(S) URL. DNS results are checked and pinned at transport."""
    if (not isinstance(url, str) or not url or len(url) > 8192
            or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url):
        raise UnsafeURL("URL is malformed")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme not in {"http", "https"} or not host
                or parts.username is not None or parts.password is not None
                or "%" in host):
            raise ValueError
        port = parts.port
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            raise ValueError
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        if (not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))
                or host in {"metadata.google.internal", "metadata"}):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if ":" in host or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in host):
                raise ValueError
        else:
            if not public_ip(host):
                raise ValueError
        authority = f"[{host}]" if ":" in host else host
        # Fragments are not sent to servers. Preserve query values without decoding.
        return urlunsplit((parts.scheme, authority, quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                           quote(parts.query, safe="/%?:@!$&'()*+,;=-._~"), ""))
    except (ValueError, UnicodeError):
        raise UnsafeURL("URL destination is not permitted") from None


@dataclass(frozen=True)
class RequestPolicy:
    timeout_seconds: float = 20.0
    max_retries: int = 2
    backoff_seconds: float = 0.5
    max_retry_delay_seconds: float = 5.0
    max_request_bytes: int = 256_000
    max_response_bytes: int = 2_000_000
    max_requests: int = 100
    max_concurrency: int = 4

    def __post_init__(self) -> None:
        for name in ("timeout_seconds", "backoff_seconds", "max_retry_delay_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.timeout_seconds == 0:
            raise ValueError("timeout_seconds must be positive")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        for name in ("max_request_bytes", "max_response_bytes", "max_requests", "max_concurrency"):
            positive_int(getattr(self, name), name)


@dataclass(frozen=True)
class HTTPRequest:
    method: str
    url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    body: bytes = field(default=b"", repr=False)
    timeout_seconds: float = 20.0
    max_response_bytes: int = 2_000_000
    progress: TransportProgress | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    body: bytes = field(default=b"", repr=False)

    def header(self, name: str) -> str | None:
        values = [v for k, v in self.headers if k.lower() == name.lower()]
        if len(values) > 1:
            raise HTTPError("Ambiguous response headers")
        return values[0] if values else None


class HTTPTransport(Protocol):
    async def send(self, request: HTTPRequest) -> HTTPResponse:
        """One exchange only. Implementations must enforce the supplied limits."""
        ...


Resolver = Callable[[str, int], tuple[str, ...]]


def system_resolver(host: str, port: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM)))


DNS_RESOLVER_ENV = "DEEPDIVE_DNS_RESOLVER"
# Fixed IP-literal JSON endpoints; certificates are verified against these IPs.
DOH_ENDPOINTS: tuple[tuple[str, str], ...] = (("1.1.1.1", "/dns-query"), ("8.8.8.8", "/resolve"))
DOH_ATTEMPT_SECONDS = 2.5
DOH_MAX_RESPONSE_BYTES = 65_536
DOH_MAX_ANSWERS = 32
DOH_CACHE_MAX_TTL_SECONDS = 300
DOH_CACHE_MAX_ENTRIES = 64


class DoHCache:
    """Bounded, thread-safe LRU of successful, all-public DoH answers.

    Entries expire at the answer's minimum TTL, capped at DOH_CACHE_MAX_TTL_SECONDS.
    Failures and answers containing any non-public address are never stored, and
    hits are revalidated against public_ip before use.
    """

    def __init__(self, max_entries: int = DOH_CACHE_MAX_ENTRIES,
                 max_ttl_seconds: float = DOH_CACHE_MAX_TTL_SECONDS):
        positive_int(max_entries, "max_entries")
        if type(max_ttl_seconds) not in (int, float) or not math.isfinite(max_ttl_seconds) or max_ttl_seconds <= 0:
            raise ValueError("max_ttl_seconds must be finite and positive")
        self._max_entries = max_entries
        self._max_ttl = max_ttl_seconds
        self._entries: OrderedDict[str, tuple[float, tuple[str, ...]]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, host: str, now: float) -> tuple[str, ...] | None:
        with self._lock:
            entry = self._entries.get(host)
            if entry is None:
                return None
            expires, addresses = entry
            if now >= expires or not addresses or any(not public_ip(ip) for ip in addresses):
                del self._entries[host]
                return None
            self._entries.move_to_end(host)
            return addresses

    def put(self, host: str, addresses: tuple[str, ...], ttl: int, now: float) -> None:
        if type(ttl) is not int or ttl <= 0 or not addresses or any(not public_ip(ip) for ip in addresses):
            return
        with self._lock:
            for key in [key for key, (expires, _) in self._entries.items() if now >= expires]:
                del self._entries[key]
            self._entries[host] = (now + min(ttl, self._max_ttl), tuple(addresses))
            self._entries.move_to_end(host)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


class _DoHFailure(Exception):
    """Internal only; never carries provider detail outside the resolver."""


def _doh_connection(address: str, timeout: float) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(address, 443, timeout=timeout,
                                       context=ssl.create_default_context(cafile=certifi.where()))


def _plain_int(value: object) -> bool:
    return type(value) is int


class DoHResolver:
    """IPv4 lookups over DNS-over-HTTPS JSON with one fallback endpoint.

    Endpoints are fixed IP literals, so no system DNS is used. Each attempt is
    bounded by a deadline timer and a response byte cap. Answers are returned to
    PublicHTTPTransport, which still requires every address to be public.
    """

    def __init__(self, connect: Callable[[str, float], http.client.HTTPConnection] = _doh_connection,
                 clock: Callable[[], float] = time.monotonic, cache: DoHCache | None = None):
        self._connect = connect
        self._clock = clock
        self._cache = cache if cache is not None else DoHCache()

    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        try:
            return (str(ipaddress.ip_address(host)),)
        except ValueError:
            pass
        if (not isinstance(host, str) or not 0 < len(host) <= 253
                or any(not 0 < len(label) <= 63 for label in host.split("."))
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-." for c in host)):
            raise socket.gaierror(socket.EAI_NONAME, "DNS-over-HTTPS name rejected")
        cached = self._cache.get(host, self._clock())
        if cached is not None:
            return cached
        timeouts = 0
        for address, path in DOH_ENDPOINTS:
            try:
                addresses, ttl = self._query(address, path, host)
                self._cache.put(host, addresses, ttl, self._clock())
                return addresses
            except TimeoutError:
                timeouts += 1
            except (_DoHFailure, OSError, http.client.HTTPException, ValueError, RecursionError):
                pass
        if timeouts == len(DOH_ENDPOINTS):
            raise TimeoutError("DNS-over-HTTPS resolution timed out")
        raise socket.gaierror(socket.EAI_FAIL, "DNS-over-HTTPS resolution failed")

    def _query(self, address: str, path: str, host: str) -> tuple[tuple[str, ...], int]:
        deadline = self._clock() + DOH_ATTEMPT_SECONDS
        connection = self._connect(address, DOH_ATTEMPT_SECONDS)
        expired = threading.Event()

        def abort() -> None:
            expired.set()
            active = connection.sock
            if active is not None:
                try:
                    active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                active.close()

        timer = threading.Timer(DOH_ATTEMPT_SECONDS, abort)
        timer.daemon = True
        timer.start()
        response = None
        try:
            connection.request("GET", f"{path}?name={quote(host, safe='')}&type=A", headers={
                "Accept": "application/dns-json", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status != 200:
                raise _DoHFailure
            if (response.getheader("content-encoding") or "identity").lower() != "identity":
                raise _DoHFailure
            length = response.getheader("content-length")
            if length is not None and (not length.isascii() or not length.isdecimal()
                                       or len(length) > 20 or int(length) > DOH_MAX_RESPONSE_BYTES):
                raise _DoHFailure
            chunks: list[bytes] = []
            size = 0
            while True:
                if self._clock() >= deadline or expired.is_set():
                    raise TimeoutError
                chunk = response.read1(DOH_MAX_RESPONSE_BYTES + 1 - size)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > DOH_MAX_RESPONSE_BYTES:
                    raise _DoHFailure
            if self._clock() >= deadline or expired.is_set():
                raise TimeoutError
            if length is not None and size != int(length):
                raise _DoHFailure
            return self._parse(b"".join(chunks), host)
        except (OSError, http.client.HTTPException):
            if expired.is_set():
                raise TimeoutError from None
            raise
        finally:
            timer.cancel()
            if response is not None:
                response.close()
            connection.close()

    @staticmethod
    def _parse(body: bytes, host: str) -> tuple[tuple[str, ...], int]:
        """Return validated A addresses and the minimum record TTL (0 = do not cache)."""
        def reject_constant(_: str) -> None:
            raise _DoHFailure

        data = json.loads(body.decode("utf-8"), parse_constant=reject_constant)
        if (not isinstance(data, dict) or not _plain_int(data.get("Status")) or data["Status"] != 0
                or data.get("TC") is not False):
            raise _DoHFailure
        question = data.get("Question")
        if (not isinstance(question, list) or not any(
                isinstance(q, dict) and isinstance(q.get("name"), str)
                and q["name"].rstrip(".").lower() == host and q.get("type") == 1 and _plain_int(q.get("type"))
                for q in question)):
            raise _DoHFailure
        answers = data.get("Answer")
        if not isinstance(answers, list) or not 0 < len(answers) <= DOH_MAX_ANSWERS:
            raise _DoHFailure
        addresses = []
        ttls = []
        for answer in answers:
            if not isinstance(answer, dict) or not _plain_int(answer.get("type")):
                raise _DoHFailure
            # A missing or invalid TTL never fails the lookup; it only disables caching.
            ttl = answer.get("TTL")
            ttls.append(ttl if _plain_int(ttl) and ttl >= 0 else 0)
            if answer["type"] != 1:
                continue
            value = answer.get("data")
            if not isinstance(value, str):
                raise _DoHFailure
            try:
                addresses.append(str(ipaddress.IPv4Address(value)))
            except ValueError:
                raise _DoHFailure from None
        if not addresses:
            raise _DoHFailure
        return tuple(dict.fromkeys(addresses)), min(ttls)


_PROCESS_DOH_CACHE = DoHCache()


def resolver_from_environment(environment: Mapping[str, str] | None = None) -> Resolver:
    """`system` (default) or `doh`; any other value is rejected without echoing it."""
    values = os.environ if environment is None else environment
    choice = values.get(DNS_RESOLVER_ENV, "system")
    if choice == "system":
        return system_resolver
    if choice == "doh":
        return DoHResolver(cache=_PROCESS_DOH_CACHE)
    raise ValueError(f"{DNS_RESOLVER_ENV} must be 'system' or 'doh'")


class _PinnedConnection(http.client.HTTPConnection):
    """Connect directly to a validated IP; retain original Host and TLS identity."""

    def __init__(self, host: str, port: int, address: str, timeout: float, tls: bool):
        super().__init__(host, port, timeout=timeout)
        self.address = address
        self.tls = tls
        self.progress: TransportProgress | None = None

    def connect(self) -> None:
        raw = socket.socket(socket.AF_INET6 if ":" in self.address else socket.AF_INET, socket.SOCK_STREAM)
        self.sock = raw
        try:
            raw.settimeout(self.timeout)
            if self.progress is not None:
                self.progress.update(TransportStage.TCP)
            raw.connect((self.address, self.port))
            if self.tls and self.progress is not None:
                self.progress.update(TransportStage.TLS)
            self.sock = (ssl.create_default_context(cafile=certifi.where()).wrap_socket(raw, server_hostname=self.host)
                         if self.tls else raw)
        except BaseException:
            raw.close()
            raise


class PublicHTTPTransport:
    """TLS verification uses certifi. Every exchange resolves once and pins one IP.

    OS DNS lookup runs in a worker, bounded from the async caller by wait_for.
    A late DNS result is discarded before opening a connection using the deadline.
    """

    def __init__(self, resolver: Resolver | None = None):
        self._resolver = resolver if resolver is not None else resolver_from_environment()

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        if request.progress is None:
            request = HTTPRequest(request.method, request.url, request.headers, request.body,
                                  request.timeout_seconds, request.max_response_bytes, TransportProgress())
        request.progress.update(TransportStage.WORKER_QUEUE)
        deadline = time.monotonic() + request.timeout_seconds
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._send, request, deadline), request.timeout_seconds)
        except (TimeoutError, OSError, http.client.HTTPException) as error:
            stage, status = request.progress.snapshot()
            raise TransportError("HTTP transport failed or timed out", category=_transport_category(error),
                                 stage=stage, http_status=status) from None

    def _send(self, request: HTTPRequest, deadline: float) -> HTTPResponse:
        url = validate_url(request.url)
        parts = urlsplit(url)
        host = parts.hostname
        assert host is not None
        port = 443 if parts.scheme == "https" else 80
        progress = request.progress
        if progress is not None:
            progress.update(TransportStage.DNS)
        try:
            addresses = self._resolver(host, port)
        except TimeoutError:
            raise TransportError("DNS resolution timed out", category=FailureCategory.TIMEOUT, stage=TransportStage.DNS) from None
        except OSError:
            raise TransportError("DNS resolution failed", category=FailureCategory.DNS, stage=TransportStage.DNS) from None
        if not addresses or any(not public_ip(ip) for ip in addresses):
            raise UnsafeURL("DNS destination is not public")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransportError("HTTP deadline exceeded", category=FailureCategory.TIMEOUT)
        connection = _PinnedConnection(host, port, addresses[0], remaining, parts.scheme == "https")
        connection.progress = progress
        response = None
        wire = None

        def abort() -> None:
            active = connection.sock if connection.sock is not None else wire
            if active is not None:
                try:
                    active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                active.close()

        timer = threading.Timer(remaining, abort)
        timer.daemon = True
        timer.start()
        try:
            if progress is not None:
                progress.update(TransportStage.TCP)
            connection.connect()
            assert connection.sock is not None
            wire = connection.sock

            def set_timeout() -> None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError("HTTP deadline exceeded", category=FailureCategory.TIMEOUT)
                wire.settimeout(remaining)

            set_timeout()
            if progress is not None:
                progress.update(TransportStage.SEND)
            connection.request(request.method, urlunsplit(("", "", parts.path, parts.query, "")),
                               body=request.body or None, headers=dict(request.headers))
            set_timeout()
            if progress is not None:
                progress.update(TransportStage.HEADERS)
            response = connection.getresponse()
            if progress is not None:
                progress.update(TransportStage.HEADERS, response.status)
            head = HTTPResponse(response.status, tuple(response.getheaders()))
            if sum(len(k) + len(v) + 4 for k, v in head.headers) > 65536:
                raise ResponseTooLarge("HTTP response headers exceed byte limit")
            length = head.header("content-length")
            transfer = head.header("transfer-encoding")
            if transfer not in (None, "chunked") or (transfer is not None and length is not None):
                raise HTTPError("Ambiguous HTTP response framing")
            if length is not None:
                if not length.isascii() or not length.isdecimal():
                    raise HTTPError("Invalid content length")
                if len(length) > 20 or int(length) > request.max_response_bytes:
                    raise ResponseTooLarge("HTTP response exceeds byte limit")
            if head.header("content-encoding") not in (None, "identity"):
                raise HTTPError("Encoded HTTP responses are not supported", category=FailureCategory.UNSUPPORTED_ENCODING)
            chunks: list[bytes] = []
            size = 0
            if progress is not None:
                progress.update(TransportStage.BODY)
            while True:
                set_timeout()
                chunk = response.read1(min(65536, request.max_response_bytes + 1 - size))
                if time.monotonic() >= deadline:
                    raise TransportError("HTTP deadline exceeded", category=FailureCategory.TIMEOUT)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > request.max_response_bytes:
                    raise ResponseTooLarge("HTTP response exceeds byte limit")
            if length is not None and size != int(length):
                raise TransportError("HTTP response was incomplete", category=FailureCategory.INCOMPLETE_RESPONSE)
            return HTTPResponse(head.status, head.headers, b"".join(chunks))
        except HTTPError as error:
            # A failed body read can still have a known HTTP status. No body or
            # headers are attached to the exception.
            if response is not None and type(response.status) is int and 100 <= response.status <= 599:
                error.http_status = response.status
            if progress is not None:
                error.stage = progress.snapshot()[0]
            raise
        except (TimeoutError, OSError, http.client.HTTPException) as error:
            raise TransportError("HTTP transport failed or timed out", category=_transport_category(error),
                                 http_status=response.status if response is not None else None,
                                 stage=progress.snapshot()[0] if progress is not None else None) from None
        finally:
            timer.cancel()
            if response is not None:
                response.close()
            connection.close()


class BoundedHTTPClient:
    """Per-instance lifetime request budget, concurrency, retries and timeouts."""

    RETRY_STATUSES = {408, 429, 500, 502, 503, 504}

    def __init__(self, transport: HTTPTransport, policy: RequestPolicy,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.transport = transport
        self.policy = policy
        self.sleep = sleep
        self.requests_used = 0
        self._semaphore = asyncio.Semaphore(policy.max_concurrency)

    async def exchange(self, method: str, url: str, headers: tuple[tuple[str, str], ...] = (),
                       body: bytes = b"") -> HTTPResponse:
        url = validate_url(url)
        if len(body) > self.policy.max_request_bytes:
            raise RequestLimitExceeded("HTTP request exceeds byte limit")
        for attempt in range(self.policy.max_retries + 1):
            response = None
            async with self._semaphore:
                if self.requests_used >= self.policy.max_requests:
                    raise RequestLimitExceeded("HTTP request budget exhausted")
                self.requests_used += 1
                progress = TransportProgress()
                try:
                    response = await asyncio.wait_for(self.transport.send(HTTPRequest(
                        method, url, headers, body, self.policy.timeout_seconds,
                        self.policy.max_response_bytes, progress)), self.policy.timeout_seconds)
                except (TransportError, TimeoutError) as error:
                    # Retrying ambiguous POST failures can duplicate billable completions.
                    if method != "GET" or attempt == self.policy.max_retries:
                        stage, status = progress.snapshot()
                        raise TransportError("HTTP transport failed or timed out",
                                             category=error.category if isinstance(error, HTTPError) else FailureCategory.TIMEOUT,
                                             http_status=error.http_status if isinstance(error, HTTPError) and error.http_status is not None else status,
                                             stage=error.stage if isinstance(error, HTTPError) and error.stage is not None else stage) from None
            if response is not None:
                if len(response.body) > self.policy.max_response_bytes:
                    raise ResponseTooLarge("HTTP response exceeds byte limit")
                if response.status not in self.RETRY_STATUSES or attempt == self.policy.max_retries:
                    return response
            delay = min(self.policy.max_retry_delay_seconds, self.policy.backoff_seconds * 2 ** attempt)
            if response is not None:
                retry_after = response.header("retry-after")
                if retry_after and retry_after.isascii() and retry_after.isdecimal():
                    delay = (self.policy.max_retry_delay_seconds if len(retry_after) > 9 else
                             min(self.policy.max_retry_delay_seconds, int(retry_after)))
            await self.sleep(delay)
        raise AssertionError("unreachable")
