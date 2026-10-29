# google-search-console

A Python client for the **Google Search Console API**: Search Analytics without the 25,000-row ceiling, URL Inspection in bulk, sitemaps and properties, sync and async, plus a `gsc` command line tool.

```python
from google_search_console import SearchConsole, OAuthCredentials

gsc = SearchConsole(OAuthCredentials.load("token.json"))

for row in gsc.query("sc-domain:example.com", days=28, dimensions=["query", "page"],
                     filters=["country == tur", "query contains shoes"]):
    print(row.query, row.page, row.clicks, row.impressions, f"{row.ctr:.1%}", row.position)
```

* **All the rows.** `query()` pages through the API's 25,000-row limit for you. `split_by="day"` runs one request per day, which returns more long-tail queries on large sites. `limit=` caps the total.
* **Readable filters.** Write `"query contains buy"`, `"page =~ ^/blog/"` or `"country == tur"` instead of nested `dimensionFilterGroups`.
* **Friendly rows.** Rows expose `row.query`, `row["page"]`, `row.as_dict()`, and export with `to_csv()` or `to_dataframe()` (pandas).
* **Bulk URL Inspection.** `inspect_many()` paces calls under the 600-per-minute quota, can skip failed URLs, and stops on quota errors. Each result is flattened (`verdict`, `coverage_state`, `last_crawl_time`, canonicals) with the full payload in `.raw`.
* **Properties made simple.** `example.com` becomes `sc-domain:example.com`, and URL-prefix properties get the trailing slash the API insists on. Site and sitemap URLs are encoded correctly in paths.
* **Robust.** Sync and async clients share one surface. OAuth 2.0 with PKCE and refresh, service accounts through `google-auth`, retries with backoff for 429/5xx and per-minute 403 quota errors, and typed exceptions (`PermissionDenied`, `RateLimited`, ...).

## Install

```bash
pip install google-search-console            # core (httpx only)
pip install "google-search-console[pandas]"  # + to_dataframe()
```

Python 3.9+.

## Set up access (once)

1. In the [Google Cloud console](https://console.cloud.google.com/), enable the **Google Search Console API** for your project.
2. Create an **OAuth client ID** of type *Desktop app* and download its `client_secret_*.json`.
3. While the consent screen is in *Testing* mode, add your Google account as a test user. Test-mode refresh tokens expire after 7 days.
4. The Google account must have access to the property in Search Console.

```bash
gsc login --client-secrets client_secret_XXXX.json          # read-only scope
gsc login --client-secrets client_secret_XXXX.json --write  # also submit sitemaps, add sites
```

The token is saved to `~/.config/google-search-console/token.json`; set `GSC_TOKEN` to change it.

## Command line

```bash
gsc sites
gsc query sc-domain:example.com --dims query --days 28 --limit 20
gsc query sc-domain:example.com --dims date,page --filter "page =~ /blog/" --csv blog.csv
gsc query https://example.com/ --dims query,country --by-day --start 2026-09-01 --end 2026-09-30 --json
gsc inspect sc-domain:example.com https://example.com/ https://example.com/about
gsc inspect sc-domain:example.com --file urls.txt --skip-errors
gsc sitemaps sc-domain:example.com
gsc sitemaps sc-domain:example.com --submit https://example.com/sitemap.xml
```

## Python API

### Search Analytics

```python
rows = gsc.query(
    "sc-domain:example.com",
    "2026-09-01", "2026-09-30",              # inclusive, Pacific-time dates
    dimensions=["date", "query"],            # date, hour, query, page, country, device, searchAppearance
    filters=["device == MOBILE", "query !contains brand"],
    search_type="web",                        # web, image, video, news, discover, googleNews
    data_state="all",                         # include fresh, not yet final data
)

from google_search_console import to_csv, to_dataframe
to_csv(rows, "september.csv", encoding="utf-8-sig")   # utf-8-sig opens cleanly in Excel
df = to_dataframe(rows)

for row in gsc.iter_query("sc-domain:example.com", days=90, dimensions=["page"]):
    ...                                       # lazy, one API page at a time

gsc.search_analytics("sc-domain:example.com", {...})  # raw request body, single call
```

Filter operators: `==` (equals), `!=`, `contains`, `!contains` (or `not contains`), `=~` (regex), `!~` (not regex). Filter dimensions: `query`, `page`, `country`, `device`, `searchAppearance`. You can also pass tuples or raw API dicts.

Without `start`/`end`, `days=N` counts back from yesterday in Pacific time. Search Console data usually lags 2–3 days, so the most recent days may be empty unless you pass `data_state="all"`.

### URL Inspection

```python
res = gsc.inspect("sc-domain:example.com", "https://example.com/page", language="tr-TR")
res.verdict, res.coverage_state, res.last_crawl_time, res.google_canonical, res.indexed

for res in gsc.inspect_many("sc-domain:example.com", urls, skip_errors=True):
    print(res.url, res.verdict)
```

The API allows 2,000 inspections per day and 600 per minute per property.

### Properties and sitemaps

```python
gsc.sites.list()                       # [{"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}]
gsc.sites.add("https://example.com/")  # needs the full scope
gsc.sitemaps.list("sc-domain:example.com")
gsc.sitemaps.submit("sc-domain:example.com", "https://example.com/sitemap.xml")
```

### Async

```python
from google_search_console import AsyncSearchConsole

async with AsyncSearchConsole(OAuthCredentials.load("token.json")) as gsc:
    rows = await gsc.query("sc-domain:example.com", days=7, dimensions=["page"])
    async for res in gsc.inspect_many("sc-domain:example.com", urls):
        ...
```

### Service accounts

Add the service account's e-mail as a user of the property in Search Console, then:

```python
from google.oauth2 import service_account
from google_search_console import GoogleAuthCredentials, Scope, SearchConsole

sa = service_account.Credentials.from_service_account_file("sa.json", scopes=[Scope.READONLY])
gsc = SearchConsole(GoogleAuthCredentials(sa))   # pip install "google-search-console[google-auth]"
```

### Errors

All errors derive from `SearchConsoleError`. API errors are `APIError` subclasses with `.http_status`, `.status` and `.reason`. A `PermissionDenied` usually means the account has no access to that property, or the property is spelled differently (`https://example.com/` and `sc-domain:example.com` are different properties). `RateLimited` covers 429 responses and quota 403s. Per-minute limits are retried automatically, but the daily `quotaExceeded` is not.

## Not included

The Mobile-Friendly Test API (`urlTestingTools.mobileFriendlyTest`) was retired by Google in December 2023 and is left out on purpose.

## Status

Version 0.1, alpha. The request and response shapes follow the official discovery document (revision 2026-10-28) and are covered by 44 unit tests on Python 3.9–3.13 against mocked HTTP. Login, `sites`, Search Analytics (query and date dimensions), URL Inspection and sitemaps have also been exercised against the live API with a real property (October 2026). Please open an issue if a live response differs.

This is a community project, not affiliated with or endorsed by Google. "Google Search Console" is a trademark of Google LLC.

## License

Apache-2.0
