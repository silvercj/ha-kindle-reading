"""Constants for the Kindle Reading integration."""

from __future__ import annotations

DOMAIN = "kindle_reading"

CONF_REGION = "region"
CONF_COOKIES = "cookies"
CONF_DEVICE_TOKEN = "device_token"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_READING_SPEED = "reading_speed"
CONF_FINISHED_THRESHOLD = "finished_threshold"
CONF_BOOKS_TO_CHECK = "books_to_check"

# Minutes between polls.
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 15
# Kindle "positions" are roughly characters of text. An adult reading
# ~250 words a minute at ~6 characters a word covers ~1,500 a minute.
DEFAULT_READING_SPEED = 1500
DEFAULT_FINISHED_THRESHOLD = 97
# How many of the most recent books are checked for progress each poll.
DEFAULT_BOOKS_TO_CHECK = 5
# How many recent books the first run checks to seed finished books.
SEED_BOOKS = 25

# Cloud Reader host per Amazon marketplace.
REGIONS: dict[str, str] = {
    "uk": "read.amazon.co.uk",
    "us": "read.amazon.com",
    "ca": "read.amazon.ca",
    "de": "read.amazon.de",
    "fr": "read.amazon.fr",
    "es": "read.amazon.es",
    "it": "read.amazon.it",
    "au": "read.amazon.com.au",
    "in": "read.amazon.in",
    "jp": "read.amazon.co.jp",
}
DEFAULT_REGION = "uk"

STORAGE_VERSION = 1
