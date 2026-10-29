"""Asynchronous client; same surface as :class:`SearchConsole` with ``await``."""

from __future__ import annotations

import asyncio
import time
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Union,
)

import httpx

from . import _api
from ._api import Call, DateLike, FilterLike, Inspection, Row
from .auth import Credentials, StaticToken
from .client import _backoff, _decode, _error, _Plan, _retryable
from .errors import RateLimited


class AsyncSearchConsole:
    """Async client.

    >>> async with AsyncSearchConsole(creds) as gsc:
    ...     rows = await gsc.query("sc-domain:example.com", days=7, dimensions=["page"])
    """

    def __init__(
        self,
        credentials: Union[Credentials, str],
        *,
        http: Optional[httpx.AsyncClient] = None,
        base_url: str = _api.DEFAULT_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 4,
        backoff: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.credentials = StaticToken(credentials) if isinstance(credentials, str) else credentials
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.max_retries = max_retries
        self.backoff = backoff
        self._own_http = http is None
        self.http = http or httpx.AsyncClient(timeout=timeout)
        self._sleep = sleep
        self._clock = clock
        self.sites = AsyncSites(self)
        self.sitemaps = AsyncSitemaps(self)

    async def aclose(self) -> None:
        if self._own_http:
            await self.http.aclose()

    async def __aenter__(self) -> AsyncSearchConsole:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def request(self, call: Call) -> Any:
        attempt = 0
        reauthed = False
        while True:
            token = await self.credentials.atoken(self.http)
            resp = await self.http.request(
                call.method, self.base_url + call.path, params=call.params, json=call.json,
                headers={"Authorization": f"Bearer {token}", "User-Agent": _api.USER_AGENT})
            if resp.status_code == 401 and not reauthed:
                reauthed = True
                self.credentials.invalidate(token)
                continue
            if resp.status_code < 400:
                return _decode(resp)
            if attempt < self.max_retries and _retryable(resp, call):
                await self._sleep(_backoff(attempt, self.backoff, resp.headers.get("Retry-After")))
                attempt += 1
                continue
            raise _error(resp)

    async def search_analytics(self, site: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return await self.request(_api.search_analytics(site, body))

    async def iter_query(
        self,
        site: str,
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
        dimensions: Sequence[str] = (),
        *,
        days: Optional[int] = None,
        filters: Optional[Sequence[FilterLike]] = None,
        search_type: Optional[str] = None,
        data_state: Optional[str] = None,
        aggregation_type: Optional[str] = None,
        limit: Optional[int] = None,
        split_by: Optional[str] = None,
        page_size: int = _api.MAX_ROW_LIMIT,
    ) -> AsyncIterator[Row]:
        plan = _Plan(site, start, end, dimensions, days=days, filters=filters,
                     search_type=search_type, data_state=data_state,
                     aggregation_type=aggregation_type, limit=limit, split_by=split_by,
                     page_size=page_size)
        seen = 0
        for s, e in plan.ranges():
            start_row = 0
            while True:
                remaining = None if plan.limit is None else plan.limit - seen
                if remaining is not None and remaining <= 0:
                    return
                body = plan.body(s, e, start_row, remaining)
                page = await self.search_analytics(site, body)
                raw = page.get("rows") or []
                for row in _api.parse_rows(plan.dimensions, raw):
                    yield row
                    seen += 1
                    if plan.limit is not None and seen >= plan.limit:
                        return
                if len(raw) < body["rowLimit"]:
                    break
                start_row += len(raw)

    async def query(self, site: str, start: Optional[DateLike] = None,
                    end: Optional[DateLike] = None, dimensions: Sequence[str] = (),
                    **kwargs: Any) -> List[Row]:
        return [r async for r in self.iter_query(site, start, end, dimensions, **kwargs)]

    async def inspect(self, site: str, url: str, *,
                      language: Optional[str] = None) -> Inspection:
        payload = await self.request(_api.inspect_url(site, url, language))
        return _api.parse_inspection(url, payload)

    async def inspect_many(
        self,
        site: str,
        urls: Iterable[str],
        *,
        language: Optional[str] = None,
        per_minute: int = 600,
        skip_errors: bool = False,
    ) -> AsyncIterator[Inspection]:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        gap = 60.0 / per_minute
        last: Optional[float] = None
        for url in urls:
            if last is not None:
                wait = gap - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            last = self._clock()
            try:
                yield await self.inspect(site, url, language=language)
            except RateLimited:
                raise
            except Exception:
                if not skip_errors:
                    raise


class _ANS:
    def __init__(self, client: AsyncSearchConsole) -> None:
        self._c = client


class AsyncSites(_ANS):
    async def list(self) -> List[Dict[str, Any]]:
        return list((await self._c.request(_api.list_sites())).get("siteEntry") or [])

    async def get(self, site: str) -> Dict[str, Any]:
        return await self._c.request(_api.get_site(site))

    async def add(self, site: str) -> None:
        await self._c.request(_api.add_site(site))

    async def delete(self, site: str) -> None:
        await self._c.request(_api.delete_site(site))


class AsyncSitemaps(_ANS):
    async def list(self, site: str, *,
                   sitemap_index: Optional[str] = None) -> List[Dict[str, Any]]:
        resp = await self._c.request(_api.list_sitemaps(site, sitemap_index))
        return list(resp.get("sitemap") or [])

    async def get(self, site: str, feedpath: str) -> Dict[str, Any]:
        return await self._c.request(_api.get_sitemap(site, feedpath))

    async def submit(self, site: str, feedpath: str) -> None:
        await self._c.request(_api.submit_sitemap(site, feedpath))

    async def delete(self, site: str, feedpath: str) -> None:
        await self._c.request(_api.delete_sitemap(site, feedpath))
