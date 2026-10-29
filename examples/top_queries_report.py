"""Compare the last 28 days with the 28 days before and print the biggest movers.

    python examples/top_queries_report.py sc-domain:example.com
"""

import datetime as dt
import sys

from google_search_console import OAuthCredentials, SearchConsole
from google_search_console.cli import DEFAULT_TOKEN

site = sys.argv[1]
end = dt.date.today() - dt.timedelta(days=3)
mid = end - dt.timedelta(days=28)
start = mid - dt.timedelta(days=28)

with SearchConsole(OAuthCredentials.load(DEFAULT_TOKEN)) as gsc:
    now = {r.query: r.clicks for r in gsc.query(site, mid + dt.timedelta(days=1), end, ["query"])}
    before = {r.query: r.clicks for r in gsc.query(site, start + dt.timedelta(days=1), mid,
                                                   ["query"])}

movers = sorted(set(now) | set(before), key=lambda q: now.get(q, 0) - before.get(q, 0))
print("Biggest drops:")
for q in movers[:10]:
    print(f"  {before.get(q, 0):6.0f} -> {now.get(q, 0):6.0f}  {q}")
print("Biggest gains:")
for q in reversed(movers[-10:]):
    print(f"  {before.get(q, 0):6.0f} -> {now.get(q, 0):6.0f}  {q}")
