# Kindle Reading for Home Assistant

Kindle reading progress in Home Assistant, automatically. No manual logging.

Every Kindle syncs your place in each book to Amazon (Whispersync). This
integration reads that position from the same private endpoints the
[Kindle Cloud Reader](https://read.amazon.co.uk) uses, so it works across
every Kindle on your account. Whichever one you picked up last, Home
Assistant sees the same up-to-date progress.

> **Unofficial.** Amazon has no public Kindle API. This uses your own
> sign-in cookies against private endpoints. It is read-only (no book
> content, no purchases, no changes) and polls gently, but it may break
> when Amazon changes the Cloud Reader, and it is outside Amazon's terms.
> Use at your own risk.

## Sensors

| Entity | What it shows |
| --- | --- |
| `sensor.kindle_current_book` | Title of the book you read most recently (cover as picture; author, progress, last device and your last 5 books as attributes) |
| `sensor.kindle_current_book_progress` | % read in that book |
| `sensor.kindle_last_read` | When your position last synced |
| `sensor.kindle_last_read_on` | Which Kindle it synced from |
| `sensor.kindle_reading_time_today` | **Estimated** minutes read today, from how far you moved through your books |
| `sensor.kindle_progress_today` | % of a book read today (summed across books) |
| `sensor.kindle_books_finished_this_year` | Books that passed 90% this year (end notes and acknowledgements mean you rarely hit 100%) (list in attributes) |

Notes:

- A Kindle only syncs with Wi-Fi on. Progress shows up at the next poll
  (every 30 minutes by default) after the Kindle syncs.
- Reading time is an estimate: Kindle positions are roughly characters of
  text, divided by a reading speed (default 1,500 a minute). Tune it in
  the options if it runs high or low.
- Each poll checks your 5 most recent books. Until your 25 most recent
  are known (to find ones finished this year), it also checks 5 older
  ones per poll, so setup stays quick.

## Install (HACS)

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/silvercj/ha-kindle-reading` as **Integration**.
2. Download **Kindle Reading** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → Kindle Reading.

## Getting the cookie header (once, on a computer)

1. In Chrome or Edge on a computer, sign in at
   **https://read.amazon.co.uk** and open any book.
2. Press **F12** (Mac: ⌥⌘I), open the **Network** tab and reload the page.
3. Click the first request (the page itself, named like `?asin=...`).
   Under **Headers → Request Headers**, find **cookie**, right-click it and
   copy the value.
4. In Home Assistant add **Kindle Reading**, pick **Amazon.co.uk** and
   paste the cookie value. Leave **Device token** empty: the integration
   reads the reader's session token from the book page itself. (Only older
   Cloud Reader versions needed the `getDeviceToken` URL.)

Cookies normally last months. The integration saves any cookies Amazon
refreshes. When Amazon does sign the session out, Home Assistant shows a
**Reconnect** prompt: repeat steps 1-3 and paste the new cookie value.

## Credits

Endpoint details learned from
[transitive-bullshit/kindle-api](https://github.com/transitive-bullshit/kindle-api),
[Xetera/kindle-api](https://github.com/Xetera/kindle-api) and
[meeluh1/KindleTracker](https://github.com/meeluh1/KindleTracker).

MIT licence.
