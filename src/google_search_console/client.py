"""Synchronous client for the Google Search Console API."""

from __future__ import annotations

import datetime as dt
import random
import time
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import httpx

from . import _api
from ._api import Call, DateLike, FilterLike, Inspection, Row
from .auth import Credentials, StaticToken
from .errors import RateLimited, from_response


def _backoff(attempt: int, base: float, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            pass
    return min(base * (2 ** attempt), 30.0) * (0.5 + random.random() / 2)


def _decode(resp: httpx.Response) -> Any:
    if not resp.content:
        return {}
    return resp.json()


def _error(resp: httpx.Response) -> Exception:
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text
    return from_response(resp.status_code, body)


def _retryable(resp: httpx.Response, call: Call) -> bool:
    if resp.status_code in _api.RETRY_STATUSES:
        return call.idempotent or resp.status_code in (429, 503)
    if resp.status_code == 403:
        # Legacy quota errors come back as 403 with a rate-limit reason; daily quota
        # (quotaExceeded) will not recover by waiting, so only retry the per-minute kind.
        err = _error(resp)
        return isinstance(err, RateLimited) and err.reason != "quotaExceeded"
    return False


class _Plan:
    """Resolved arguments of a Search Analytics query, shared by both clients."""

    def __init__(self, site: str, start: Optional[DateLike], end: Optional[DateLike],
                 dimensions: Sequence[str], *, days: Optional[int],
                 filters: Optional[Sequence[FilterLike]], search_type: Optional[str],
                 data_state: Optional[str], aggregation_type: Optional[str],
                 limit: Optional[int], split_by: Optional[str], page_size: int) -> None:
        self.site = site
        self.start, self.end = _api.date_range(start, end, days)
        dims = list(dimensions)
        if split_by not in (None, "day"):
            raise ValueError("split_by must be None or 'day'")
        if split_by == "day" and "date" not in dims:
            dims.insert(0, "date")
        self.dimensions = _api.check_dimensions(dims)
        self.split = split_by == "day"
        self.kw: Dict[str, Any] = dict(filters=filters, search_type=search_type,
                                       data_state=data_state, aggregation_type=aggregation_type)
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")
        self.limit = limit
        self.page_size = page_size
        _api.query_body(self.start, self.end, self.dimensions, row_limit=page_size, **self.kw)

    def ranges(self) -> List[Tuple[dt.date, dt.date]]:
        if self.split:
            return [(d, d) for d in _api.each_day(self.start, self.end)]
        return [(self.start, self.end)]

    def body(self, s: dt.date, e: dt.date, start_row: int,
             remaining: Optional[int]) -> Dict[str, Any]:
        size = self.page_size if remaining is None else max(1, min(self.page_size, remaining))
        return _api.query_body(s, e, self.dimensions, row_limit=size, start_row=start_row,
                               **self.kw)


class SearchConsole:
    """Blocking client.

    >>> from google_search_console import SearchConsole, OAuthCredentials
    >>> gsc = SearchConsole(OAuthCredentials.load("token.json"))
    >>> for row in gsc.query("sc-domain:example.com", days=28, dimensions=["query"]):
    ...     print(row.query, row.clicks)
    """

    def __init__(
        self,
        credentials: Union[Credentials, str],
        *,
        http: Optional[httpx.Client] = None,
        base_url: str = _api.DEFAULT_BASE_URL,
        timeout: float = 60.0,
        max_retries: int = 4,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.credentials = StaticToken(credentials) if isinstance(credentials, str) else credentials
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.max_retries = max_retries
        self.backoff = backoff
        self._own_http = http is None
        self.http = http or httpx.Client(timeout=timeout)
        self._sleep = sleep
        self._clock = clock
        self.sites = Sites(self)
        self.sitemaps = Sitemaps(self)

    def close(self) -> None:
        if self._own_http:
            self.http.close()

    def __enter__(self) -> SearchConsole:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- transport ---------------------------------------------------------------
    def request(self, call: Call) -> Any:
        attempt = 0
        reauthed = False
        while True:
            token = self.credentials.token(self.http)
            resp = self.http.request(
                call.method, self.base_url + call.path, params=call.params, json=call.json,
                headers={"Authorization": f"Bearer {token}", "User-Agent": _api.USER_AGENT})
            if resp.status_code == 401 and not reauthed:
                reauthed = True
                self.credentials.invalidate(token)
                continue
            if resp.status_code < 400:
                return _decode(resp)
            if attempt < self.max_retries and _retryable(resp, call):
                self._sleep(_backoff(attempt, self.backoff, resp.headers.get("Retry-After")))
                attempt += 1
                continue
            raise _error(resp)

    # -- search analytics --------------------------------------------------------
    def search_analytics(self, site: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """One raw ``searchAnalytics.query`` call (no pagination)."""
        return self.request(_api.search_analytics(site, body))

    def iter_query(
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
    ) -> Iterator[Row]:
        """Like :meth:`query` but yields rows lazily, one API page at a time."""
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
                page = self.search_analytics(site, body)
                raw = page.get("rows") or []
                for row in _api.parse_rows(plan.dimensions, raw):
                    yield row
                    seen += 1
                    if plan.limit is not None and seen >= plan.limit:
                        return
                if len(raw) < body["rowLimit"]:
                    break
                start_row += len(raw)

    def query(self, site: str, start: Optional[DateLike] = None,
              end: Optional[DateLike] = None, dimensions: Sequence[str] = (),
              **kwargs: Any) -> List[Row]:
        """Search Analytics rows for ``[start, end]`` (inclusive, Pacific-time dates).

        Pages through the 25,000-row limit automatically. ``days=28`` counts back from
        yesterday. ``filters`` accepts strings such as ``"query contains buy"`` or
        ``"country == tur"``. ``split_by="day"`` runs one query per day, which
        returns more long-tail rows on large sites (``date`` is added to the
        dimensions). ``limit`` caps the total number of rows.
        """
        return list(self.iter_query(site, start, end, dimensions, **kwargs))

    # -- URL inspection ----------------------------------------------------------
    def inspect(self, site: str, url: str, *, language: Optional[str] = None) -> Inspection:
        """Index status of one URL (``urlInspection.index.inspect``)."""
        return _api.parse_inspection(url, self.request(_api.inspect_url(site, url, language)))

    def inspect_many(
        self,
        site: str,
        urls: Iterable[str],
        *,
        language: Optional[str] = None,
        per_minute: int = 600,
        skip_errors: bool = False,
    ) -> Iterator[Inspection]:
        """Inspect many URLs, spacing calls to stay under ``per_minute`` (the API
        allows 600 per minute and 2,000 per day per property). With
        ``skip_errors=True`` failed URLs are skipped instead of raising, except for
        quota errors, which always stop the run."""
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        gap = 60.0 / per_minute
        last: Optional[float] = None
        for url in urls:
            if last is not None:
                wait = gap - (self._clock() - last)
                if wait > 0:
                    self._sleep(wait)
            last = self._clock()
            try:
                yield self.inspect(site, url, language=language)
            except RateLimited:
                raise
            except Exception:
                if not skip_errors:
                    raise


class _NS:
    def __init__(self, client: SearchConsole) -> None:
        self._c = client


class Sites(_NS):
    def list(self) -> List[Dict[str, Any]]:
        """Properties the account can see: ``[{"siteUrl", "permissionLevel"}, ...]``."""
        return list(self._c.request(_api.list_sites()).get("siteEntry") or [])

    def get(self, site: str) -> Dict[str, Any]:
        return self._c.request(_api.get_site(site))

    def add(self, site: str) -> None:
        self._c.request(_api.add_site(site))

    def delete(self, site: str) -> None:
        self._c.request(_api.delete_site(site))


class Sitemaps(_NS):
    def list(self, site: str, *, sitemap_index: Optional[str] = None) -> List[Dict[str, Any]]:
        return list(self._c.request(_api.list_sitemaps(site, sitemap_index)).get("sitemap")
                    or [])

    def get(self, site: str, feedpath: str) -> Dict[str, Any]:
        return self._c.request(_api.get_sitemap(site, feedpath))

    def submit(self, site: str, feedpath: str) -> None:
        self._c.request(_api.submit_sitemap(site, feedpath))

    def delete(self, site: str, feedpath: str) -> None:
        self._c.request(_api.delete_sitemap(site, feedpath))
