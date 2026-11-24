"""Read-only client for the private Kindle Cloud Reader endpoints.

Amazon has no public Kindle API. The Kindle Cloud Reader website reads
your library and Whispersync reading position through a few JSON
endpoints, using your normal Amazon sign-in cookies. This client calls
the same endpoints with your own cookies. It only reads: it never
downloads book content, buys anything or changes anything.

Amazon rejects requests whose TLS handshake does not look like a real
browser, so requests go through curl_cffi with Chrome impersonation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from curl_cffi.requests import AsyncSession

_LOGGER = logging.getLogger(__name__)

CLIENT_VERSION = "20000100"
TIMEOUT = 30
_JSONP_RE = re.compile(r"\((\{.*\})\)", re.DOTALL)
# The reader page embeds its session: var deviceToken = {"deviceName":...,"deviceSessionToken":...};
_PAGE_TOKEN_RE = re.compile(r"var\s+deviceToken\s*=\s*(\{.*?\})\s*;", re.DOTALL)


class KindleError(Exception):
    """Base error."""


class KindleAuthError(KindleError):
    """Cookies are missing, expired or signed out."""


class KindleBlockedError(KindleError):
    """Amazon returned a bot check or an unexpected page."""


class KindleDeviceTokenError(KindleError):
    """The device token was rejected, so progress cannot be read."""


@dataclass
class LibraryBook:
    """A book from the library list."""

    asin: str
    title: str
    authors: list[str]
    cover_url: str | None
    origin_type: str | None
    resource_type: str | None


@dataclass
class BookProgress:
    """Whispersync position for one book."""

    asin: str
    position: int | None
    start_position: int
    end_position: int | None
    sync_time: datetime | None
    device_name: str | None

    @property
    def percent(self) -> float | None:
        """Percent read, or None if unknown."""
        if self.position is None or not self.end_position:
            return None
        pct = (self.start_position + self.position) / self.end_position * 100
        return round(max(0.0, min(100.0, pct)), 1)


def parse_cookie_header(raw: str) -> dict[str, str]:
    """Parse a pasted Cookie header ('a=1; b=2') into a dict.

    Accepts a leading 'cookie:' label, as copied from browser dev tools.
    """
    raw = raw.strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:]
    cookies: dict[str, str] = {}
    for part in raw.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip().strip('"')
        if name:
            cookies[name] = value
    return cookies


def parse_device_token(raw: str) -> str:
    """Accept either the bare token or the whole getDeviceToken URL."""
    raw = raw.strip()
    if "serialNumber=" in raw:
        query = parse_qs(urlparse(raw).query)
        values = query.get("serialNumber") or query.get("deviceType")
        if values:
            return values[0].strip()
    return raw


def has_sign_in_cookies(cookies: dict[str, str]) -> bool:
    """True if the cookies include an Amazon sign-in token (at-main, at-acbuk, ...)."""
    return any(name.startswith("at-") for name in cookies) and "session-id" in cookies


def _authors(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    names: list[str] = []
    for entry in raw:
        for name in str(entry).split(":"):
            name = name.strip()
            # Amazon lists authors as "Surname, First" joined by ":".
            if "," in name:
                last, first = (p.strip() for p in name.split(",", 1))
                name = f"{first} {last}".strip()
            if name and name not in names:
                names.append(name)
    return names


def _sync_time(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        text = str(value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000, tz=UTC)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError, OSError):
        return None


def _unwrap(payload: Any) -> Any:
    """Some responses wrap the real payload in a JSON string 'body'."""
    if isinstance(payload, dict) and "body" in payload and len(payload) <= 3:
        body = payload["body"]
        if isinstance(body, str):
            try:
                return json.loads(body)
            except ValueError:
                return payload
        if isinstance(body, dict):
            return body
    return payload


class KindleClient:
    """Async client for one Amazon account."""

    def __init__(self, host: str, cookies: dict[str, str], device_token: str) -> None:
        """Set up the client. Call async_close() when done."""
        self._host = host
        self._base = f"https://{host}"
        # read.amazon.co.uk -> .amazon.co.uk
        self._cookie_domain = "." + host.split(".", 1)[1]
        self._device_token = device_token
        self._session = AsyncSession(impersonate="chrome")
        for name, value in cookies.items():
            self._session.cookies.set(name, value, domain=self._cookie_domain)
        self._session_id_header: str | None = cookies.get("session-id")
        self._adp_token: str | None = None
        self._page_asin: str | None = None
        self._metadata: dict[str, tuple[int, int]] = {}

    async def async_close(self) -> None:
        """Close the HTTP session."""
        await self._session.close()

    def current_cookies(self) -> dict[str, str]:
        """Cookies as they stand now, including any Amazon has rotated."""
        found: dict[str, str] = {}
        for cookie in self._session.cookies.jar:
            if cookie.value is None:
                continue
            # Prefer the account-wide domain over host-only copies.
            if cookie.name in found and not cookie.domain.startswith("."):
                continue
            found[cookie.name] = cookie.value
        return found

    async def _get(self, url: str, *, referer: bool = True) -> tuple[int, str]:
        headers = {"Accept-Language": "en-GB,en;q=0.9"}
        if url.startswith(self._base):
            headers["Accept"] = "application/json, text/plain, */*"
            if referer:
                headers["Referer"] = f"{self._base}/kindle-library"
            if self._session_id_header:
                headers["x-amzn-sessionid"] = self._session_id_header
            if self._adp_token:
                headers["x-adp-session-token"] = self._adp_token
        try:
            resp = await self._session.get(
                url, headers=headers, allow_redirects=False, timeout=TIMEOUT
            )
        except Exception as err:  # curl_cffi raises its own error types
            raise KindleError(f"Request failed: {type(err).__name__}") from err
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if "signin" in location or "/ap/" in location:
                raise KindleAuthError("Amazon redirected to sign-in")
            raise KindleBlockedError(f"Unexpected redirect (HTTP {resp.status_code})")
        if resp.status_code == 401:
            raise KindleAuthError("HTTP 401")
        return resp.status_code, resp.text

    @staticmethod
    def _json(text: str) -> Any:
        try:
            return json.loads(text)
        except ValueError as err:
            lowered = text[:3000].lower()
            if "captcha" in lowered or "robot" in lowered:
                raise KindleBlockedError("Amazon showed a bot check") from err
            if "signin" in lowered or "sign-in" in lowered or "sign in" in lowered:
                raise KindleAuthError("Amazon returned a sign-in page") from err
            raise KindleBlockedError("Response was not JSON") from err

    async def async_get_library(self, count: int = 10) -> list[LibraryBook]:
        """Most recent books in the library (newest first)."""
        url = (
            f"{self._base}/kindle-library/search?query=&libraryType=BOOKS"
            f"&sortType=recency&querySize={count}"
        )
        status, text = await self._get(url)
        if status == 403:
            raise KindleAuthError("Library request forbidden (HTTP 403)")
        if status != 200:
            raise KindleError(f"Library request failed (HTTP {status})")
        data = _unwrap(self._json(text))
        items = data.get("itemsList") if isinstance(data, dict) else None
        if items is None:
            raise KindleBlockedError("Library response had no itemsList")
        if items and isinstance(items[0], dict) and items[0].get("asin"):
            self._page_asin = items[0]["asin"]
        new_session = self.current_cookies().get("session-id")
        if new_session:
            self._session_id_header = new_session
        books: list[LibraryBook] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("asin"):
                continue
            books.append(
                LibraryBook(
                    asin=item["asin"],
                    title=item.get("title") or item["asin"],
                    authors=_authors(item.get("authors")),
                    cover_url=item.get("productUrl"),
                    origin_type=item.get("originType"),
                    resource_type=item.get("resourceType"),
                )
            )
        return books

    async def async_register_device(self) -> None:
        """Get a reader session token.

        The current Cloud Reader embeds it in the book page, so no device
        token is needed. A pasted device token is the fallback.
        """
        self._adp_token = None
        if await self._session_from_page():
            return
        if not self._device_token:
            raise KindleDeviceTokenError("No reader session token on the page")
        url = (
            f"{self._base}/service/web/register/getDeviceToken"
            f"?serialNumber={self._device_token}&deviceType={self._device_token}"
        )
        self._adp_token = None
        status, text = await self._get(url)
        if status in (400, 403, 404):
            raise KindleDeviceTokenError(f"Device token rejected (HTTP {status})")
        if status != 200:
            raise KindleError(f"Device registration failed (HTTP {status})")
        data = _unwrap(self._json(text))
        token = data.get("deviceSessionToken") if isinstance(data, dict) else None
        if not token:
            raise KindleDeviceTokenError("No deviceSessionToken in response")
        self._adp_token = token

    async def _progress_get(self, url: str) -> tuple[int, str]:
        """GET where a 401 means a stale reader token, not signed-out cookies."""
        try:
            return await self._get(url)
        except KindleAuthError:
            return 401, ""

    async def _session_from_page(self) -> bool:
        """Read deviceSessionToken from the reader page HTML."""
        asin = self._page_asin
        url = f"{self._base}/?asin={asin}" if asin else f"{self._base}/kindle-library"
        status, text = await self._get(url)
        if status != 200:
            _LOGGER.debug("Reader page returned HTTP %s", status)
            return False
        match = _PAGE_TOKEN_RE.search(text)
        if not match:
            if "ap/signin" in text[:20000]:
                raise KindleAuthError("Reader page asked for sign-in")
            _LOGGER.debug("No deviceToken block on the reader page")
            return False
        try:
            data = json.loads(match.group(1))
        except ValueError:
            _LOGGER.debug("deviceToken block was not JSON")
            return False
        token = data.get("deviceSessionToken") if isinstance(data, dict) else None
        if not token:
            return False
        self._adp_token = token
        return True

    async def async_get_progress(self, asin: str) -> BookProgress:
        """Whispersync position for one book."""
        if not self._adp_token:
            if not self._page_asin:
                self._page_asin = asin
            await self.async_register_device()
        url = (
            f"{self._base}/service/mobile/reader/startReading"
            f"?asin={asin}&clientVersion={CLIENT_VERSION}"
        )
        status, text = await self._progress_get(url)
        if status != 200 or "ADP session" in text:
            # The reader session token may have expired; renew once.
            await self.async_register_device()
            status, text = await self._progress_get(url)
        if status in (401, 403) or "ADP session" in text:
            raise KindleDeviceTokenError(f"Progress request refused (HTTP {status})")
        if status != 200:
            raise KindleError(f"Progress request failed (HTTP {status})")
        info = _unwrap(self._json(text))
        if not isinstance(info, dict):
            raise KindleBlockedError("Progress response was not an object")
        last = info.get("lastPageReadData")
        position: int | None = None
        sync_time = None
        device_name = None
        if isinstance(last, dict):
            raw_pos = last.get("position")
            position = int(raw_pos) if raw_pos not in (None, "") else None
            sync_time = _sync_time(last.get("syncTime"))
            device_name = last.get("deviceName")
        start, end = 0, None
        meta_url = info.get("metadataUrl")
        if asin in self._metadata:
            start, end = self._metadata[asin]
        elif meta_url:
            start, end = await self._get_metadata(meta_url)
            if end:
                self._metadata[asin] = (start, end)
        return BookProgress(
            asin=asin,
            position=position,
            start_position=start,
            end_position=end,
            sync_time=sync_time,
            device_name=device_name,
        )

    async def _get_metadata(self, url: str) -> tuple[int, int | None]:
        status, text = await self._get(url, referer=False)
        if status != 200:
            return 0, None
        match = _JSONP_RE.search(text)
        try:
            meta = json.loads(match.group(1) if match else text)
        except ValueError:
            return 0, None
        start = int(meta.get("startPosition") or 0)
        end = meta.get("endPosition")
        return start, int(end) if end else None
