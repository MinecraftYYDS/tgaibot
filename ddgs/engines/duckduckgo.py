"""Duckduckgo search engine implementation."""

from collections.abc import Mapping
from typing import Any, ClassVar, TypeVar

try:
    from fake_useragent import UserAgent
except Exception:  # pragma: no cover - optional dependency
    UserAgent = None  # type: ignore[assignment]

from ddgs.base import BaseSearchEngine
from ddgs.http_client2 import HttpClient2
from ddgs.results import TextResult

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

if UserAgent is not None:
    try:
        _ua_value = UserAgent().random
    except Exception:
        _ua_value = _DEFAULT_UA
else:
    _ua_value = _DEFAULT_UA

T = TypeVar("T")


class Duckduckgo(BaseSearchEngine[TextResult]):
    """Duckduckgo search engine."""

    name = "duckduckgo"
    category = "text"
    provider = "bing"

    search_url = "https://html.duckduckgo.com/html/"
    search_method = "POST"

    items_xpath = "//div[contains(@class, 'body')]"
    elements_xpath: ClassVar[Mapping[str, str]] = {"title": ".//h2//text()", "href": "./a/@href", "body": "./a//text()"}

    headers: ClassVar[dict[str, str]] = {"User-Agent": _ua_value}

    def __init__(self, proxy: str | None = None, timeout: int | None = None, *, verify: bool = True) -> None:
        """Temporary, delete when HttpClient is fixed."""
        self.http_client = HttpClient2(headers=self.headers, proxy=proxy, timeout=timeout, verify=verify)
        self.results: list[T] = []  # type: ignore[valid-type]

    def build_payload(
        self,
        query: str,
        region: str,
        safesearch: str,  # noqa: ARG002
        timelimit: str | None,
        page: int = 1,
        **kwargs: str,  # noqa: ARG002
    ) -> dict[str, Any]:
        """Build a payload for the search request."""
        payload = {"q": query, "b": "", "l": region}
        if page > 1:
            payload["s"] = f"{10 + (page - 2) * 15}"
        if timelimit:
            payload["df"] = timelimit
        return payload

    def post_extract_results(self, results: list[TextResult]) -> list[TextResult]:
        """Post-process search results."""
        return [r for r in results if not r.href.startswith("https://duckduckgo.com/y.js?")]

