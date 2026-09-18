# Design: Recording UX + STT reliability + Google-Calendar-like calendar

Date: 2026-09-17
Status: Approved
Scope: anotherme-plugin-stt, anotherme-plugin-chat (UI touch-up), anotherme-plugin-diary (UI touch-up), anotherme-plugin-calendar (backend + UI)

## 1. Recording bar (stt plugin, global component)

The stt plugin already declares `ui.component: true`, so the host injects its
HTML/JS globally (shell.js `loadComponents`). The stt `ui/index.html` gains a
fixed floating bar (bottom center, above input areas), driven by
`Alpine.store('voice')`:

- **recording** state: red pulsing dot, 5 live waveform bars (AnalyserNode RMS
  levels via requestAnimationFrame), elapsed timer, target label
  ("Chat"/"Diary"), Stop and Cancel buttons.
- **transcribing** state: spinner + "Transcribing…" until the response
  arrives; then toast result and hide the bar.
- **Cancel** discards audio without transcription.

`Alpine.store('voice')` shape: `{ target, time, isRecording, isTranscribing,
level, levels }`.

Chat and diary drop their tiny inline `recording-timer` span (the bar
replaces it); the mic button keeps its `recording` class.

## 2. STT cutoff fixes (stt plugin `ui/index.js`)

1. `mediaRecorder.start(1000)` — flush chunks every second so mobile browser
   throttling cannot lose the tail of the audio.
2. Race fix: `onstop` snapshots chunks (`const chunks = this.audioChunks;
   this.audioChunks = []`) before processing; `startRecording` awaits any
   in-flight stop (`_stopping` promise) so a quick re-tap cannot wipe the
   previous recording's chunks.
3. Truncation detection: compare recorded elapsed seconds to the API's
   returned `duration`; if `duration < 70%` of elapsed, warn:
   "Transcription may be incomplete (recorded M:SS, got M:SS)".

## 3. Calendar backend

Schema v2 (idempotent, guarded ALTERs — PRAGMA column check before
registering, same pattern as chat migration fix):

- `recurrence TEXT` — NULL | 'weekly' | 'monthly' | 'yearly'
- `end_date TEXT` — for normal events: multi-day span end; for recurring:
  series end (last occurrence date). NULL = single day / forever.
- `color TEXT` — palette key: tomato, flamingo, banana, sage, basil, peacock,
  blueberry, lavender, graphite (NULL = default blue).
- `exceptions TEXT` — JSON array of "YYYY-MM-DD" dates skipped by a recurring
  series (used by delete-this-occurrence).

### API contract

`GET /plugins/calendar/events?from&to` returns expanded occurrences:

```json
{"events": [
  {"id": "uuid", "title": "...", "date": "2026-09-20", "time": "14:30",
   "note": null, "color": "tomato", "recurrence": "weekly",
   "end_date": null, "source": "manual",
   "is_occurrence": true, "series_date": "2026-09-06"}
]}
```

- Single-day event: one row, `is_occurrence: false`, `series_date: null`.
- Multi-day (end_date > date): one row per day in range, `is_occurrence: true`.
- Recurring: one row per occurrence (skipping exceptions, <= end_date),
  `is_occurrence: true`, `series_date` = anchor date. Cap 366 occurrences.
- `id` is always the DB row id (stable across a series).

`POST /events` and `PUT /events/{id}` accept `recurrence`, `end_date`,
`color` (validated: recurrence enum, end_date >= date, color in palette).

`DELETE /events/{id}?date=YYYY-MM-DD`:
- with `date` + recurring → append to exceptions ("this event only")
- with `date` + not recurring → 400
- without `date` → delete the whole series

Occurrence expansion is a pure function (unit-tested):
- weekly: same weekday, step 7 days
- monthly: same day-of-month; months lacking it are skipped (Feb 30 → skip;
  Feb 29 only in leap years)
- yearly: same month/day (Feb 29 only in leap years)

AI tools: `create_event` gains `recurrence`/`end_date`/`color`; `delete_event`
gains optional `date` ("delete only the occurrence on this date");
`list_events` output marks recurring events.

## 4. Calendar UI (GCal-like)

- **Toolbar**: ‹ Today ›, period label, Month | Week | Agenda switcher,
  search box, + button.
- **Month view** (default): 7-column grid, today highlighted, colored chips
  (dot + title/time), "+N more" overflow; click day → day detail below; drag
  chip to another day = reschedule (PUT date); click chip = edit dialog;
  drag on touch falls back to tap-to-edit.
- **Week view**: hourly grid 0–23h, all-day row on top (multi-day events
  live there), events positioned by time (height from 30-min default), click
  empty slot = create with date/time prefilled, drag = reschedule date+time
  (15-min snap).
- **Event dialog** (create/edit): title, date, time, end date (multi-day),
  repeat (none/weekly/monthly/yearly) + until, color swatches, note; delete
  offers "this event" vs "whole series" for recurring.
- **Agenda view**: existing list with color dots + search filter.
- Mobile: dialogs as bottom sheets, week view horizontally scrollable.

No host changes, no new dependencies. Vanilla Alpine + CSS in plugin ui/.

## Testing

- stt: unit tests for the race-guard logic where feasible (chunk snapshot),
  manual verification for audio levels (browser-only).
- calendar backend: unit tests for expansion (weekly/monthly/yearly,
  exceptions, end_date, multi-day, cap), API tests for new fields, occurrence
  delete, validation errors; migration test (v1 → v2 with data).
- calendar UI: manual (no browser harness in plugin repos).
