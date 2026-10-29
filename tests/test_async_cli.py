from __future__ import annotations

import json

import httpx
import pytest

from google_search_console import AsyncSearchConsole, RateLimited, cli
from tests.test_client import D1, D3, INSPECTION, _paged


async def test_async_query_paginates(api, agsc: AsyncSearchConsole) -> None:
    api.add("POST", "/searchAnalytics/query", _paged(30_000))
    rows = await agsc.query("example.com", D1, D3, ["query"])
    assert len(rows) == 30_000 and rows[0].query.startswith("2026-10-01")
    assert [json.loads(r.content)["rowLimit"] for r in api.requests] == [25_000, 25_000]
    limited = [r async for r in agsc.iter_query("example.com", D1, D3, ["query"], limit=10)]
    assert len(limited) == 10


async def test_async_sites_sitemaps_inspect(api, agsc, clock) -> None:
    api.add("GET", "/sitemaps", {"sitemap": [{"path": "s.xml"}]})
    api.add("PUT", "/sitemaps/", httpx.Response(204))
    api.add("DELETE", "/sitemaps/", httpx.Response(204))
    api.add("GET", "/webmasters/v3/sites", [
        httpx.Response(503, json={"error": {"code": 503}}),
        httpx.Response(200, json={"siteEntry": [{"siteUrl": "sc-domain:a.com"}]})])
    api.add("POST", ":inspect", INSPECTION)
    async with agsc:
        assert (await agsc.sites.list())[0]["siteUrl"] == "sc-domain:a.com"
        assert (await agsc.sitemaps.list("a.com"))[0]["path"] == "s.xml"
        await agsc.sitemaps.submit("a.com", "https://a.com/s.xml")
        await agsc.sitemaps.delete("a.com", "https://a.com/s.xml")
        res = [r async for r in agsc.inspect_many("a.com", ["https://a.com/1", "https://a.com/2"],
                                                   per_minute=120)]
    assert [r.verdict for r in res] == ["PASS", "PASS"]
    assert clock.sleeps[-1] == 0.5


async def test_async_quota_error(api, agsc) -> None:
    api.add("POST", ":inspect", httpx.Response(429, json={"error": {"code": 429}}))
    agsc.max_retries = 0
    with pytest.raises(RateLimited):
        await agsc.inspect("a.com", "https://a.com/1")


# ------------------------------------------------------------------------ CLI
@pytest.fixture
def run(api, gsc, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda args: gsc)

    def _run(*argv: str):
        cli.main(list(argv))
        return capsys.readouterr()
    return _run


def test_cli_query_table_csv_json(api, run, tmp_path) -> None:
    api.add("POST", "/searchAnalytics/query", {"rows": [
        {"keys": ["shoes"], "clicks": 3, "impressions": 100, "ctr": 0.03, "position": 4.25}]})
    out = run("query", "example.com", "--dims", "query", "--days", "7",
              "--filter", "country == tur")
    assert out.out.strip() == "shoes\t3\t100\t3.00%\t4.2"
    body = api.body()
    assert body["dimensionFilterGroups"][0]["filters"][0]["expression"] == "tur"
    run("query", "example.com", "--dims", "query", "--csv", str(tmp_path / "q.csv"))
    assert (tmp_path / "q.csv").read_text().startswith("query,clicks")
    data = json.loads(run("query", "example.com", "--dims", "query", "--json").out)
    assert data[0]["query"] == "shoes"


def test_cli_sites_inspect_sitemaps(api, run, tmp_path) -> None:
    api.add("GET", "/sitemaps", {"sitemap": [{"path": "https://a.com/s.xml",
                                              "lastDownloaded": "2026-10-20", "errors": "0",
                                              "warnings": "1"}]})
    api.add("PUT", "/sitemaps/", httpx.Response(204))
    api.add("GET", "/webmasters/v3/sites", {"siteEntry": [
        {"siteUrl": "sc-domain:a.com", "permissionLevel": "SITE_OWNER"}]})
    api.add("POST", ":inspect", INSPECTION)
    assert run("sites").out == "sc-domain:a.com\tSITE_OWNER\n"
    (tmp_path / "urls.txt").write_text("https://a.com/2\n\n")
    out = run("inspect", "a.com", "https://a.com/1", "--file", str(tmp_path / "urls.txt"))
    assert [line.split("\t")[0] for line in out.out.splitlines()] == ["PASS", "PASS"]
    assert "warnings=1" in run("sitemaps", "a.com").out
    assert "Submitted" in run("sitemaps", "a.com", "--submit", "https://a.com/s.xml").err


def test_cli_errors_exit_cleanly(api, run) -> None:
    with pytest.raises(SystemExit, match="unknown dimension"):
        run("query", "a.com", "--dims", "keyword")
    with pytest.raises(SystemExit, match="404"):
        run("sites")


def test_cli_requires_token(tmp_path) -> None:
    with pytest.raises(SystemExit, match="gsc login"):
        cli.main(["--token", str(tmp_path / "none.json"), "sites"])
