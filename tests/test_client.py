from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from google_search_console import (
    NotFound,
    PermissionDenied,
    RateLimited,
    Row,
    SearchConsole,
    StaticToken,
    normalize_site,
    parse_filter,
    to_csv,
)
from google_search_console._api import date_range, query_body
from google_search_console.auth import Credentials

D1, D3 = dt.date(2026, 10, 1), dt.date(2026, 10, 3)


# -------------------------------------------------------------------- helpers
def test_normalize_site() -> None:
    assert normalize_site("sc-domain:example.com") == "sc-domain:example.com"
    assert normalize_site("example.com") == "sc-domain:example.com"
    assert normalize_site("https://example.com") == "https://example.com/"
    assert normalize_site("https://example.com/blog/") == "https://example.com/blog/"
    with pytest.raises(ValueError):
        normalize_site("  ")


@pytest.mark.parametrize("spec,expected", [
    ("query contains buy shoes", ("QUERY", "CONTAINS", "buy shoes")),
    ("country == tur", ("COUNTRY", "EQUALS", "tur")),
    ("page != https://x.com/", ("PAGE", "NOT_EQUALS", "https://x.com/")),
    ("query not contains 'free'", ("QUERY", "NOT_CONTAINS", "free")),
    ("page =~ ^/blog/", ("PAGE", "INCLUDING_REGEX", "^/blog/")),
    ("page !~ \\.pdf$", ("PAGE", "EXCLUDING_REGEX", "\\.pdf$")),
    ("searchAppearance equals AMP_BLUE_LINK", ("SEARCH_APPEARANCE", "EQUALS", "AMP_BLUE_LINK")),
    (("device", "equals", "MOBILE"), ("DEVICE", "EQUALS", "MOBILE")),
    ({"dimension": "query", "operator": "notContains", "expression": "x"},
     ("QUERY", "NOT_CONTAINS", "x")),
])
def test_parse_filter(spec, expected) -> None:
    f = parse_filter(spec)
    assert (f["dimension"], f["operator"], f["expression"]) == expected


@pytest.mark.parametrize("bad", ["query", "keyword contains x", "query like x"])
def test_parse_filter_errors(bad) -> None:
    with pytest.raises(ValueError):
        parse_filter(bad)


def test_query_body_enums_and_validation() -> None:
    b = query_body(D1, D3, ["query", "page"], filters=["country == tur"],
                   search_type="googleNews", data_state="all", aggregation_type="byPage",
                   row_limit=10, start_row=20)
    assert b == {
        "startDate": "2026-10-01", "endDate": "2026-10-03", "dimensions": ["query", "page"],
        "dimensionFilterGroups": [{"groupType": "AND", "filters": [
            {"dimension": "COUNTRY", "operator": "EQUALS", "expression": "tur"}]}],
        "type": "GOOGLE_NEWS", "dataState": "ALL", "aggregationType": "BY_PAGE",
        "rowLimit": 10, "startRow": 20,
    }
    assert query_body(D1, D1, ["searchappearance"])["dimensions"] == ["searchAppearance"]
    assert query_body(D1, D1, data_state="hourly_all")["dataState"] == "HOURLY_ALL"
    for kwargs in ({"search_type": "shopping"}, {"row_limit": 25001}, {"row_limit": 0}):
        with pytest.raises(ValueError):
            query_body(D1, D1, **kwargs)
    with pytest.raises(ValueError):
        query_body(D1, D1, ["query", "query"])
    with pytest.raises(ValueError):
        query_body(D1, D1, ["keyword"])


def test_date_range() -> None:
    assert date_range("2026-10-01", "2026-10-28", None) == (D1, dt.date(2026, 10, 28))
    s, e = date_range(None, "2026-10-28", 7)
    assert (s, e) == (dt.date(2026, 10, 22), dt.date(2026, 10, 28))
    s, e = date_range(None, None, None)
    assert (e - s).days == 27
    with pytest.raises(ValueError):
        date_range("2026-10-05", "2026-10-01", None)


