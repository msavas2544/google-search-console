"""Request builders shared by the sync and async clients (no I/O here)."""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from ._version import __version__

DEFAULT_BASE_URL = "https://searchconsole.googleapis.com/"
USER_AGENT = f"google-search-console-python/{__version__}"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Maximum ``rowLimit`` per Search Analytics request.
MAX_ROW_LIMIT = 25_000

DIMENSIONS = ("date", "hour", "query", "page", "country", "device", "searchAppearance")
SEARCH_TYPES = ("web", "image", "video", "news", "discover", "googleNews")
DATA_STATES = ("final", "all", "hourly_all")
AGGREGATION_TYPES = ("auto", "byProperty", "byPage", "byNewsShowcasePanel")

#: Search Console reports dates in Pacific time.
try:  # pragma: no cover - tzdata is present on all supported platforms
    from zoneinfo import ZoneInfo

    PACIFIC: dt.tzinfo = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover - Windows without tzdata
    PACIFIC = dt.timezone(dt.timedelta(hours=-8))

DateLike = Union[dt.date, str]


class Scope:
    """OAuth 2.0 scopes for the Search Console API."""

    READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
    FULL = "https://www.googleapis.com/auth/webmasters"


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    params: Optional[Dict[str, Any]] = None
    json: Optional[Dict[str, Any]] = None
    #: Safe to retry on 5xx (GET/PUT/DELETE are idempotent; queries are reads).
    idempotent: bool = True


# ------------------------------------------------------------------ site URLs
def normalize_site(site: str) -> str:
    """Return the property id the API expects.

    ``sc-domain:example.com`` is kept as is. ``example.com`` (no scheme) becomes a
    domain property. URL-prefix properties keep their scheme and get a trailing
    slash, which the API requires (``https://example.com`` -> ``https://example.com/``).
    """
    s = site.strip()
    if not s:
        raise ValueError("site must not be empty")
    if s.startswith("sc-domain:"):
        return s
    if "://" not in s:
        return "sc-domain:" + s.strip("/")
    if not s.endswith("/"):
        parsed = urllib.parse.urlsplit(s)
        if parsed.path in ("", "/") and not parsed.query:
            s += "/"
    return s


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def site_path(site: str) -> str:
    return f"webmasters/v3/sites/{_q(normalize_site(site))}"


# ------------------------------------------------------------------ endpoints
def list_sites() -> Call:
    return Call("GET", "webmasters/v3/sites")


def get_site(site: str) -> Call:
    return Call("GET", site_path(site))


def add_site(site: str) -> Call:
    return Call("PUT", site_path(site))


def delete_site(site: str) -> Call:
    return Call("DELETE", site_path(site))


def list_sitemaps(site: str, sitemap_index: Optional[str] = None) -> Call:
    params = {"sitemapIndex": sitemap_index} if sitemap_index else None
    return Call("GET", f"{site_path(site)}/sitemaps", params)


def get_sitemap(site: str, feedpath: str) -> Call:
    return Call("GET", f"{site_path(site)}/sitemaps/{_q(feedpath)}")


def submit_sitemap(site: str, feedpath: str) -> Call:
    return Call("PUT", f"{site_path(site)}/sitemaps/{_q(feedpath)}")


def delete_sitemap(site: str, feedpath: str) -> Call:
    return Call("DELETE", f"{site_path(site)}/sitemaps/{_q(feedpath)}")


def search_analytics(site: str, body: Mapping[str, Any]) -> Call:
    return Call("POST", f"{site_path(site)}/searchAnalytics/query", json=dict(body))


def inspect_url(site: str, url: str, language: Optional[str] = None) -> Call:
    body = {"inspectionUrl": url, "siteUrl": normalize_site(site)}
    if language:
        body["languageCode"] = language
    return Call("POST", "v1/urlInspection/index:inspect", json=body)


# ------------------------------------------------------------------ dates
def to_date(value: DateLike) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value[:10])


def today_pacific() -> dt.date:
    return dt.datetime.now(PACIFIC).date()


def date_range(start: Optional[DateLike], end: Optional[DateLike],
               days: Optional[int]) -> Tuple[dt.date, dt.date]:
    """Resolve an inclusive ``(start, end)``. ``days`` counts back from ``end``
    (default: yesterday in Pacific time, the API's reporting time zone)."""
    e = to_date(end) if end is not None else today_pacific() - dt.timedelta(days=1)
    if start is not None:
        s = to_date(start)
    else:
        s = e - dt.timedelta(days=(days or 28) - 1)
    if s > e:
        raise ValueError(f"start {s} is after end {e}")
    return s, e


