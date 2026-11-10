# Open threads

Where the work stands, for whoever picks it up next. This is the one place for state rather than
design: when you close a thread, delete it; when you leave one open, add it with its evidence.

Last updated at 0.37.0, 2026-11-10.

## Development sequence after 0.34.2

- **0.35.0:** issued-prediction journal, shared observation grading, four
  performance sensors, collection status and per-provider capture/read-back
  timestamps. The projection remains baseline-0.34.2. Live journal grading
  needs the first actual checkpoints and their completed observation windows.
- **0.36.1:** the product is English only, as explicitly clarified by the
  maintainer. The language selector and Arabic resources from 0.36.0 are removed;
  that release was published but never installed on the reference house. Keep
  the quality section and actionable configuration Repairs in English. Chat
  with the maintainer remains Arabic. Actual dashboard rendering is unverified.
- **0.37.0 morning experiment:** a normalized three-part daylight shape, compared
  silently over 14 new complete mornings. It must not add false fill calls,
  must reduce missed fill days, and must not worsen the night gate. Until that
  evidence exists, baseline-0.34.2 remains active. The journal stores separate
  candidate snapshots and diagnostics expose every paired gate. Promotion is a
  subsequent reviewed change, never inferred from the development replay.
  The old reference download lacks independent daylight bounds; requiring zero
  shoulders alone correctly fell back on every day. New diagnostics provide
  independent local sunrise/sunset windows so sparse-provider horizons can be
  checked without assuming missing readings are zero.
- **Evening interval:** 80% empirical range for the frozen 20:00 forecast,
  needing 30 calibration nights and 14 later complete shadow observations
  with at least 80% coverage. It is not a band around the live projection.
- **Second installation:** synthetic variants and a common comparison report
  can be prepared now; no independent house's data is available. Generalization
  and 1.0 readiness cannot be asserted from the reference house alone.
- Keep the owner's existing evening/morning scheduled checks active. Diagnostics
  and journal downloads stay private; test fixtures remain synthetic.

## English-only installation verified

- 0.36.1 was installed via HACS and loaded after restart on 2026-11-10. Core
  reached RUNNING. Collection status is ok, archive read-back succeeds, the
  outlook refreshes, and Solar Sanity has no warnings/errors. The card resource
  points to `?v=0.36.1`. 0.36.0 was never installed on this house.
- Synthetic phone/desktop checks confirm English card output on an RTL page
  even with a legacy Arabic language option. Actual authenticated dashboard
  rendering remains unverified. The current journal has no due checkpoints yet.

## Installed journal baseline

- 0.35.0 was installed through HACS on 2026-11-10 and loaded after a Core restart.
  Core reached RUNNING, with no Solar Sanity warnings/errors. Runtime
  diagnostics confirm a valid outlook and collection status `ok`, with
  a matching archive read-back five seconds after capture. No checkpoints had
  occurred since installation, so metrics correctly remain unknown with zero
  samples. Actual checkpoint persistence and completed-window grading remain
  pending the scheduled evening/morning observations.
- The existing two scheduled checks remain active and now verify the installed
  stable version and journal instead of requiring the older 0.34.2 string.

## Observe 0.34.2 through a real night and morning

- **What:** the eight audit findings are fixed and have synthetic regression
  coverage. The reference archive still passes: 29 nights, mean absolute
  lowest error 2.47 points, reserve right on 78 of 85 projections.
- **Delivery:** one stable release, followed by installation through HACS and
  verification on the reference Home Assistant. Its history and settings must
  survive, forecast capture must continue, and live read-outs must keep moving.
- **Installed on 2026-11-10:** HACS installed v0.34.2 and a Core restart loaded
  0.34.2, confirmed by runtime diagnostics. All five input channels are readable,
  the six corrected forecast sensors have values, and the outlook recomputes.
  The 766 stored buckets and both 386-row forecast series survive without loss;
  capacity, reserve and target remain 30 kWh, 35% and 100%. Startup forecast
  capture succeeds and there are no Solar Sanity warnings or errors.
  Core reaches RUNNING and the existing card resource updates itself to
  `?v=0.34.2` after the started event. The served card's SHA-256 matches the
  release asset exactly. After the recorder queue settles, `latest` updates
  while `dayahead` stays unchanged. Tomorrow's archived 50.092 kWh multiplied
  by 0.45 agrees with the corrected sensor's 22.54 kWh.