def test_row_access() -> None:
    r = Row(("query", "page"), ("shoes", "/a"), 3.0, 100.0, 0.03, 4.2)
    assert r.query == "shoes" and r["page"] == "/a" and r["clicks"] == 3.0
    assert r.get("country") is None and r.get("query") == "shoes"
    assert r.as_dict() == {"query": "shoes", "page": "/a", "clicks": 3.0, "impressions": 100.0,
                           "ctr": 0.03, "position": 4.2}
    with pytest.raises(AttributeError):
        _ = r.country
    with pytest.raises(KeyError):
        _ = r["country"]


# ------------------------------------------------------------- search analytics
def _paged(total: int, dims=("query",)):
    """Serve ``total`` synthetic rows, honouring startRow/rowLimit."""
    def handler(request: httpx.Request) -> httpx.Response:
        b = json.loads(request.content)
        start, size = b.get("startRow", 0), b.get("rowLimit", 1000)
        rows = [{"keys": [f"{b['startDate']}-q{i}"][: len(dims)] + [""] * (len(dims) - 1),
                 "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 2.5}
                for i in range(start, min(start + size, total))]
        return httpx.Response(200, json={"rows": rows, "responseAggregationType": "byProperty"})
    return handler


def test_query_paginates_past_25k(api, gsc: SearchConsole) -> None:
    api.add("POST", "/searchAnalytics/query", _paged(60_000))
    rows = gsc.query("https://example.com", D1, D3, ["query"])
    assert len(rows) == 60_000
    bodies = [json.loads(r.content) for r in api.requests]
    assert [(b.get("startRow", 0), b["rowLimit"]) for b in bodies] == [
        (0, 25_000), (25_000, 25_000), (50_000, 25_000)]
    assert "/sites/https%3A%2F%2Fexample.com%2F/searchAnalytics/query" in \
        api.requests[0].url.raw_path.decode()
    assert api.requests[0].headers["Authorization"] == "Bearer tok"


def test_query_limit_shrinks_last_page(api, gsc) -> None:
    api.add("POST", "/searchAnalytics/query", _paged(60_000))
    rows = gsc.query("sc-domain:example.com", D1, D3, ["query"], limit=30_000)
    assert len(rows) == 30_000
    assert [json.loads(r.content)["rowLimit"] for r in api.requests] == [25_000, 5_000]
    assert gsc.query("sc-domain:example.com", D1, D3, limit=0) == []


def test_query_split_by_day_adds_date(api, gsc) -> None:
    api.add("POST", "/searchAnalytics/query", _paged(3, dims=("date", "page")))
    rows = gsc.query("example.com", D1, D3, ["page"], split_by="day", page_size=1000)
    bodies = [json.loads(r.content) for r in api.requests]
    assert [(b["startDate"], b["endDate"]) for b in bodies] == [
        ("2026-10-01", "2026-10-01"), ("2026-10-02", "2026-10-02"), ("2026-10-03", "2026-10-03")]
    assert bodies[0]["dimensions"] == ["date", "page"]
    assert len(rows) == 9 and rows[0].dimensions == ("date", "page")
    with pytest.raises(ValueError):
        gsc.query("example.com", D1, D3, split_by="week")


def test_query_no_rows(api, gsc) -> None:
    api.add("POST", "/searchAnalytics/query", {"responseAggregationType": "auto"})
    assert gsc.query("example.com", days=7) == []


# ------------------------------------------------------------- sites / sitemaps
def test_sites_and_sitemaps(api, gsc) -> None:
    api.add("GET", "/webmasters/v3/sites/sc-domain%3Aexample.com/sitemaps/",
            {"path": "https://example.com/sitemap.xml"})
    api.add("GET", "/sitemaps", {"sitemap": [{"path": "https://example.com/sitemap.xml",
                                              "isPending": False, "errors": "0"}]})
    api.add("PUT", "/sitemaps/", httpx.Response(204))
    api.add("DELETE", "/sitemaps/", httpx.Response(204))
    api.add("GET", "/webmasters/v3/sites/sc-domain", {"siteUrl": "sc-domain:example.com",
                                                       "permissionLevel": "SITE_OWNER"})
    api.add("GET", "/webmasters/v3/sites", {"siteEntry": [
        {"siteUrl": "sc-domain:example.com", "permissionLevel": "SITE_OWNER"}]})
    api.add("PUT", "/webmasters/v3/sites/", httpx.Response(204))
    api.add("DELETE", "/webmasters/v3/sites/", httpx.Response(204))

    assert gsc.sites.list()[0]["permissionLevel"] == "SITE_OWNER"
    assert gsc.sites.get("example.com")["siteUrl"] == "sc-domain:example.com"
    gsc.sites.add("https://new.example.com")
    assert api.requests[-1].url.raw_path.decode().endswith("https%3A%2F%2Fnew.example.com%2F")
    gsc.sites.delete("https://new.example.com/")
    assert api.requests[-1].method == "DELETE"

    maps = gsc.sitemaps.list("example.com", sitemap_index="https://example.com/index.xml")
    assert maps[0]["path"].endswith("sitemap.xml")
    assert api.requests[-1].url.params["sitemapIndex"] == "https://example.com/index.xml"
    gsc.sitemaps.submit("example.com", "https://example.com/sitemap.xml")
    assert api.requests[-1].url.raw_path.decode().endswith(
        "/sitemaps/https%3A%2F%2Fexample.com%2Fsitemap.xml")
    assert gsc.sitemaps.get("example.com", "https://example.com/sitemap.xml")["path"]
    gsc.sitemaps.delete("example.com", "https://example.com/sitemap.xml")
    assert api.requests[-1].method == "DELETE"


# --------------------------------------------------------------- URL inspection
INSPECTION = {"inspectionResult": {
    "inspectionResultLink": "https://search.google.com/search-console/inspect?x",
    "indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed",
                          "indexingState": "INDEXING_ALLOWED", "pageFetchState": "SUCCESSFUL",
                          "robotsTxtState": "ALLOWED", "lastCrawlTime": "2026-10-20T10:00:00Z",
                          "crawledAs": "MOBILE", "googleCanonical": "https://example.com/a",
                          "userCanonical": "https://example.com/a"}}}


