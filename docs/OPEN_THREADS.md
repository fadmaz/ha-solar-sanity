# Open threads

Where the work stands, for whoever picks it up next. This is the one place for state rather than
design: when you close a thread, delete it; when you leave one open, add it with its evidence.

Last updated for 0.49.0, 2026-11-11.

## Latest delivery and operational acceptance

- 0.48.0 adds installation-scoped reserve and measured surplus signals and
  notification blueprints. The reference installation can exercise reserve and
  optional estimated notifications. Field validation of measured readiness
  requires a separate installation with live export power.

- The Nov 11 read-only check found a real evening issue still pending after its
  window closed. Recorder returned ten of eleven required hours when queried
  from the issue second, and all eleven when queried from the hour boundary.
  The private observation confirms a 1.1-point error and a correct reserve
  call. 0.43.1 aligns journal queries and restricts partial-hour extremes to
  the issue window. The 0.43.1 HACS restart preserved both genuine issues;
  the evening issue graded automatically with the independently checked
  1.1-point error and correct reserve call. Archive read-back succeeded.
- 0.44.0 adds a separate hourly coverage observation and the charge minimum
  between expected coverage and a later observed hour. The reference case
  appeared in the History response after backfill, and the completed coverage
  observation survived a second restart. Authenticated dashboard rendering
  remains to be verified.
- 0.45.0 adds an installation-scoped Setup Check to the tools card, including
  for solar-only installations. A second independent installation remains
  needed to verify onboarding and confirm that the readiness signals match
  different equipment and measurement configurations.
- 0.46.0 adds optional manual charge power and inverter draw settings with a
  read-only comparison in What if. Auto retains the baseline projection and
  its journal context. The reference deployment checks Auto and previews.
- 0.47.0 adds a preferred provider per installation and labels the sources
  actually used in Setup. A multi-provider failover is covered by synthetic
  fixtures. A real multi-provider installation is still needed for field
  verification.
- The four approved stages shipped independently: History/tools in 0.40.0
  (PR132), What if in 0.41.0 (PR133), Changes in 0.42.0 (PR134), and optional
  alternate schedules in 0.43.0 (PR135). Each was merged with a merge commit,
  its branch deleted and a stable release published after CI passed both
  HA 2025.11.0 and 2026.2.3. The final code passes 717 Python tests at each
  version and 203 card tests. Local full gates pass 419 Python tests with nine
  HA modules skipped, TypeScript, build, cards and the size budget.
- **Prior 0.43.0 verification.** HACS install/restart and Core RUNNING
  were verified. Archive read-back succeeded at 16:07:35 UTC, the resource used
  `?v=0.43.0` and the outlook advanced to 16:10:51 UTC. Collection is ok and the
  live system log contains no Solar Sanity warnings/errors. All 772 prior rows,
  dayahead contents and 30/35/100 settings survived the restart. The day list is
  empty, with zero pairs and the load experiment inactive/not collecting.
- The current version's native simulation again returned identical zero-load
  curves and exactly 1 kWh for 1000 W/60 minutes. Some heavy MCP diagnostics/log
  requests failed to connect after startup; bounded entity reads and the native
  system-log command supplied fresh operational evidence without changing any
  connection, authentication or browser protections. This connector limitation
  must not be confused with a Solar Sanity fault.
- Earlier live acceptance is verified: 0.41.0 produced identical zero-load
  curves and exactly 1 kWh for a 1000 W/60 minute scenario. 0.42.0 compared two
  real issues at 15:44/15:49 UTC over their common future. Both returned to Core
  RUNNING with fresh archive read-back, advancing sensors and matching resources.
  No Solar Sanity warnings/errors appeared. All 772 prior measured rows and
  dayahead contents survived unchanged; settings remained 30 kWh / 35% / 100%.
- Synthetic visual and independent reviewer checks passed for desktop/phone,
  both themes and parent directions. The actual authenticated dashboard/form
  remains **unverified**: Chrome CDP timed out, and Windows browser control
  stopped because it could not determine the current URL confidently enough
  to enforce its policy. No browser protections were changed or bypassed.
- The read-only evening and morning checks completed on Nov 10 and 11. Both
  authentic issues were observed in the journal. The morning fill window ends
  at 22:00 local. The 0.43.1 restart verified nonempty journal persistence.

## Tools development sequence

- 0.49.0 adds Find best time to What if. Operational acceptance compares its
  suggestion with a simulation at the same start on one real projection.
- 0.40.0 adds issued history and a separate tools card. Native read-only
  actions return display fields from the in-memory journal, scoped by device
  and read permissions. Synthetic UI checks cover desktop/phone layouts.
  Real authenticated dashboard rendering remains open. Nonempty journal
  persistence was verified with the 0.43.1 restart.
- 0.43.0 prepares alternate schedule learning, with an empty day selection by
  default for every installation, including the reference house. Operational
  comparison waits for a documented close of the morning experiment.
