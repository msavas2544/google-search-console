from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

import httpx
import pytest

from google_search_console import AsyncSearchConsole, SearchConsole, StaticToken

Handler = Callable[[httpx.Request], httpx.Response]


class FakeAPI:
    """Records requests and dispatches them to handlers keyed by (method, path fragment)."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.routes: List[tuple] = []

    def add(self, method: str, path_contains: str, handler: Any) -> None:
        self.routes.append((method, path_contains, handler))

    def body(self, i: int = -1) -> Dict[str, Any]:
        return json.loads(self.requests[i].content or b"{}")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raw_path = request.url.raw_path.decode()
        for method, frag, handler in self.routes:
            if request.method == method and frag in raw_path:
                if callable(handler):
                    return handler(request)
                if isinstance(handler, list):
                    return handler.pop(0) if len(handler) > 1 else handler[0]
                if isinstance(handler, httpx.Response):
                    return handler
                return httpx.Response(200, json=handler)
        return httpx.Response(404, json={"error": {"code": 404, "message": f"no route {raw_path}",
                                                   "status": "NOT_FOUND"}})


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: List[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s

    async def asleep(self, s: float) -> None:
        self.sleep(s)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def gsc(api: FakeAPI, clock: Clock) -> SearchConsole:
    http = httpx.Client(transport=httpx.MockTransport(api))
    return SearchConsole(StaticToken("tok"), http=http, sleep=clock.sleep, clock=clock.time)


@pytest.fixture
def agsc(api: FakeAPI, clock: Clock) -> AsyncSearchConsole:
    http = httpx.AsyncClient(transport=httpx.MockTransport(api))
    return AsyncSearchConsole(StaticToken("tok"), http=http, sleep=clock.asleep,
                              clock=clock.time)
