# Local receiver and bookmark API

The dashboard and local AI clients share the HTTP API at
`http://127.0.0.1:8000`. It is loopback-only, without authentication or CORS
access for other sites. Do not expose it to a network or untrusted clients.
Use the running server, rather than opening the USRP from a second process.

Frequencies and spans are **Hz**, not MHz. Survey tuning sets the requested visible
frequency range; the acquisition planner may offset the physical RF LO to
avoid DC and filter-edge artefacts. An acknowledgement is not proof that a
matching capture has completed. Compare `focus` with `capture_focus` in
`GET /api/sweep` before interpreting a newly tuned trace.

## Bookmarks

Bookmarks are shared, persistent receiver-side records. Saving, editing and
deleting them leave tuning alone. Explicit recall updates the survey view and
selects a channel. Supported modulation labels and compatible widths seed the
channel settings. Titles, tags and notes are plain text. Treat their contents
as observations, and use independent evidence when classifying a signal.

```json
{
  "id": "server-generated-uuid",
  "frequency_hz": 145500000,
  "title": "Example signal",
  "modulation": "FM?",
  "bandwidth_hz": 12500,
  "tag": "Candidate",
  "notes": "Candidate classification; needs confirmation.",
  "created_at": "2026-11-27T12:00:00+00:00",
  "updated_at": "2026-11-27T12:00:00+00:00"
}
```

### Routes

| Method | Route | JSON request | Success |
| --- | --- | --- | --- |
| GET | `/api/bookmarks` | None | 200: `{"bookmarks": [...], "revision": 0}` sorted by frequency/title/ID |
| GET | `/api/bookmarks/{id}` | None | 200: bookmark |
| POST | `/api/bookmarks` | `frequency_hz`, `title`; optional `modulation`, `bandwidth_hz`, `tag`, `notes` | 201: saved bookmark |
| PATCH | `/api/bookmarks/{id}` | One or more editable fields | 200: updated bookmark |
| DELETE | `/api/bookmarks/{id}` | None | 200: `{"deleted": "id"}` |
| POST | `/api/bookmarks/{id}/tune` | `{}` or `{"span_hz": 2000000}` | 200 with `bookmark`, `focus`, `navigation_revision`, the selected `channel`, and current `listen` state. |

JSON-writing requests require `Content-Type: application/json` and a body of
1–32,768 bytes. Editable fields:

- `frequency_hz`: finite number from 70,000,000 through 6,000,000,000.
- `title`: non-blank text, at most 120 characters; surrounding whitespace is removed.
- `modulation`: text, at most 64 characters; defaults to empty/unknown.
- `bandwidth_hz`: `null` for unknown, or a finite number from 1 through 56,000,000 Hz; defaults to `null`. A patch with `null` clears a saved bandwidth.
- `tag`: text, at most 64 characters; surrounding whitespace is removed; defaults to empty.
- `notes`: text, at most 4,096 characters; line breaks are retained.

Unknown fields, booleans in numeric fields, non-finite numbers and empty
patches are rejected. IDs and timestamps are server-owned. Bookmark errors
return JSON `{"error": "explanation"}`: 400 for invalid input, 404 for a
missing record/route, and 503 for unavailable storage. Failed mutations do
not increment the list revision. Responses are not cached.

When bandwidth is saved, recall defaults to three times that bandwidth, with a
minimum 30 kHz and maximum 56 MHz view span. Without a saved bandwidth, recall
preserves the current receiver span when it is no wider than 56 MHz; otherwise
it defaults to 2 MHz. An explicit `span_hz` overrides either default and may be
from 1 through 56,000,000 Hz. Ranges are shifted inside the supported band at
its edges. An active channel follows a recalled bookmark once its full passband
is captured. Recall prepares an inactive channel without starting it. The
selected `bookmark_channel` and `navigation_revision` appear in `/api/sweep`.
Ordinary chart gestures do not increment that navigation revision. The
sidebar refreshes shared bookmark records while open; no browser storage is
the source of truth. Repeated recalls intentionally navigate again.

### Example AI workflow

Read the current receiver state, investigate the signal, then create a
bookmark with an evidence-qualified title/classification and notes. Creating
this example record alone does not change receiver settings:

