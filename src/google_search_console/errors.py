"""Exceptions mapped from Google's error envelopes (both the v1 ``status`` style and
the legacy Webmasters v3 ``errors[].reason`` style)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class SearchConsoleError(Exception):
    """Base class for all errors raised by this library."""


class AuthError(SearchConsoleError):
    """OAuth token acquisition/refresh failed."""


class APIError(SearchConsoleError):
    """The API returned a non-2xx response."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int,
        status: Optional[str] = None,
        reason: Optional[str] = None,
        details: Optional[List[Dict[str, Any]]] = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(f"{http_status} {status or reason or ''}: {message}".replace("  ", " "))
        self.message = message
        self.http_status = http_status
        self.status = status
        self.reason = reason
        self.details = details or []
        self.response_body = response_body


class InvalidArgument(APIError):
    pass


class Unauthenticated(APIError):
    pass


class PermissionDenied(APIError):
    """Often: the account has no access to this property, or the site URL is spelled
    differently from the property (``https://example.com/`` vs ``sc-domain:example.com``)."""


class NotFound(APIError):
    pass


class RateLimited(APIError):
    """Per-minute or per-day quota exceeded (HTTP 429, or 403 with a quota reason)."""


class ServerError(APIError):
    pass


QUOTA_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded",
                           "dailyLimitExceeded", "RATE_LIMIT_EXCEEDED"})

_BY_STATUS = {
    "INVALID_ARGUMENT": InvalidArgument,
    "OUT_OF_RANGE": InvalidArgument,
    "FAILED_PRECONDITION": InvalidArgument,
    "UNAUTHENTICATED": Unauthenticated,
    "PERMISSION_DENIED": PermissionDenied,
    "NOT_FOUND": NotFound,
    "RESOURCE_EXHAUSTED": RateLimited,
}
_BY_HTTP = {400: InvalidArgument, 401: Unauthenticated, 403: PermissionDenied, 404: NotFound,
            429: RateLimited}


def _reason(err: Dict[str, Any]) -> Optional[str]:
    for e in err.get("errors") or []:
        if isinstance(e, dict) and e.get("reason"):
            return str(e["reason"])
    for d in err.get("details") or []:
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("google.rpc.ErrorInfo"):
            return d.get("reason")
    return None


def from_response(http_status: int, body: Any) -> APIError:
    status = message = reason = None
    details: List[Dict[str, Any]] = []
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        status = err.get("status")
        message = err.get("message")
        details = err.get("details") or []
        reason = _reason(err)
    if message is None:
        message = body if isinstance(body, str) and body else "request failed"
    if reason in QUOTA_REASONS:
        cls: type = RateLimited
    else:
        cls = _BY_STATUS.get(status or "") or _BY_HTTP.get(http_status) or (
            ServerError if http_status >= 500 else APIError)
    return cls(str(message), http_status=http_status, status=status, reason=reason,
               details=details, response_body=body)
