"""``gsc`` command line: login, sites, query, inspect, sitemaps."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from ._api import Scope
from .auth import OAuthCredentials, OAuthFlow, load_client_secrets
from .client import SearchConsole
from .errors import SearchConsoleError
from .export import to_csv

DEFAULT_TOKEN = Path(os.environ.get(
    "GSC_TOKEN", Path.home() / ".config" / "google-search-console" / "token.json"))


def _client(args: argparse.Namespace) -> SearchConsole:
    path = Path(args.token)
    if not path.exists():
        sys.exit(f"No token at {path}. Run `gsc login --client-secrets FILE` first.")
    return SearchConsole(OAuthCredentials.load(path))


def cmd_login(args: argparse.Namespace) -> None:
    client_id = args.client_id or os.environ.get("GSC_CLIENT_ID")
    secret = args.client_secret or os.environ.get("GSC_CLIENT_SECRET")
    secrets_file = args.client_secrets or os.environ.get("GSC_CLIENT_SECRETS")
    if secrets_file:
        client_id, secret = load_client_secrets(secrets_file)
    if not client_id:
        sys.exit("--client-secrets FILE, or --client-id (or GSC_CLIENT_ID) is required")
    scope = Scope.FULL if args.write else Scope.READONLY
    flow = OAuthFlow(client_id, secret, [scope])
    creds = flow.run_local_server(port=args.port, open_browser=not args.no_browser,
                                  print_fn=lambda m: print(m, file=sys.stderr))
    creds.save(args.token)
    print(f"Saved token to {args.token}", file=sys.stderr)


def cmd_sites(args: argparse.Namespace) -> None:
    with _client(args) as c:
        sites = c.sites.list()
    if args.json:
        print(json.dumps(sites, indent=2))
        return
    if not sites:
        print("No properties. Add the site in Search Console with this Google account.",
              file=sys.stderr)
    for s in sorted(sites, key=lambda x: x.get("siteUrl", "")):
        print(f"{s.get('siteUrl')}\t{s.get('permissionLevel')}")


def cmd_query(args: argparse.Namespace) -> None:
    dims = [d for d in (args.dims or "").split(",") if d]
    with _client(args) as c:
        rows = c.query(args.site, args.start, args.end, dims, days=args.days,
                       filters=args.filter, search_type=args.type,
                       data_state=args.data_state, limit=args.limit,
                       split_by="day" if args.by_day else None)
    if args.json:
        print(json.dumps([r.as_dict() for r in rows], indent=2, ensure_ascii=False))
    elif args.csv:
        to_csv(rows, args.csv)
        print(f"Wrote {len(rows)} rows to {args.csv}", file=sys.stderr)
    else:
        if not rows:
            print("No rows (Search Console data usually lags 2-3 days).", file=sys.stderr)
        for r in rows:
            keys = "\t".join(r.keys)
            print(f"{keys}\t{r.clicks:g}\t{r.impressions:g}\t{r.ctr:.2%}\t{r.position:.1f}"
                  .lstrip("\t"))


def cmd_inspect(args: argparse.Namespace) -> None:
    urls = list(args.urls)
    if args.file:
        urls += [u.strip() for u in Path(args.file).read_text().splitlines() if u.strip()]
    if not urls:
        sys.exit("give URLs as arguments or with --file")
    with _client(args) as c:
        for res in c.inspect_many(args.site, urls, skip_errors=args.skip_errors):
            if args.json:
                print(json.dumps({"url": res.url, **res.raw}, ensure_ascii=False))
            else:
                print(f"{res.verdict or '?'}\t{res.coverage_state or ''}\t"
                      f"{res.last_crawl_time or ''}\t{res.url}")


def cmd_sitemaps(args: argparse.Namespace) -> None:
    with _client(args) as c:
        if args.submit:
            c.sitemaps.submit(args.site, args.submit)
            print(f"Submitted {args.submit}", file=sys.stderr)
            return
        maps = c.sitemaps.list(args.site)
    if args.json:
        print(json.dumps(maps, indent=2))
        return
    for m in maps:
        state = "pending" if m.get("isPending") else (m.get("lastDownloaded") or "")
        print(f"{m.get('path')}\t{state}\terrors={m.get('errors', 0)}\t"
              f"warnings={m.get('warnings', 0)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gsc", description=__doc__)
    p.add_argument("--token", default=str(DEFAULT_TOKEN), help="token file (default: %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help="authorize in the browser and save a token")
    s.add_argument("--client-secrets", metavar="FILE",
                   help="client_secret_*.json downloaded from the Google Cloud console")
    s.add_argument("--client-id")
    s.add_argument("--client-secret")
    s.add_argument("--write", action="store_true",
                   help="request the full scope (needed for sitemap submit, add/delete site)")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("sites", help="list properties")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sites)

    s = sub.add_parser("query", help="Search Analytics, e.g. `query sc-domain:x.com --dims query`")
    s.add_argument("site", help="property, e.g. sc-domain:example.com or https://example.com/")
    s.add_argument("--dims", help="comma-separated: date,hour,query,page,country,device,"
                                  "searchAppearance")
    s.add_argument("--days", type=int, default=28, help="last N days (default: %(default)s)")
    s.add_argument("--start", help="YYYY-MM-DD (overrides --days)")
    s.add_argument("--end", help="YYYY-MM-DD, inclusive (default: yesterday, Pacific time)")
    s.add_argument("--filter", action="append",
                   help="repeatable: --filter 'query contains shoes' --filter 'country == tur'")
    s.add_argument("--type", help="web (default), image, video, news, discover, googleNews")
    s.add_argument("--data-state", help="final (default) or all (include fresh, partial data)")
    s.add_argument("--limit", type=int, help="maximum number of rows")
    s.add_argument("--by-day", action="store_true", help="one request per day (more rows)")
    out = s.add_mutually_exclusive_group()
    out.add_argument("--csv", metavar="FILE")
    out.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_query)

    s = sub.add_parser("inspect", help="URL Inspection: index status of URLs")
    s.add_argument("site")
    s.add_argument("urls", nargs="*")
    s.add_argument("--file", help="text file with one URL per line")
    s.add_argument("--skip-errors", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("sitemaps", help="list sitemaps, or submit one with --submit URL")
    s.add_argument("site")
    s.add_argument("--submit", metavar="SITEMAP_URL")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sitemaps)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (SearchConsoleError, ValueError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":  # pragma: no cover
    main()