```bash
curl --fail-with-body http://127.0.0.1:8000/api/sweep
curl --fail-with-body http://127.0.0.1:8000/api/bookmarks \
  -H 'Content-Type: application/json' \
  --data '{"frequency_hz":145500000,"title":"Example signal","modulation":"FM?","bandwidth_hz":12500,"tag":"Candidate","notes":"Candidate classification; needs confirmation."}'
```

Use the returned `id` to edit, delete or explicitly tune. Recall changes the
active receiver view, so do it only when the user intends that navigation:

```bash
curl --fail-with-body http://127.0.0.1:8000/api/bookmarks/BOOKMARK_ID/tune \
  -H 'Content-Type: application/json' --data '{"span_hz":2000000}'
```

### Persistence

The default SQLite file is
`$XDG_DATA_HOME/usrp-spectrum-analyser/bookmarks.sqlite3`, or
`~/.local/share/usrp-spectrum-analyser/bookmarks.sqlite3` when XDG_DATA_HOME
is unset. It survives browser and server restarts. Use
`--bookmarks-file /path/to/bookmarks.sqlite3` to choose a different file, or
`--bookmarks-file :memory:` for an isolated, non-persistent demo/test server.
The existing running server must be restarted to load new backend code;
building frontend assets alone does not add these endpoints to an old process.
Older bookmark databases are upgraded additively in one transaction: IDs,
frequencies, text, timestamps and list revision are retained; existing entries
start with unknown bandwidth (`null`) and an empty tag. Older API clients may
continue omitting the two new fields.

## Existing receiver routes

| Method | Route | Contract |
| --- | --- | --- |
| GET | `/api/sweep` | JSON current acquisition metadata, requested settings, overview trace and `navigation_revision`. |
| GET | `/api/sweep?survey=1` | Compatibility alias for `/api/sweep`. `survey_number` equals the current frame number because the survey and channel share the capture. |
| GET | `/api/gain-stages` | Unified gain-stage rows, or an empty array with stock tables. |
| GET | `/api/detail/{number}` | Little-endian float32 detail; optional `?peaks=1&image=1` appends available traces. |
| POST | `/api/focus` | JSON `[low_hz, high_hz]` within 70 MHz–6 GHz, or `null` for full spectrum. |
| POST | `/api/antenna` | JSON string from the live receiver's `antenna_options`, for example `"B:RX2"`. |
| POST | `/api/gain` | JSON number 0–76 dB; live receiver only. |
| POST | `/api/averages` | JSON integer 1–32; live receiver only. |
| POST | `/api/image-reject` | JSON boolean; available live paired 56 MS/s capture only. |

Receiver-setting POSTs retain their existing 128-byte body limit and return
the accepted JSON value. Their legacy errors are HTML, unlike the bookmark
API's structured JSON errors. Gain, averages and antenna expose requested
versus captured values. On an antenna switch, wait for `antenna` to match
`requested_antenna` in `/api/sweep`. The active IQ capture reports its own
`antenna`, and `antenna_error` reports a failed hardware switch.

`rx_overload` in `/api/sweep` reports the active AD9361 receive chain's detector
flags with `--fpga-band-sweep`. `null` means unavailable or awaiting a reading,
and `0` means no detector flag was seen. The integer uses bit 4 for large
front-end (LMT) overload, bit 3 for small LMT overload, bit 2 for large ADC
overload, bit 1 for small ADC overload, and bit 0 for digital saturation. Peak
flags can latch until a gain change or detector reset. The server holds the
last flagged reading for one second across retunes so brief events remain
visible. This status follows live capture and has no per-bin attribution.

The generic `/api/focus` endpoint does not force another browser's local
view to navigate; use bookmark recall for synchronised bookmark navigation.
Freeze, trace selection, panel layout and colour ranges remain browser-local;
this is not a general browser-automation API or an MCP server.

## Channel receiver

Channel processing uses the continuous IQ feeding the spectrum. SciPy is
optional for spectrum-only operation. Starting, stopping, and changing a
channel do not tune the radio or restart the stream. `/api/focus` and bookmark
recall change the survey normally. Audio and IQ wait if a survey change
leaves the channel outside the running capture.