def each_day(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


# ------------------------------------------------------------------ filters
_OPERATORS = {
    "==": "EQUALS", "=": "EQUALS", "equals": "EQUALS",
    "!=": "NOT_EQUALS", "notequals": "NOT_EQUALS",
    "contains": "CONTAINS", "~": "CONTAINS",
    "!contains": "NOT_CONTAINS", "notcontains": "NOT_CONTAINS", "not contains": "NOT_CONTAINS",
    "!~": "EXCLUDING_REGEX",
    "=~": "INCLUDING_REGEX", "~=": "INCLUDING_REGEX", "regex": "INCLUDING_REGEX",
    "includingregex": "INCLUDING_REGEX", "matches": "INCLUDING_REGEX",
    "!regex": "EXCLUDING_REGEX", "excludingregex": "EXCLUDING_REGEX",
    "not regex": "EXCLUDING_REGEX",
}
_FILTER_DIMS = {"query": "QUERY", "page": "PAGE", "country": "COUNTRY", "device": "DEVICE",
                "searchappearance": "SEARCH_APPEARANCE", "search_appearance": "SEARCH_APPEARANCE"}
_FILTER_RE = re.compile(
    r"^\s*(?P<dim>\w+)\s+(?P<op>not\s+contains|not\s+regex|!contains|!regex|[!=~]{1,2}|\w+)"
    r"\s+(?P<expr>.+?)\s*$", re.I)

FilterLike = Union[str, Tuple[str, str, str], Mapping[str, str]]


def parse_filter(f: FilterLike) -> Dict[str, str]:
    """Turn ``"query contains buy"``, ``("country", "==", "tur")`` or a raw API dict
    into an ``ApiDimensionFilter``. Operators: ``== != contains !contains =~ !~``
    (or their words: equals, notEquals, notContains, regex, !regex)."""
    if isinstance(f, Mapping):
        dim, op, expr = f["dimension"], f.get("operator", "equals"), f["expression"]
    elif isinstance(f, str):
        m = _FILTER_RE.match(f)
        if not m:
            raise ValueError(f"cannot parse filter {f!r}; use e.g. 'query contains buy'")
        dim, op, expr = m.group("dim"), m.group("op"), m.group("expr")
        if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in "\"'":
            expr = expr[1:-1]
    else:
        dim, op, expr = f
    d = _FILTER_DIMS.get(str(dim).lower().replace("-", "_"))
    if d is None:
        d = str(dim).upper() if str(dim).upper() in _FILTER_DIMS.values() else ""
    if not d:
        raise ValueError(f"unknown filter dimension {dim!r}; use one of query, page, country, "
                         "device, searchAppearance")
    o = _OPERATORS.get(re.sub(r"\s+", " ", str(op).lower()).replace("_", ""))
    if o is None:
        o = str(op).upper() if str(op).upper() in set(_OPERATORS.values()) else ""
    if not o:
        raise ValueError(f"unknown filter operator {op!r}")
    return {"dimension": d, "operator": o, "expression": str(expr)}


# ------------------------------------------------------------------ query body
def _enum(value: Optional[str], allowed: Sequence[str], name: str) -> Optional[str]:
    if value is None:
        return None
    norm = {a.lower().replace("_", ""): a for a in allowed}
    key = value.lower().replace("_", "")
    if key not in norm:
        raise ValueError(f"invalid {name} {value!r}; use one of {', '.join(allowed)}")
    # API enum names are UPPER_SNAKE_CASE (e.g. byNewsShowcasePanel -> BY_NEWS_SHOWCASE_PANEL).
    return re.sub(r"(?<!^)(?=[A-Z])", "_", norm[key]).upper()


def check_dimensions(dimensions: Sequence[str]) -> List[str]:
    out = []
    lookup = {d.lower(): d for d in DIMENSIONS}
    for d in dimensions:
        key = d.lower().replace("_", "")
        if key not in lookup:
            raise ValueError(f"unknown dimension {d!r}; use any of {', '.join(DIMENSIONS)}")
        out.append(lookup[key])
    if len(set(out)) != len(out):
        raise ValueError("dimensions must not repeat")
    return out


def query_body(
    start: dt.date,
    end: dt.date,
    dimensions: Sequence[str] = (),
    *,
    filters: Optional[Sequence[FilterLike]] = None,
    search_type: Optional[str] = None,
    data_state: Optional[str] = None,
    aggregation_type: Optional[str] = None,
    row_limit: Optional[int] = None,
    start_row: int = 0,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"startDate": start.isoformat(), "endDate": end.isoformat()}
    if dimensions:
        body["dimensions"] = check_dimensions(dimensions)
    if filters:
        body["dimensionFilterGroups"] = [{"groupType": "AND",
                                          "filters": [parse_filter(f) for f in filters]}]
    t = _enum(search_type, SEARCH_TYPES, "search_type")
    if t:
        body["type"] = t
    ds = _enum(data_state, DATA_STATES, "data_state")
    if ds:
        body["dataState"] = ds
    ag = _enum(aggregation_type, AGGREGATION_TYPES, "aggregation_type")
    if ag:
        body["aggregationType"] = ag
    if row_limit is not None:
        if not 1 <= row_limit <= MAX_ROW_LIMIT:
            raise ValueError(f"row_limit must be 1..{MAX_ROW_LIMIT}")
        body["rowLimit"] = row_limit
    if start_row:
        body["startRow"] = start_row
    return body


# ------------------------------------------------------------------ results
@dataclass(frozen=True)
class Row:
    """One Search Analytics row. Dimension values are reachable as attributes or
    items: ``row.query``, ``row["page"]``."""

    dimensions: Tuple[str, ...]
    keys: Tuple[str, ...]
    clicks: float
    impressions: float
    ctr: float
    position: float

    def get(self, dimension: str, default: Optional[str] = None) -> Optional[str]:
        try:
            return self.keys[self.dimensions.index(dimension)]
        except ValueError:
            return default

    def __getitem__(self, name: str) -> Any:
        if name in self.dimensions:
            return self.keys[self.dimensions.index(name)]
        if name in ("clicks", "impressions", "ctr", "position"):
            return getattr(self, name)
        raise KeyError(name)

    def __getattr__(self, name: str) -> str:
        dims = object.__getattribute__(self, "dimensions")
        if name in dims:
            return object.__getattribute__(self, "keys")[dims.index(name)]
        raise AttributeError(name)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = dict(zip(self.dimensions, self.keys))
        out.update(clicks=self.clicks, impressions=self.impressions, ctr=self.ctr,
                   position=self.position)
        return out


def parse_rows(dimensions: Sequence[str], raw_rows: Iterable[Mapping[str, Any]]) -> List[Row]:
    dims = tuple(dimensions)
    out = []
    for r in raw_rows:
        out.append(Row(dims, tuple(str(k) for k in r.get("keys") or ()),
                       float(r.get("clicks", 0) or 0), float(r.get("impressions", 0) or 0),
                       float(r.get("ctr", 0) or 0), float(r.get("position", 0) or 0)))
    return out


@dataclass(frozen=True)
class Inspection:
    """Flattened view of ``urlInspection.index.inspect``; ``raw`` keeps everything."""

    url: str
    verdict: Optional[str]
    coverage_state: Optional[str]
    indexing_state: Optional[str]
    page_fetch_state: Optional[str]
    robots_txt_state: Optional[str]
    last_crawl_time: Optional[str]
    crawled_as: Optional[str]
    google_canonical: Optional[str]
    user_canonical: Optional[str]
    link: Optional[str]
    raw: Mapping[str, Any]

    @property
    def indexed(self) -> bool:
        return self.verdict == "PASS"


def parse_inspection(url: str, payload: Mapping[str, Any]) -> Inspection:
    res = payload.get("inspectionResult") or {}
    idx = res.get("indexStatusResult") or {}
    return Inspection(
        url=url, verdict=idx.get("verdict"), coverage_state=idx.get("coverageState"),
        indexing_state=idx.get("indexingState"), page_fetch_state=idx.get("pageFetchState"),
        robots_txt_state=idx.get("robotsTxtState"), last_crawl_time=idx.get("lastCrawlTime"),
        crawled_as=idx.get("crawledAs"), google_canonical=idx.get("googleCanonical"),
        user_canonical=idx.get("userCanonical"), link=res.get("inspectionResultLink"),
        raw=payload)