def test_inspect(api, gsc) -> None:
    api.add("POST", "/v1/urlInspection/index:inspect", INSPECTION)
    res = gsc.inspect("example.com", "https://example.com/a", language="tr-TR")
    assert res.indexed and res.coverage_state == "Submitted and indexed"
    assert res.crawled_as == "MOBILE" and res.link and res.raw == INSPECTION
    assert api.body() == {"inspectionUrl": "https://example.com/a",
                          "siteUrl": "sc-domain:example.com", "languageCode": "tr-TR"}


def test_inspect_many_throttles_and_skips(api, gsc, clock) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["inspectionUrl"].endswith("/bad"):
            return httpx.Response(400, json={"error": {"code": 400, "message": "not in property",
                                                       "status": "INVALID_ARGUMENT"}})
        return httpx.Response(200, json=INSPECTION)

    api.add("POST", ":inspect", handler)
    urls = ["https://example.com/a", "https://example.com/bad", "https://example.com/c"]
    out = list(gsc.inspect_many("example.com", urls, per_minute=60, skip_errors=True))
    assert [r.url for r in out] == ["https://example.com/a", "https://example.com/c"]
    assert clock.sleeps == [1.0, 1.0]
    with pytest.raises(Exception, match="not in property"):
        list(gsc.inspect_many("example.com", urls, per_minute=600))


def test_inspect_many_stops_on_quota(api, gsc) -> None:
    api.add("POST", ":inspect", httpx.Response(429, json={"error": {
        "code": 429, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}}))
    gsc.max_retries = 0
    with pytest.raises(RateLimited):
        list(gsc.inspect_many("example.com", ["https://example.com/a"], skip_errors=True))