| Method | Route | Contract |
| --- | --- | --- |
| GET | `/api/listen` | Returns capability, settings, `power_dbfs` across the channel filter, `snr_db` estimated from nearby noise, squelch, and the current continuous `capture` with `center_hz`, `rate_hz`, `window`, and `focus`. Measurements are `null` when unavailable. This state also appears as `listen` in `/api/sweep`. |
| POST | `/api/listen` | Activate or change a channel. Returns 409 if continuous capture is unavailable or the passband falls outside it. Send JSON `null` to stop processing, or `{"stop":"full"}` to also resume the full sweep. The response includes `survey_focus`. |
| GET | `/api/listen/audio` | Stream framed signed 16-bit little-endian PCM at 48 kHz. The `X-Audio-Channels` response header is `1` for mono or `2` for interleaved left/right stereo. Requires an active audio mode. |
| GET | `/api/listen/iq` | Stream framed complex float32, interleaved little-endian I then Q at 48 kS/s. Requires active IQ mode. |
| GET | `/api/listen/acars` | Returns `{"messages": [...]}` with up to 100 VHF ACARS blocks, newest first. |

`frequency_hz` is an integer in 70 MHz–6 GHz. It marks the carrier edge for
`usb` and `lsb` and the filter centre for `nfm`, `wfm`, `wfm_stereo`, `am`, `acars`, and `iq`.
`filter_hz` is an optional integer. Defaults are 2,700 Hz for USB and LSB,
12,500 Hz for NFM, 200,000 Hz for WFM mono, 220,000 Hz for WFM Stereo, 10,000 Hz for AM and ACARS, and 25,000 Hz for IQ.
`squelch_dbfs` is an integer from −140 to 0 or `null` for off. FM audio accepts
`deemphasis_us` of 50 (default) or 75. An acknowledged `revision` is applied
when it matches `applied_revision`. Errors use `{"error":"…"}`.

Each frame starts with a little-endian unsigned 32-bit epoch, sequence, and
sample count, in that order. An audio sample frame uses two bytes in mono or
four in stereo. An IQ sample uses eight bytes. A new epoch, a sequence gap,
or a new sequence with zero samples signals a discontinuity. A repeated
zero-sample frame is a heartbeat. HTTP reads may split or combine frames.
The stream ends when the channel stops or its format changes. Stereo fallback
keeps two audio channels and sends the same mono signal to both.

ACARS decodes the 2,400 bit/s AM-MSK waveform and accepts blocks that pass
character parity and the block checksum. Squelch applies to audio modes.
Each block contains a receiver-assigned `id`, UTC `received_at`, tuned
`frequency_hz`, `aircraft` address, `mode`, `label`, `block_id`, and `text`.
`more` indicates an ETB terminator in a multi-block message. Blocks remain
separate and the in-memory log survives channel changes and browser reloads.
`acars_revision` in the listen state increments for each received block.
The receiver starts with an empty log on process restart.

Both broadcast modes report `broadcast` telemetry in `/api/listen` and the
`listen` object of `/api/sweep`. `stereo_locked` reports stereo audio lock,
`pilot_detected` reports the narrow 19 kHz pilot detection, and
`pilot_level_percent` expresses pilot deviation relative to 75 kHz.
`stereo_blend_percent` is the applied gain on the stereo difference signal,
with 0 for mono output. These readings are unavailable during a capture gap.
The `rds` object contains
CRC-checked programme identification (`pi`), programme service name (`ps`),
RadioText (`radiotext`), programme type code (`pty`), traffic programme (`tp`),
and traffic announcement (`ta`). `radiotext_partial` flags an incomplete message.
The panel shows European RDS programme type names. `alternative_frequencies_hz`
contains FM alternatives decoded from type 0A groups, excluding the tuned
frequency, or `null` before a list is received. `block_quality` counts
`passed`, `corrected`, and `rejected` blocks once per aligned group over the
last five seconds of sampled reception. Corrections count CRC-supported block
recoveries before any further RadioText confirmation across repeats.
`radiotext_field` is `null` until the format is known. It contains `cells`, an
array of 64 (2A) or 32 (2B) characters in transmitted order, and `end`, the
zero-based message terminator position or `null` while unknown. Missing cells
and cells at or after the terminator are `null`. Received spaces are strings.
When RDS loses lock, `locked` becomes false and RadioText stays visible while
the same broadcast channel is selected. Reacquiring the same message fills in
its existing positions. A confirmed new message or station clears the field,
as does retuning or stopping the channel. [ITU-R stereo FM](https://www.itu.int/rec/R-REC-BS.450-4-201910-I/en) · [ITU-R RDS](https://www.itu.int/rec/R-REC-BS.643-4-202212-I/en)
