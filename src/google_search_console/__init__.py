"""Python client for the Google Search Console API."""

from ._api import Inspection, Row, Scope, normalize_site, parse_filter
from ._version import __version__
from .async_client import AsyncSearchConsole
from .auth import (
    Credentials,
    GoogleAuthCredentials,
    OAuthCredentials,
    OAuthFlow,
    StaticToken,
    load_client_secrets,
    revoke,
)
from .client import SearchConsole
from .errors import (
    APIError,
    AuthError,
    InvalidArgument,
    NotFound,
    PermissionDenied,
    RateLimited,
    SearchConsoleError,
    ServerError,
    Unauthenticated,
)
from .export import to_csv, to_dataframe

__all__ = [
    "__version__",
    "SearchConsole",
    "AsyncSearchConsole",
    "Row",
    "Inspection",
    "Scope",
    "normalize_site",
    "parse_filter",
    "to_csv",
    "to_dataframe",
    "Credentials",
    "OAuthCredentials",
    "OAuthFlow",
    "StaticToken",
    "GoogleAuthCredentials",
    "load_client_secrets",
    "revoke",
    "SearchConsoleError",
    "AuthError",
    "APIError",
    "InvalidArgument",
    "Unauthenticated",
    "PermissionDenied",
    "NotFound",
    "RateLimited",
    "ServerError",
]
