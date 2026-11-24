"""Polling coordinator and daily reading tracker."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    BookProgress,
    KindleAuthError,
    KindleBlockedError,
    KindleClient,
    KindleDeviceTokenError,
    KindleError,
    LibraryBook,
)
from .const import (
    CONF_BOOKS_TO_CHECK,
    CONF_COOKIES,
    CONF_FINISHED_THRESHOLD,
    CONF_READING_SPEED,
    CONF_SCAN_INTERVAL,
    DEFAULT_BOOKS_TO_CHECK,
    DEFAULT_FINISHED_THRESHOLD,
    DEFAULT_READING_SPEED,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SEED_BOOKS,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Pause between per-book calls, to stay gentle with Amazon.
REQUEST_GAP = 1.0
# Older books checked per poll until the first SEED_BOOKS are known.
SEED_PER_POLL = 5


@dataclass
class Book:
    """A book with its latest progress."""

    asin: str
    title: str
    authors: list[str]
    cover_url: str | None
    percent: float | None
    position: int | None
    end_position: int | None
    last_read: datetime | None
    device: str | None

    def as_dict(self) -> dict[str, Any]:
        """Attributes-friendly view."""
        return {
            "title": self.title,
            "author": ", ".join(self.authors),
            "asin": self.asin,
            "progress": self.percent,
            "last_read": self.last_read.isoformat() if self.last_read else None,
            "last_read_on": self.device,
            "cover": self.cover_url,
        }


@dataclass
class KindleData:
    """Everything the sensors show."""

    current: Book | None
    recent: list[Book]
    reading_minutes_today: float
    progress_today: float
    books_today: list[str]
    finished_this_year: list[dict[str, Any]]
    last_poll: datetime
    extra: dict[str, Any] = field(default_factory=dict)


class KindleCoordinator(DataUpdateCoordinator[KindleData]):
    """Polls the Kindle Cloud Reader and tracks today's reading."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: KindleClient) -> None:
        """Set up the coordinator."""
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._state: dict[str, Any] | None = None

    @property
    def _speed(self) -> float:
        return float(self.config_entry.options.get(CONF_READING_SPEED, DEFAULT_READING_SPEED))

    @property
    def _threshold(self) -> float:
        return float(
            self.config_entry.options.get(CONF_FINISHED_THRESHOLD, DEFAULT_FINISHED_THRESHOLD)
        )

    @property
    def _books_to_check(self) -> int:
        return int(self.config_entry.options.get(CONF_BOOKS_TO_CHECK, DEFAULT_BOOKS_TO_CHECK))

    async def _load_state(self) -> dict[str, Any]:
        if self._state is None:
            self._state = await self._store.async_load() or {}
            self._state.setdefault("books", {})
            self._state.setdefault("finished", {})
            self._state.setdefault("day", None)
            self._state.setdefault("day_start", {})
            self._state.setdefault("seeded", False)
        return self._state

    async def async_state_snapshot(self) -> dict[str, Any]:
        """Stored tracking state, for diagnostics."""
        return dict(await self._load_state())

    async def _fetch(
        self, count: int, state: dict[str, Any]
    ) -> list[tuple[LibraryBook, BookProgress | None]]:
        seeding = not state["seeded"]
        library = await self.client.async_get_library(SEED_BOOKS if seeding else max(count, 10))
        targets = library[:count]
        if seeding:
            # Backfill older books a few per poll, so setup stays quick.
            unseen = [b for b in library[count:SEED_BOOKS] if b.asin not in state["books"]]
            targets += unseen[:SEED_PER_POLL]
            if len(unseen) <= SEED_PER_POLL:
                state["seed_done"] = True
        results: list[tuple[LibraryBook, BookProgress | None]] = []
        for index, item in enumerate(targets):
            if index:
                await asyncio.sleep(REQUEST_GAP)
            try:
                progress = await self.client.async_get_progress(item.asin)
            except (KindleAuthError, KindleDeviceTokenError, KindleBlockedError):
                raise
            except KindleError as err:
                _LOGGER.debug("Progress for %s failed: %s", item.asin, err)
                progress = None
            _LOGGER.debug(
                "Book %s %r origin=%s -> %s",
                item.asin,
                item.title[:40],
                item.origin_type,
                None
                if progress is None
                else f"pos={progress.position} end={progress.end_position} "
                f"pct={progress.percent} sync={progress.sync_time} dev={progress.device_name}",
            )
            results.append((item, progress))
        _LOGGER.debug("Library returned %d books, checked %d", len(library), len(targets))
        return results

    async def _async_update_data(self) -> KindleData:
        state = await self._load_state()
        count = self._books_to_check
        try:
            fetched = await self._fetch(count, state)
        except KindleAuthError as err:
            raise ConfigEntryAuthFailed(
                "Amazon sign-in expired. Paste fresh cookies to reconnect."
            ) from err
        except KindleDeviceTokenError as err:
            raise ConfigEntryAuthFailed(
                "Kindle device token was rejected. Paste a fresh one to reconnect."
            ) from err
        except KindleBlockedError as err:
            raise UpdateFailed(f"Amazon blocked the request: {err}") from err
        except KindleError as err:
            raise UpdateFailed(str(err)) from err

        self._save_rotated_cookies()

        now = dt_util.now()
        today = now.date().isoformat()
        if state["day"] != today:
            # New day: today's reading is measured from where each book stood.
            state["day"] = today
            state["day_start"] = {
                asin: info.get("position")
                for asin, info in state["books"].items()
                if info.get("position") is not None
            }

        books: list[Book] = []
        for item, progress in fetched:
            known = state["books"].get(item.asin, {})
            if progress is None:
                # Keep the last good reading rather than dropping the book.
                last = known.get("last_read")
                book = Book(
                    asin=item.asin,
                    title=item.title,
                    authors=item.authors,
                    cover_url=item.cover_url,
                    percent=known.get("percent"),
                    position=known.get("position"),
                    end_position=known.get("end_position"),
                    last_read=dt_util.parse_datetime(last) if last else None,
                    device=known.get("device"),
                )
            else:
                last_read = progress.sync_time
                if last_read is None:
                    # No sync time from Amazon: if the place moved, it was just read.
                    prev = known.get("last_read")
                    moved = (
                        progress.position is not None
                        and known.get("position") is not None
                        and progress.position != known.get("position")
                    )
                    if moved:
                        last_read = now
                    elif prev:
                        last_read = dt_util.parse_datetime(prev)
                book = Book(
                    asin=item.asin,
                    title=item.title,
                    authors=item.authors,
                    cover_url=item.cover_url,
                    percent=progress.percent,
                    position=progress.position,
                    end_position=progress.end_position,
                    last_read=last_read,
                    device=progress.device_name,
                )
            books.append(book)
            self._track(state, book, known, today)

        if state.pop("seed_done", False):
            state["seeded"] = True
        await self._store.async_save(state)

        reading = [b for b in books if b.last_read]
        reading.sort(key=lambda b: b.last_read, reverse=True)  # type: ignore[arg-type,return-value]
        current = reading[0] if reading else (books[0] if books else None)

        delta_positions = 0
        progress_today = 0.0
        books_today: list[str] = []
        for asin, info in state["books"].items():
            start = state["day_start"].get(asin)
            pos = info.get("position")
            if start is None or pos is None or pos <= start:
                continue
            delta_positions += pos - start
            if info.get("end_position"):
                progress_today += (pos - start) / info["end_position"] * 100
            books_today.append(info.get("title", asin))

        year = str(now.year)
        finished = [
            {"title": v.get("title"), "author": v.get("author"), "finished": v.get("finished")}
            for v in state["finished"].values()
            if str(v.get("finished", "")).startswith(year)
        ]
        finished.sort(key=lambda v: v["finished"] or "", reverse=True)

        return KindleData(
            current=current,
            recent=reading or books,
            reading_minutes_today=round(delta_positions / self._speed, 1),
            progress_today=round(progress_today, 1),
            books_today=books_today,
            finished_this_year=finished,
            last_poll=now,
        )

    def _track(self, state: dict[str, Any], book: Book, known: dict[str, Any], today: str) -> None:
        """Remember positions and finished books between polls and restarts."""
        first_seen = not known
        info = {
            "title": book.title,
            "author": ", ".join(book.authors),
            "percent": book.percent,
            "position": book.position,
            "end_position": book.end_position,
            "last_read": book.last_read.isoformat() if book.last_read else None,
            "device": book.device,
        }
        state["books"][book.asin] = info
        if book.position is not None and book.asin not in state["day_start"]:
            prev = known.get("position")
            if prev is not None:
                state["day_start"][book.asin] = prev
            elif first_seen and state["seeded"]:
                # A book we have never seen, opened for the first time today,
                # counts from the start; otherwise from where it is now.
                read_today = book.last_read and dt_util.as_local(book.last_read).date().isoformat() == today
                state["day_start"][book.asin] = 0 if read_today and (book.percent or 0) < 5 else book.position
            else:
                state["day_start"][book.asin] = book.position
        if (
            book.percent is not None
            and book.percent >= self._threshold
            and book.asin not in state["finished"]
        ):
            when = book.last_read or dt_util.utcnow()
            state["finished"][book.asin] = {
                "title": book.title,
                "author": ", ".join(book.authors),
                "finished": dt_util.as_local(when).date().isoformat(),
            }

    def _save_rotated_cookies(self) -> None:
        """Keep any cookies Amazon refreshed, so the sign-in lasts longer."""
        stored: dict[str, str] = self.config_entry.data.get(CONF_COOKIES, {})
        current = self.client.current_cookies()
        changed = {
            name: value
            for name, value in current.items()
            if name in stored and stored[name] != value and value
        }
        if changed:
            _LOGGER.debug("Amazon refreshed cookies: %s", ", ".join(sorted(changed)))
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_COOKIES: {**stored, **changed}},
            )