- **Visual checks:** synthetic desktop/phone cards passed in light/dark and
  LTR/RTL. Rendering the actual dashboard remains unverified: the MCP screenshot
  feature is disabled, the Chrome connection was unavailable, and the in-app
  browser reaches a login form without an authenticated session.
- **Pending:** verify the periodic forecast capture and the first post-update
  night and morning against actual charge readings. Do not describe that
  observation as complete based on the historical replay alone.

## Grade the factor by the kind of day on the next download

- **What:** 0.32.0 corrects the forecast with a factor learned mostly from days whose forecast
  looked like the one ahead (`ForecastScale.for_day`).
- **Evidence so far** is the reference installation's month up to 2026-11-09. Graded each day on
  what was learned before it, the sunny-day bias went from +26% to +8% and the hourly error from
  42% to 36%.
- **Next:** the maintainer was asked for a diagnostics download around 2026-11-15. Run the
  backtest as `AGENTS.md` shows. The gate must still pass: at least 14 nights, mean absolute
  lowest error at most 5, reserve calls at least 85%.
- **State at 2026-11-10, after the 0.34.2 night-window correction:** 29 nights,
  2.47, 78 of 85. The earlier 2.44 becomes slightly larger because the night
  includes the inverter until solar covers it too; reserve decisions do not change.

## Sunny mornings may be under-predicted

- **2026-11-09, a forecast of 48 kWh:** the 08:00 projection said the battery would not fill,
  and it filled at 13:30. Over the hours the battery had room, the roof delivered 0.53 of the
  forecast while the factor used was 0.43. The morning hours ran at 0.8 to 1.25 of the
  forecast.
- **The backtest's mornings line, on the 2026-11-10 download, before 0.34.2:**
  - with the factor by the kind of day: "filled, predicted not" on 6 of 29 mornings;
  - with one factor (`--one-factor`): 5;
  - "predicted full, did not fill": 2 either way.

  So the 08:00 projection misses fill days whichever factor it uses. The kind of day only
  moved the timing on the days it calls right, from a median of 81 minutes early to 2 minutes
  late.
- **0.34.2 excludes three incomplete mornings:** 26 complete mornings remain;
  six missed fill days and two false fill calls. The kind-of-day run has only
  two correctly timed fill days, with a median eight minutes late. The earlier
  29-morning count included missing maximum readings in its observation windows.
- **One day is not evidence.**
  - First check whether the next download repeats it.
  - A per-hour shape on top of the kind-of-day factor is the obvious candidate. Per-hour
    factors alone did no better than one factor when tried in November 2026: an hourly error
    of 41.5-41.8% against 41.8%.
  - Any change must pass the gate and must not break the reference installation's night
    figures.

## A recurring morning load on the reference installation

- **What:** about 3 kWh in the 06:00 local hour on 2026-11-08 and 2026-11-10. The battery drops
  about ten points in that hour.
- **Status:** the learned load profile includes it, and the night figures were right on
  2026-11-09 (predicted 52.7%, actual 53%).
- **Action:** none, unless the maintainer wants it pointed out.

## The battery power option is not set yet

- **What:** 0.34.0 added an optional battery power sensor (`OPT_BATTERY_POWER`) for the battery
  card's live line.
- **Why it matters:** the reference installation's battery channels are energy counters, so its
  live line shows no battery until the option is set.
- **Status:** the maintainer was asked to set it and to check its sign.

## Dependabot PR 106

- **What:** a dev-dependency bump, open since 2026-10-20.
- **Rule:** Dependabot PRs are not merged as opened. Redo the bumps as one `chore/` PR through
  the full gate, then close 106.

## Not decided

- **Card text is English only** (ARCHITECTURE risk 7). The maintainer reads Arabic, and the
  cards are right-to-left-safe but not translated. Ask before starting.
- **Auto-merge.** GitHub auto-merge and required checks on `main` are off. The maintainer was
  given the two `gh api` commands to turn them on and has not yet. Until then, merge by hand
  when CI is green.
