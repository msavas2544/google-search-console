# Changelog

## 0.1.0 — 2026-10-29

- First release: sync and async clients for the Search Console API (sites, sitemaps, Search Analytics, URL Inspection).
- Search Analytics: automatic pagination past 25,000 rows, `split_by="day"`, `limit`, readable string filters, `Row` objects, CSV and pandas export.
- URL Inspection: flattened `Inspection` results and rate-paced `inspect_many()`.
- Property normalisation (`example.com` → `sc-domain:example.com`, trailing slash for URL-prefix properties).
- OAuth 2.0 with PKCE and refresh, service accounts through `google-auth`, retries with backoff, typed errors.
- `gsc` command line: `login`, `sites`, `query`, `inspect`, `sitemaps`.
- Verified against the live API with a real property.