- The day-group learner, separate sister records and acceptance gate passed
  synthetic verification. The release-reviewed morning decision remains
  explicitly unset. Selecting days does not activate production or begin live
  comparison while that prerequisite is open. No days are applied to the house.
- The baseline, morning and interval gates require their existing prospective
  evidence before promotion.

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
- **0.38.0 evening interval:** 80% empirical range for the frozen 20:00 forecast,
  needing 30 calibration nights and 14 later complete shadow observations
  with at least 80% coverage. The code freezes calibration and tests only prior
  held-out issues; public bounds stay unknown until the gate passes and expire
  at the saved night boundary. No calibration or prospective interval evidence
  is available yet. This remains an open live-data gate.
- **0.39.0 second-installation tooling:** the unified private evaluation report
  and synthetic export/meter/time-zone/capacity variants are implemented.
  Real HA tests cover collection conservation across clock changes. No
  independent house's download is available; provider timing/completeness,
  independent provenance and the night gate still need actual evidence.
  Generalization and 1.0 readiness remain unproven.
- The Nov 10 and 11 evening/morning checks completed. The owner authorized a
  single read-only follow-up at 22:25 local on Nov 11 for the morning fill outcome.
- Startup verification waits for Core to start: on the real house an unrelated
  slow startup exceeded the read-back retry window. The 0.39.0 post-start
  read-back succeeded and returned collection health to ok.
- **0.39.1 precision fix:** live comparison exposed early rounding of tomorrow's
  raw total, shifting its corrected sensor by 0.01 kWh from its hourly curve.
  Preserve precision until final correction. This does not change the battery
  projection. The loaded patch's live sensor is 22.96 kWh and its exact curve
  sums to 22.955206 kWh, agreeing after final rounding.
- Diagnostics and journal downloads stay private; test fixtures remain synthetic.

## Evaluation evidence and open live-data gates

- Strict 08:00/20:00 replay of the same private reference download: 29 complete
  nights, MAE 2.531 points, reserve calls 27/29 (93.1%). Only two mornings are
  gradable after shared external-charge and completeness screening; fill-time
  MAE is unknown because these are hourly extremes. The old multi-evening
  baseline remains 2.47 points and 78/85; its 26 morning observations were a
  broader group that did not all screen external charging. Do not conflate them.
- Adding independently calculated daylight windows to the private development
  copy still yields no eligible historical candidate pairs. No improvement is
  claimed. The legacy period-end archive cannot recover a missing sunset split;
  actual complete new days and three usable training days per part are needed.
- The first actual 20:00 issue was independently evaluated in the Nov 11 private
  check and graded automatically after the 0.43.1 restart. The 14 new paired
  mornings/nights and 30+14-night interval calibration/validation remain open.
  The active projection is
  baseline-0.34.2.
- No independent-installation evidence exists. Synthetic tests do not close it.
- The product is English only. Actual authenticated dashboard rendering remains
  unverified; synthetic English phone/desktop, both themes and parent directions
  passed, including known/unknown interval details with no horizontal overflow.

## Release validation on 2026-11-10

- **Earlier 0.39.1 verification.** HACS installation and restart are
  verified; Core is RUNNING. The 12:29:05 UTC capture was verified by read-back
  at 12:36:09 UTC, after startup. Collection is ok, all five input channels
  are readable and there are no Solar Sanity warnings/errors. All 769 measured
  buckets and both 386-row archives survive, dayahead is unchanged, and battery
  settings remain 30 kWh / 35% / 100%. The corrected sensor agrees with its
  hourly curve and the card resource points to `?v=0.39.1`.
- 0.39.0 was also verified after its HACS update and restart. Its
  first capture at 12:11:51 UTC was verified at 12:18:38 UTC, after the slow
  startup completed. Collection is ok, the outlook refreshes, all five channels
  are readable and there are no Solar Sanity warnings/errors. The 769 measured
  buckets, both 386-row forecast series and 30/35/100 battery settings survive;
  dayahead contents are identical. The card resource uses `?v=0.39.0`.
- 0.39.1 passes every CI gate, including 630 Python tests at each of HA
  2025.11.0 and 2026.2.3, plus 179 card tests. The local full check passes
  362 Python tests with nine HA-dependent modules skipped; the upstream HA
  harness needs Linux's fcntl. CI includes both startup-state regressions.
- Synthetic English phone/desktop checks pass with both themes and parent
  directions, including expanded interval details without overflow. Actual
  authenticated dashboard rendering remains unverified. 0.36.0 was never
  installed on the reference house; the English-only correction was installed
  before the later features.
- Scheduled checks verify the actual loaded stable version and journal;
  they do not require the older 0.34.2 version string. No due checkpoint has
  occurred yet, so persistence of a nonempty issued journal and completed
  observation grading remain open.

## Observe the baseline through a real night and morning

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

- **Auto-merge.** GitHub auto-merge and required checks on `main` are off. The maintainer was
  given the two `gh api` commands to turn them on and has not yet. Until then, merge by hand
  when CI is green.