# ------------------------------------------------------------- errors / retries
def _v3_error(code: int, reason: str, message: str = "x") -> httpx.Response:
    return httpx.Response(code, json={"error": {"code": code, "message": message,
                                                "errors": [{"reason": reason,
                                                            "message": message}]}})


def test_permission_error_mapping(api, gsc) -> None:
    api.add("GET", "/sites/", _v3_error(403, "forbidden", "User does not have sufficient "
                                                          "permission for site"))
    with pytest.raises(PermissionDenied) as exc:
        gsc.sites.get("example.com")
    assert exc.value.reason == "forbidden" and exc.value.http_status == 403


def test_rate_limit_403_is_retried(api, gsc, clock) -> None:
    api.add("GET", "/webmasters/v3/sites", [_v3_error(403, "rateLimitExceeded"),
                                            httpx.Response(200, json={"siteEntry": []})])
    assert gsc.sites.list() == []
    assert len(api.requests) == 2 and len(clock.sleeps) == 1


def test_daily_quota_is_not_retried(api, gsc) -> None:
    api.add("GET", "/webmasters/v3/sites", _v3_error(403, "quotaExceeded"))
    with pytest.raises(RateLimited):
        gsc.sites.list()
    assert len(api.requests) == 1


def test_retry_after_and_5xx(api, gsc, clock) -> None:
    api.add("GET", "/webmasters/v3/sites", [
        httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"code": 429}}),
        httpx.Response(503, json={"error": {"code": 503, "message": "backend"}}),
        httpx.Response(200, json={"siteEntry": [{"siteUrl": "sc-domain:a.com"}]})])
    assert gsc.sites.list()[0]["siteUrl"] == "sc-domain:a.com"
    assert clock.sleeps[0] == 7.0 and len(api.requests) == 3


def test_not_found(api, gsc) -> None:
    with pytest.raises(NotFound):
        gsc.sitemaps.get("example.com", "https://example.com/missing.xml")


def test_401_refreshes_once(api) -> None:
    class Rotating(Credentials):
        def __init__(self) -> None:
            self.n = 0
            self.invalidated = []

        def token(self, http):
            self.n += 1
            return f"t{self.n}"

        def invalidate(self, failed_token=None):
            self.invalidated.append(failed_token)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] == "Bearer t1":
            return httpx.Response(401, json={"error": {"code": 401, "message": "expired"}})
        return httpx.Response(200, json={"siteEntry": []})

    api.add("GET", "/sites", handler)
    creds = Rotating()
    c = SearchConsole(creds, http=httpx.Client(transport=httpx.MockTransport(api)))
    assert c.sites.list() == []
    assert creds.invalidated == ["t1"]


def test_string_token_and_context_manager(api) -> None:
    api.add("GET", "/sites", {"siteEntry": []})
    with SearchConsole("raw-token", http=httpx.Client(transport=httpx.MockTransport(api))) as c:
        c.sites.list()
    assert api.requests[0].headers["Authorization"] == "Bearer raw-token"
    assert isinstance(c.credentials, StaticToken)


# ------------------------------------------------------------------- export
def test_to_csv(tmp_path) -> None:
    rows = [Row(("date", "query"), ("2026-10-01", "a, b"), 1, 10, 0.1, 3.0)]
    text = to_csv(rows, tmp_path / "out.csv")
    assert text.splitlines() == ["date,query,clicks,impressions,ctr,position",
                                 '2026-10-01,"a, b",1,10,0.1,3.0']
    assert (tmp_path / "out.csv").read_text() == text
    assert to_csv([]) == "clicks,impressions,ctr,position\n"


def test_to_dataframe() -> None:
    pd = pytest.importorskip("pandas")
    from google_search_console import to_dataframe

    df = to_dataframe([Row(("date",), ("2026-10-01",), 1, 10, 0.1, 3.0)])
    assert list(df.columns) == ["date", "clicks", "impressions", "ctr", "position"]
    assert df["date"].dtype.kind == "M" and pd is not None
