# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [0.49.0] - 2026-11-11

### Added

- Find best time in What if recommends when to run one extra load before a
  deadline while the battery stays above reserve in the current outlook.
  Compare the recommendation with running now and see the estimated battery
  and grid impact. The same calculation is available as a read-only action.

## [0.48.0] - 2026-11-11

### Added

- Reserve risk and Solar surplus ready binary sensors, with per-installation
  warning, power and hold settings. Setup explains their current states.
- Notification blueprints for reserve risk and measured solar surplus, with an
  optional, explicitly labelled forecast-based surplus notification.

## [0.47.0] - 2026-11-11

### Added

- Choose a preferred solar forecast provider for each installation in Configure.
  Battery, today and tomorrow can use different providers when their forecast
  coverage differs. Setup shows the provider actually used and why it switched.
- Issued predictions retain their actual forecast source and capture time.
  Accuracy and interval calibration use observations from the same source.

## [0.46.0] - 2026-11-11

### Added

- Set a battery-side charge power limit or inverter standby draw in Configure.
  Leave either field blank to learn it from measurements. Setup shows each
  applied value and its source.
- Preview battery settings in What if. The comparison uses one published
  projection to show the effect on charge, reserve, fill and solar takeover
  before saving.

## [0.45.0] - 2026-11-11

### Added

- The tools card's Setup tab checks measurements, solar forecast capture and
  archive verification, battery outlook settings, and prediction history for
  one installation. Configuration problems point to the relevant Home
  Assistant settings.
- Solar-only installations get the tools card in generated dashboards and open
  directly to Setup. The read-only `solar_sanity.get_setup_check` action returns
  the same installation-scoped checks.

## [0.44.0] - 2026-11-11

### Added

- The evening History row compares expected solar coverage with the first
  measured hour when solar energy covered house load and estimated inverter
  use. When that hour starts later, History shows the lowest recorded charge
  between the expected time and the start of the observed hour, if available.
- Eligible older issues gain the same comparison from retained measurements.
  Coverage observations survive restarts and leave the original forecasts,
  night grades, reserve decisions and accuracy metrics intact.

## [0.43.1] - 2026-11-11

### Fixed

- Morning and evening predictions issued a few seconds after the hour can now
  be graded. The recorder query includes the first hour, and recorded states
  constrain partial hours to the original observation window. Pending issues
  are retried without rewriting their forecasts or reopening settled results.

## [0.43.0] - 2026-11-10

### Added

- Alternate schedule days in integration settings, independently per installation.
  The default is empty for everyone. Select one to six days to define a second
  routine; all seven are rejected with guidance to leave the list empty instead.
- A gated consumption experiment using the previous 28 local calendar dates,
  requiring three distinct dates per used hour. Repeated clock hours count once
  per date, missing hours remain missing and the measured anchor is preserved.
- Separate candidate snapshots and a persistent experiment epoch. Changing or
  clearing days resets its comparisons while retaining production metrics.
  Candidate grading/calibration remains separate from the morning experiment.
- Prospective acceptance counts and reasons in private diagnostics and the
  unified evaluation report. Promotion needs 14 new complete night/morning
  pairs, evidence from both groups and every error/accuracy gate to pass.

### Notes

- The morning experiment remains open. Live day-group comparison is blocked
  until a reviewed, documented morning decision; choosing days cannot bypass
  it. The production model is unchanged. No reference-house days are preset,
  no candidate activates automatically, and promotion needs a later release.
- Old journal envelopes remain readable. Unknown experiment metadata is preserved
  without overwrite. The measurement-window Store and existing services/entities
  are unchanged; no production dependency or CI change is included.

## [0.42.0] - 2026-11-10

### Added

- Changes in the tools card: previous/current battery charge, corrected solar,
  expected consumption, correction factors and learned charge/standby estimates
  when they change. Solar and load share the same future interval, so elapsed
  hours and a moving horizon do not create false input changes.
- Bounded `outlook_changes` on the existing minimum-charge sensor, excluded from
  Recorder. Two valid snapshots stay in memory and reset after restart, invalid
  data or a changed setup/source. No causal shares of result changes are claimed.

### Notes

- Production projection and all prospective experiment gates remain unchanged.

## [0.41.0] - 2026-11-10

### Added

- What if in the tools card: compare one additional load with a baseline from
  the same current projection. It shows both curves, raw full/reserve times,
  night minimum, extra energy and uncovered energy. Calculate starts automatic
  updates; hidden tabs pause and obsolete responses are ignored.
- The read-only `simulate_load` response action with installation permissions,
  finite power and 1–1440 minute validation. Scheduled starts use Home Assistant
  time, require an explicit DST occurrence when needed, and apply only the
  future part of a started run. A complete run must fit inside the horizon.
- `projection_id` on the existing minimum-charge sensor for coordinated tools
  updates, excluded from Recorder attributes.

### Notes

- Scenario inputs stay in the card. No appliance control, forecast journal
  writes, new entities or changes to the active projection model are included.

## [0.40.0] - 2026-11-10

### Added

- Solar Sanity: tools, a separate English card showing issued predictions
  against observed battery results. Choose 7, 28 or 90 days, with seven days
  shown at a time. Pending, excluded and missed checkpoints remain explicit;
  hourly observations never claim precise fill times.
- The read-only `get_forecast_history` response action, scoped to one device
  with read-permission checks. It returns display fields from the existing
  journal without replay inputs or source entity IDs. Generated dashboards
  include the tools card for each battery installation.

### Notes

- The active projection, morning experiment and interval gates are unchanged.
  No due checkpoint has been fabricated for testing. Load simulation and
  change explanations follow in separate feature releases.

## [0.39.1] - 2026-11-10

### Fixed

- Round tomorrow's solar total after applying the roof correction. Early
  rounding could move the displayed total by 0.01 kWh from the sum of its
  corrected hourly curve. Battery projection arithmetic is unchanged.

## [0.39.0] - 2026-11-10

### Fixed

- Defer startup archive read-back until Home Assistant has started. An unrelated
  slow startup on the reference house outlasted all bounded retries and left
  a healthy queued archive marked unverified until the next capture. Pending
  startup verification is cancelled when an entry unloads or a newer batch wins.

### Added

- A unified private evaluation command, `scripts/evaluate_installation.py`,
  reports one installation at a time with source fingerprint, time zone,
  battery settings, provider timing checks and algorithm/context separation.
  It reuses the shipping projection and observation grader, including external
  charging exclusions. Hourly observations never produce invented minute
  precision. Historical development evidence stays separate from actual
  issued-journal promotion and interval-validation evidence.
- Synthetic coverage spans export/no-export, power/cumulative meters,
  capacities and time zones. Real Home Assistant collection tests verify energy
  conservation across spring and autumn clock changes.

### Notes

- No independent installation download is available. Synthetic tests and the
  reference house cannot establish generalization or 1.0 readiness.
- The strict 08:00/20:00 reference replay grades 29 complete nights with MAE
  2.53 points and reserve decisions correct on 27/29. Only two mornings remain
  gradable after external-charge and completeness exclusions. The older
  multi-evening replay remains unchanged at 2.47 points and 78/85 reserve calls;
  its 26 morning observations had not all excluded external charging. These
  are different denominators, not a changed battery projection.
- The old reference archive still lacks eligible three-part daylight training
  after horizon checks. The candidate remains inactive, with prospective
  morning/night, interval-coverage and independent-installation gates open.

## [0.38.0] - 2026-11-10

### Fixed

- Supply the explicit production-history fallback argument when running the
  silent daylight candidate. Its missing argument prevented shadow calculation
  while the isolated baseline continued normally. No prospective checkpoint
  had yet been recorded on the reference installation.

### Added

- Two experimental sensors expose the lower and upper bounds of the issued
  20:00 minimum-charge prediction. The range targets 80% coverage, using the
  10th and 90th signed-error quantiles of up to 60 earlier valid nights from
  the same model and configuration. This night's outcome cannot calibrate it.
- Bounds remain unknown until 30 calibration nights and 14 later complete
  silent-validation nights achieve at least 80% observed coverage. Calibration
  and validation counts, measured coverage, revision, issue time and night
  boundary accompany the sensors. Limits expire when that frozen night ends.
- The cards' quality section displays the dated experimental evening interval
  separately, with explicit reasons while evidence is insufficient. Waiting
  for calibration does not change collection health or raise a Repairs issue.

### Notes

- New evidence starts with actual recorded forecasts. An old snapshot never
  gains a reconstructed interval. Recorder reads completed after issue time
  cannot improve that issue's calibration. The live projection is unchanged.
- This is an empirical experimental interval, not a battery guarantee.

## [0.37.0] - 2026-11-10

### Added

- A silent morning experiment learns three daylight parts from previous local
  days only. Each part needs three usable days with battery charging room;
  existing day-similarity weights and smoothing are reused. Distribution is
  normalized to preserve the corrected daily energy. Missing daylight coverage
  retains the baseline shape. Independent sunrise/sunset bounds may prove a
  sparse provider's horizon; missing hours inside daylight never become zero.
- The issued journal freezes a separate candidate alongside the active 08:00
  and 20:00 outlook, grades both complete observation windows and reports the
  paired promotion gates in diagnostics. The candidate remains inactive until
  at least 14 new complete mornings and nights support every conservative gate.
- `--shape-shadow` enables development replay, explicitly separate from new
  issued evidence. Diagnostics retain daylight bounds needed to reproduce it.
- The corrected tomorrow sensor includes exact `corrected_hours`; the forecast
  card consumes them without applying the displayed daily factor twice.

### Changed

- Corrected forecast calculations keep the projection's unrounded learned
  factor. The descriptive `factor` attribute remains rounded; `applied_factor`
  states the exact multiplier. This removes small rounding disagreements
  between forecast figures and the projection; battery projection is unchanged.

### Notes

- The baseline-0.34.2 projection is still active. No battery efficiency,
  reserve threshold or settled-time rule changes. Candidate failures cannot
  disable baseline calculation. Passing development data cannot promote it.
- The product remains English only.

## [0.36.1] - 2026-11-10

### Removed

- Arabic translations and the card language selector. The product is English
  only, including cards, editors, setup, entity labels and reasons. The
  maintainer clarified this requirement after 0.36.0 was published; that
  release had not been installed on the reference Home Assistant.
- Previously saved card `language` fields are ignored, and editing a card
  drops that obsolete field. User/provider names remain as entered.

### Notes

- The accuracy and data-quality section and actionable configuration Repairs
  from 0.36.0 remain available in English. No projection calculation changes.

## [0.36.0] - 2026-11-10

### Added

- Arabic and English cards and editors follow the Home Assistant user's
  language. Each card also accepts `language: auto`, `ar` or `en`. Arabic
  setup, options, sensor labels and configuration Repairs are included.
- An expandable accuracy and data-quality section reads the issued journal's
  28-day metrics and seven-day summary, sample and exclusion counts, and each
  provider's capture and archive-verification timestamps. Missing samples stay
  unknown and are not presented as integration faults.
- Stable `reason_code` attributes accompany existing outlook reason sentences.
  Cards translate known reasons while retaining the legacy sentence fallback.
- Non-cumulative energy channels raise an actionable Repairs issue until the
  source classification is corrected or the mapping is changed. Startup removes
  only retired energy-balance issue identifiers and preserves current issues.

### Notes

- Existing entity IDs, services and the baseline-0.34.2 projection are unchanged.
  The journal's first complete nights and mornings are still being collected.

## [0.35.0] - 2026-11-10

### Added

- **A private record of issued predictions.** The first outlook published in
  the five minutes after 08:00 and 20:00 in Home Assistant's time zone is kept
  unchanged, with its inputs and settings. A missed checkpoint is recorded as
  missed, never reconstructed from a later forecast. The separate journal keeps
  at most 180 records over 90 days and survives restarts.
- **Measured prediction performance.** Four sensors report the last 28 days'
  night-minimum mean absolute error, reserve-call accuracy, morning fill-call
  accuracy and fill-time mean absolute error, with sample counts, exclusions
  and a seven-day summary. A missing observation is unknown, not zero. Hourly
  extremes can grade a fill call but do not claim minute precision.
- **Collection health and archive evidence.** A collection-status sensor and
  two timestamps per provider distinguish a valid forecast capture from a
  read-back of matching archive content. Submission to the recorder alone is
  not presented as verified persistence. Diagnostics include the private
  journal and the stages of each provider's most recent capture.

### Notes

- This release keeps the 0.34.2 battery projection unchanged. Performance starts
  with predictions actually recorded after installation; no historical score
  is invented at setup. The morning experiment, calibrated evening interval,
  Arabic cards and independent-installation validation follow separately.
- The existing measurement-window store and public entities remain compatible.
  An unreadable journal is left untouched and cannot take down the outlook.

## [0.34.2] - 2026-11-10

### Fixed

- **The night ends when solar covers the house and the inverter.** A morning
  covering only the house could end the night an hour early, overstate its
  lowest charge and hide a reserve crossing while the inverter still needed
  the battery.
- **Old charge readings need evidence that the battery stayed still.** Missing
  history, a missing direction or an unreadable battery channel no longer
  counts as zero movement. Without complete evidence, the existing six-hour
  limit applies; observed movement still rejects an old reading after two hours.
- **Corrected forecasts expire with the battery outlook's forecast.** After
  three hours without a successful capture, all six corrected sensors say why
  they are unknown. An expired forecast cannot supply the live spare-power
  estimate. The archive and the learned correction are kept for recovery.
- **Power belongs to the hour when it flowed.** A change arriving just after
  an hour boundary used to credit that slice to the preceding hour. Delayed
  sampling now closes each elapsed hour separately, including recording gaps.
- **Energy readings without a cumulative classification are excluded.** A
  reading with no `state_class`, or with `measurement`, was added again every
  five minutes. Use `total` or `total_increasing`; the log and diagnostics say
  why an unsupported channel is excluded, and it recovers when corrected.
- **The same house cannot slip through setup behind a shared solar sensor.**
  A shared consumption channel is checked across all matches and entries,
  regardless of their order, during setup and reconfiguration.
- **The forecast card follows the house's time zone.** Tomorrow, day boundaries,
  chart labels and the accessible table agree with Home Assistant, including
  daylight-saving changes and a browser in another country. A zone change
  discards the old request and reads the right day again.

### Internal

- The backtest excludes incomplete nights and mornings rather than grading
  surviving readings as a whole interval. Raw history exports must have both
  endpoints and no internal gap longer than the existing two-hour limit.
- On the reference installation's 29 nights, the night-window correction
  changes the mean absolute lowest error from 2.44 to 2.47 points; reserve
  decisions remain correct on 78 of 85 projections. The slightly longer night
  includes the inverter's draw. Three incomplete mornings are excluded, leaving
  26: two predicted fill days did not fill and six fill days were missed.

## [0.34.1] - 2026-11-10

### Fixed

- **The battery card no longer runs under the card below it** on a sections
  dashboard. It asked for a fixed height of five rows. 0.34.0 added the
  power-flow line and a second row of figures, and the chart's bottom then
  slid under the next card. Both Solar Sanity cards now take the height their
  content needs.

## [0.34.0] - 2026-11-09

Three new figures to act on, and the battery card shows the power flowing now.

### Added

- **"Usable battery energy"**: what the battery can still give the house
  before its reserve, after the inverter's loss. At 49% of 30 kWh with a 35%
  reserve that is 3.9 kWh. It is a measurement rather than a forecast, so it
  is recorded as a statistic for history graphs.
- **"Expected use tonight"**: what the house and the inverter will use from
  dusk until the sun takes over, from the hours Solar Sanity has learned. On
  the reference installation that was 12.7 kWh from 17:00 to 08:00. Read it
  beside "Usable battery energy" to see whether the night is covered.
- **"Spare solar power now"**: solar power going to waste this minute, because
  the battery is full or already charging as fast as it can. An air
  conditioner or water heater can take that much at no cost to the battery or
  the grid.
  - It is worked every 30 seconds from the live readings.
  - With no export, a full battery holds production down. Measured production
    then understates the sun, so the corrected forecast says what the sun
    could give.
- **The battery card** shows the three as a second row of figures. Above them
  is a line of the power flowing now: solar, house, battery and grid.
- **An optional "Battery power" sensor under Configure**, for that line. Use
  it when your battery channels are energy totals, as on the reference
  installation, since a total cannot show what the battery is doing this
  second. Untick "positive while charging" if your inverter signs it the
  other way.

### Notes

- **No countdown sensors.** "Battery full at" and "Battery reaches reserve at"
  already count down by themselves on the device page. An Entities card with
  `format: datetime` shows their exact date and time.
- **No copies** of your inverter's own power sensors as new entities. The card
  reads them directly, so nothing is stored twice.

## [0.33.0] - 2026-11-09

Your forecast provider's figures, as your roof would make them, and only those.

### Added

- **Five corrected sensors beside "Solar energy tomorrow (corrected)":**
  - "Solar power now (corrected)";
  - "Solar energy this hour (corrected)";
  - "Solar energy next hour (corrected)";
  - "Solar energy remaining today (corrected)";
  - "Solar energy today (corrected)".

  These are the figures Forecast.Solar publishes, multiplied by what your roof
  has delivered against that provider. With a battery, that is the factor
  learned for a day forecast like today. A provider forecasts an ideal roof,
  and dirty panels or poor placement mean yours is not one. The sensors exist
  when a forecast provider is configured. Each says which provider and which
  factor in its attributes.

### Changed

- **The forecast card shows only the corrected forecast** once the correction
  is learned. The total and the curve are both corrected, and the figure for
  an ideal roof is no longer printed. Until then it shows the provider's
  forecast and says it is not yet corrected.
- "Expected tomorrow (corrected)" is now named "Solar energy tomorrow
  (corrected)", to sit with the others. Its entity id does not change.

### Removed

- **The "Forecast tomorrow" sensor** (`expected_tomorrow`). It was the
  provider's uncorrected figure, which the provider's own sensor already shows.
  It is removed from the registry on upgrade. An automation that used it should
  use the provider's "Estimated energy production - tomorrow" sensor, or the
  corrected one.

### Notes

- There is no corrected peak time. One factor for the whole day cannot move
  the day's peak, so the provider's own peak-time sensors already have the
  answer.
- "Power now" is drawn between the hourly averages, the finest shape the
  hourly forecast holds. At half past the hour it is exactly that hour's
  average.

## [0.32.0] - 2026-11-09

The forecast is corrected by how far it misses on days like the one ahead,
learned from your own roof, instead of by one average for every day.

### Changed

- **A sunny day and a cloudy one are no longer corrected by the same amount.**
  Solar Sanity already learned how much of the forecast your roof really
  delivers, from the last fortnight's hours with room in the battery. That was
  one figure for every day. A forecast made for an ideal roof misses by very
  different amounts on different days. Dirty panels, shading, or a provider set
  up for the wrong roof show most in full sun. On the reference installation
  the roof delivered about a third of a sunny day's forecast, and roughly all
  of a cloudy one's. The single figure was 26% too optimistic on sunny days and
  11% too pessimistic on cloudy ones.

  Each day is now corrected by a factor learned mostly from the days whose
  forecast looked like it. That cut the error to 8% and 6%. Hour by hour, the
  error in expected production fell from 42% to 36%. Each day was graded on
  what had been learned before it.
- **"Expected tomorrow (corrected)" and the forecast card's "From your roof"
  use tomorrow's factor.** For the reference installation's 61 kWh forecast for
  10 November that is about 0.43, so about 26 kWh rather than 33. The card now
  says the figure is for days forecast like this one.
- **The battery outlook projects today and tomorrow with their own factors.**
  Replayed over the reference installation's last month:
  - Tonight's lowest charge: mean error 2.5 points instead of 2.7, and worst
    9.0 instead of 12.3.
  - The reserve was called right on 75 of 82 nights instead of 74.
  - "Full at", on the mornings it was called right: a median of 1 minute late
    instead of 81 minutes early.

### Added

- `forecast_scale_today` and `forecast_scale_tomorrow` attributes on "Battery
  full at", "Lowest battery before sunrise" and "Spare solar". `forecast_scale`
  stays the overall figure.
- The diagnostics download lists each day the factor was learned from, so
  either factor can be recomputed by hand.

### Notes

- A roof whose forecast is off by the same amount every day sees no change.
- Today is judged by the forecast its passed hours had a day ahead, not by the
  provider's later rewrite of them. Forecast.Solar's last capture of a day added
  up to a clear sky, 72-74 kWh, whatever the weather had been.

## [0.31.2] - 2026-11-09

"Battery full at" and "Battery reaches reserve at" no longer jump between
today and tomorrow on a close call.

### Fixed

- **A time already shown stays while the battery is projected to miss it by a
  point or two.** Once 0.31.1 let the sensors update every few minutes, a close
  call became visible. On the reference installation the battery was projected
  to peak at 99.4%, just over the 99% that counts as full. "Battery full at"
  then swung between Monday 15:40 and Tuesday 12:20 four times in thirteen
  minutes, because a single whole-percent step in the charge reading was enough
  to tip it. The time shown now stays until the projected peak falls more than
  two points short, and the same applies to the reserve warning. An earlier time
  still shows at once, and a time that has passed without the battery filling
  moves on.

### Notes

- The projection itself is unchanged, and so are the other three read-outs and
  the card's chart. Only when the two times change is different.

## [0.31.1] - 2026-11-08

The battery outlook's sensors now follow the battery. They were being written
only when Home Assistant started and once every six hours.

### Fixed

- **The battery outlook's sensors move every few minutes again, as intended.**
  The outlook was recomputed every five minutes, but the timers that should
  then have written its sensors ran in a background thread, and Home Assistant
  refuses a state write from there. So "Battery full at", "Lowest battery
  before sunrise", "Safe average load tonight", "Spare solar" and the battery
  card changed only at a restart and once every six hours. After a restart on
  the reference installation the card said the charge level was not reporting,
  while the diagnostics held a projection computed minutes before. An earlier
  fix for the same symptom, in 0.1.5, never took effect, for the same reason.
- Hours were closed in that same background thread while live power readings
  were added to the same hour from Home Assistant's own loop. Both now happen on
  the loop, so the two can no longer interleave.

### Notes

- Your Home Assistant log may hold errors saying that `solar_sanity` "calls
  async_write_ha_state from a thread other than the event loop", repeated about
  twice a minute. They stop with this release.
- The outlook sensors now record a new state roughly every five minutes, which
  is what they were designed to do.

## [0.31.0] - 2026-11-08

Tomorrow now has one number. The forecast card, the "Forecast tomorrow" sensor
and the battery outlook all work from the provider's current forecast, and the
corrected forecast finally says what your roof is expected to make of it.

### Changed

- **The forecast card shows tomorrow as it is forecast now.** It used to show
  the first forecast recorded for that day, about two days old and never
  updated. On the reference installation that meant 48.2 kWh on the card beside
  34.6 kWh on the sensor for the same Monday. It now draws the same forecast as
  the sensor, and reads it again every half hour.
- **"Expected tomorrow (corrected)" uses what the battery outlook learned.**
  When you have a battery, it is tomorrow's forecast multiplied by the factor
  the outlook projects with: how much of that provider's forecast your roof has
  delivered on hours the battery had room. Without a battery it still uses the
  provider's stated accuracy. If your house already had a stated accuracy and a
  battery, the figure moves to the learned factor.
- The card's description in the picker now says what it shows.

### Added

- **"From your roof" on the forecast card**: tomorrow's forecast moved by that
  same factor, with how many days it rests on. It appears once a factor has been
  learned, usually within three days.
- `expected_tomorrow_corrected` gains the attributes `provider`,
  `forecast_statistic_id`, `factor`, `basis` (`learned` or `scored`) and `days`,
  and a `reason` while it is unknown.

### Fixed

- "Expected tomorrow (corrected)" stayed unknown on houses whose forecast gap
  grows on sunny days. On a house that cannot export, the battery fills and
  production stops, so whole days never agree on one percentage, and the
  provider's accuracy is never stated. The reference installation saw unknown
  for a month.
- With more than one forecast provider, the corrected figure could multiply one
  provider's forecast by another provider's accuracy. It now corrects only the
  provider "Forecast tomorrow" shows.

### Notes

- The learned factor counts only hours with room in the battery. On a house
  that cannot export it reads high on a day the battery fills: on the reference
  installation 0.52, against about 0.41 over whole days.
- The `<provider> accuracy` sensor is unchanged, and still says unknown, with
  its reason, when the gap is not one steady percentage.

## [0.30.0] - 2026-11-07

Solar Sanity is now a battery outlook. The energy-balance check, which told you
whether your sensors added up, is gone. The outlook replaces it: graded on a
month of real nights, it gets the night right to within about three points of
charge. The forecast archive and its scoring stay as they were.

### Changed

- **The setup wizard no longer asks about your house.** The three questions
  (do you have a battery, is your grid sensor one net sensor, does your
  consumption sensor cover the whole house) only fed the check. The step after
  the sensor mapping now shows only the forecast integrations and, if you
  picked a charge-level sensor, the battery's size. When there is neither, it
  is skipped.
- **Configure shows only the battery settings.** The guaranteed annual
  production and the list of findings to stop reporting are gone with the
  check.
- **The old status card shows the battery card.** A dashboard holding
  `custom:solar-sanity-card` keeps working and shows the battery outlook. The
  card picker no longer offers it, and its `entity:` setting is ignored. The
  Solar Sanity dashboard lays out the battery card and the forecast card.
- **Measured hours are saved as each hour closes,** rather than every six
  hours. A restart now loses at most the hour in progress.

### Removed

- **Entities:** Status (`sensor.*_status`), Data problem
  (`binary_sensor.*_data_problem`), Data completeness, Corrections in effect, and
  Live residual. They are removed from the entity registry when Solar Sanity
  starts. Their recorded history stays in the recorder and shows as no longer
  provided.
- **Services:** `solar_sanity.validate_now` and `solar_sanity.export_report`.
- **Events:** `solar_sanity_finding_raised` and `solar_sanity_finding_cleared`.
- **Finding codes,** which appeared in the status sensor's `finding_code`
  attribute and in those events: `stuck_channel`, `production_stalled`, `unit_scale_1000`, `cumulative_in_periodic_field`, `signed_net_in_dedicated_slot`, `signed_net_battery_slot`, `channels_swapped`, `duplicate_channel_pair`, `simultaneous_opposing_flow`, `channel_sign_inverted`, `channel_never_positive`, `double_counted_channel`, `missing_storage_channel`, `missing_export_channel`, `missing_generation_channel`, `partial_ct_coverage`, `load_boundary_mismatch`, `pv_measured_dc`, `battery_measured_dc`, `unmetered_standby`, `correction_now_harmful`, `unexplained_residual`.
- **Repairs:** every Solar Sanity issue is removed when Solar Sanity starts.
  Nothing will raise one again.
- **Options:** guaranteed annual production, findings to stop reporting, and
  accepted corrections.

### Notes

- **Nothing about your configuration is deleted.** Entries made before this
  release keep the three answers and the three removed options where they were.
  Nothing reads them now, and leaving them there means going back to 0.29.0
  loses nothing.
- **The old shared state file is deleted at start,** if one is left from a
  release older than 0.5.0. It only ever held the check's fitted model.
- **If you used the check's entities in an automation,** the automation will
  report a missing entity. The battery outlook's sensors are the replacement
  for most uses: *Battery reaches reserve at* is the one to alert on.

## [0.29.0] - 2026-11-07

### Added

- **A battery card.** *Solar Sanity: battery* puts the battery outlook on one
  card: the charge now, one sentence worth leading with, the safe load until
  morning, the lowest charge before sunrise and the spare sun, and a line of the
  projected charge over the next day and a half against your reserve and
  target, with the night shaded.

  The sentence follows what the projection says, not the time of day on your
  phone:
  - "Reaches the reserve at 04:10" when the battery will run out before the
    sun, which is the only warning it gives;
  - "Lasts until the sun, at 06:40" once the night has begun;
  - "Full at 14:35, in 2 h 10 min" by day;
  - "Will not fill today" when the forecast says so.

  Times are shown in Home Assistant's time zone. The card finds its sensors by
  what they are rather than what they are called, so renaming them does not
  lose them. With more than one installation it asks you to choose one in its
  settings rather than guessing. The Solar Sanity dashboard puts it first.

- **The lowest-battery sensor carries the starting charge, the reserve and the
  target** as attributes (`soc_pct`, `computed_at`, `reserve_pct`,
  `target_pct`), so the card draws everything from one refresh. The charge is
  published while the outlook is still learning your consumption.

### Fixed

- **On a phone, the forecast card pushed the page sideways.** Its hidden
  screen-reader table ignored its 1-pixel width and widened the page by the
  width of its hour column.
- **In a right-to-left dashboard, the forecast card's total read "kWh 20.2".**

## [0.28.2] - 2026-11-07

### Fixed

- **Forecast.Solar hours were an hour late on Home Assistant before 2026.11.**
  Forecast.Solar labels each hour's energy with the moment the hour *ends*.
  Home Assistant passed that label on unchanged until 2026.11, and Solar Sanity
  read it as the moment the hour starts. So on any release from 2025.11 to
  2026.10 the archived forecast, the battery outlook and the forecast-tomorrow
  sensor all saw the sun an hour late, and the short stretch before sunset was
  added to the hour before it. The forecast is now corrected the way Home
  Assistant 2026.11 corrects it, and only where it needs to be: Forecast.Solar
  on releases before 2026.11.
- **The forecast card's gridlines and axis labels were invisible.** They were
  drawn in a way no browser displays, so the card has shown a bare curve with
  no scale since it was written. Both axes and the gridlines now show.

### Notes

- Forecast history archived before this release stays as it was written, an
  hour late. The battery outlook learns its forecast scale from the last
  fortnight, so it is fully on corrected data two weeks after the update.
  Around the update the sunrise hour may be counted twice in the day-ahead
  series for up to two days, because each hour of it is written once and never
  revised.
- If you are already on Home Assistant 2026.11 or later, nothing changes for
  you except the card.
- Replayed with its archive corrected, the reference installation's month of
  real nights grades better than before: the lowest-battery figure is no
  longer biased in either direction, the reserve call is right on 72 of 80
  projections, and the time the battery reaches reserve is predicted with a
  median error of two minutes.

## [0.28.1] - 2026-11-07

### Fixed

- **The outlook now learns how far your forecast is off, instead of trusting
  it.** On the reference installation the Forecast.Solar forecast ran more than
  twice what the roof delivered, and *Battery full at* called a full battery on
  13 of 27 mornings when the battery did not fill. The outlook now compares the
  forecast as it stood a day ahead with what your panels produced over the last
  fortnight, and scales the forecast by that ratio. Only hours when the battery
  had room count: a full battery on an inverter that cannot export turns
  production down, and that says nothing about the sun. On the same month the
  outlook wrongly called a full battery on 2 days instead of 13, and missed 4
  days that did fill. The figure is shown as `forecast_scale` on the sensors
  that use it. Until three days of history exist, the forecast is used as it
  comes.
- **The inverter's own draw is now counted.** A hybrid inverter running from
  its battery uses power the house meter never sees: about 100 W on the
  reference installation, all night, every night. Left out, every night came
  out two to three points better than it was. It is now measured from your
  battery and consumption meters on the hours the battery carried the house
  alone in the dark, and added to the load. It is shown as `inverter_standby_w`.
  A battery metered on the house side learns zero, and nothing changes.

### Notes

- Replayed against a month of real nights on the reference installation, the
  lowest-battery figure is off by 2.8 points on average, from 3.8, and is no
  longer biased high: it had been 2.7 points too optimistic on average. The
  call on whether the battery reaches reserve before sunrise was right on 70 of
  80 projections.
- Diagnostics now include the learned standby and forecast scale.

### Internal

- `scripts/backtest_outlook.py` grades on each hour's lowest and highest charge
  reading rather than its average. A battery that touches its reserve and is
  then charged from the grid has an hourly average a point above the reserve,
  and graded on that, a night the projection called right read as one it got
  wrong. `--raw` replays as 0.28.0 did, for comparison.

## [0.28.0] - 2026-11-06

### Added

- **A battery outlook: five sensors that say where your battery is heading.**
  Map your battery's charge level and tell Solar Sanity how big the battery is,
  and it projects the charge an hour at a time from now until the forecast runs
  out:

  - **Battery full at** — when it will reach its charging target.
  - **Battery reaches reserve at** — when it will reach your reserve, if that
    happens before the sun takes over in the morning. Unknown means it lasts.
  - **Lowest battery before sunrise** — how low it will fall tonight.
  - **Safe average load tonight** — the steadiest load the house could draw
    until the sun takes over without the battery reaching reserve.
  - **Spare solar** — sunshine the battery will have no room for today (or
    tomorrow, at night), which is yours to use.

  Consumption is learned from your own history, hour by hour, so the outlook
  works from the first day if Home Assistant already has a couple of weeks of
  it. The night read-outs work without a forecast provider; the two daytime
  ones need one, because an inverter with nowhere to send a surplus records
  production cut back to what it could use, and that history understates the
  sun.

  Grid charging is never counted. The outlook answers the question that
  matters when the grid is out: would the battery alone get the house there?
  When the battery has been charged from the grid recently, the lowest-battery
  sensor says so in an attribute.

- **Battery capacity, reserve and charging target settings.** Asked in the
  setup wizard once a charge-level sensor is picked, and in Configure. Without
  a capacity the outlook sensors are not created.

### Notes

- This is the first step of a change of direction. The energy-balance check
  will be retired once the outlook has been measured against real
  installations; until then both run side by side and nothing about the check
  changes in this release.
- Diagnostics now carry a month of hourly charge level and both forecast
  archives, so the outlook can be replayed against what the battery actually
  did.

## [0.27.0] - 2026-09-03

### Changed

- **A battery charged from the grid overnight is now measured, not turned away.**
  0.26.0 learned to measure a battery's conversion loss on the hours after dark,
  and it did so by assuming there is no charging then — which is true of most
  installations and false of any house on a cheap overnight tariff. Those houses
  were refused outright and got no figure at all.

  They no longer have to be. A battery loses energy in both directions and the
  two are tied together by one efficiency, so charging and discharging are one
  number rather than two, and the charging hours carry information instead of
  spoiling it. Where a house does not charge after dark nothing changes at all:
  the arithmetic reduces to exactly what it did before, to the last digit.

### Fixed

- **A consumption sensor reading about a tenth low could be absorbed as battery
  loss.** On installations with a large battery and ordinary meter noise, the
  check meant to catch that can quietly stop working: where almost all of what
  your consumption sensor reports is explained by the battery, asking whether
  the unexplained energy follows consumption is asking whether it follows
  itself, and the answer flatters the fault. Measured on 600 simulated houses,
  eighteen were affected.

  There is now a separate figure that says whether that check is a real test on
  your house, computed from the shape of your data alone so that no fault can
  influence it, and the battery figure is not taken unless it clears. It appears
  in the diagnostics download as `dark_load_independence` whether it passes or
  not.

### Internal

- The dark-hours estimator fits both battery directions under the physical lock,
  finding the loss fraction that is its own slope against the column it defines
  by bisection rather than iteration; a plain fixed point two-cycles on some
  houses, which would make the answer depend on where the search stopped.

## [0.26.1] - 2026-09-03

### Fixed

- **When the battery figure cannot be established, the diagnostics download now
  says why.** 0.26.0 added a measurement taken on the hours after dark, and said
  that a house it could not answer would still be told what was seen. On a large
  share of houses it was not: the figures were assembled below a check that
  stops early on any installation with fewer than two hundred night hours, which
  is most of them for the first fortnight, and every installation whose battery
  is quiet overnight.

  Those are precisely the houses the new measurement exists to answer, so the
  explanation was missing wherever it was worth having. There are seven distinct
  reasons the figure can be declined and they are seven different situations —
  not enough nights yet, the nights disagree with each other, something else in
  the house moves with the battery, the number is not a loss fraction at all.
  All of them now appear in the download, whether the figure was taken or not.

  Found by replaying a real installation, which declined the figure four ways at
  once and reported none of them.

## [0.26.0] - 2026-09-02

### Added

- **Batteries down to 80% round-trip efficiency now get an answer.** The cut-off
  was 90%, which is an ordinary hybrid rather than an unusual one, and below it a
  perfectly healthy installation was told "still looking" indefinitely.

  What changed is where the figure is measured. A battery loses energy in both
  directions, and the two are locked together by one efficiency, so it is one
  number rather than two. Measuring it during the day never worked well, because
  a battery charges from the surplus and so its charging is inseparable from what
  the panels are doing. In the dark there is no charging and no generation, so the
  only thing left moving is the battery discharging — and that is now where the
  figure is taken.

  It is a better measurement rather than a looser one. Faults that used to be at
  risk of being mistaken for battery loss are simply not present in those hours,
  so the checks that guard against them are no longer a matter of degree: a charge
  sensor reading low, an unmeasured export path, or a battery reading a tenth low
  all look exactly like a healthy house overnight, and are refused. A house whose
  battery is charged from the grid on a cheap overnight tariff is recognised and
  handed back to the old method, because for that house the premise does not hold.

  Where no figure can be established, the diagnostics download now carries what
  was measured anyway, so "nothing could be established" says which of the seven
  reasons applied.

### Changed

- **Home Assistant 2025.11 or newer is now required.** The declared minimum was
  2025.1, and the integration could never have run there. Two things stop it: the
  options page is built on a class Home Assistant added in 2025.8, so below that
  the integration fails to load at all; and the forecast archive is written with
  metadata the recorder had no column for until 2025.11, so on anything older the
  first write of each provider's series raises inside the recorder and the archive
  is silently never created.

  That archive is the one record this integration cannot rebuild later, which is
  why the floor moves to where it always actually was rather than to where the
  first failure happens. HACS will no longer offer the integration below 2025.11,
  and CI now runs the whole suite against that version as well as the newest — the
  declared minimum had never been executed once.

### Fixed

- **The guaranteed annual production figure never reached the check.** It was
  saved where the settings page puts it and looked for somewhere else, so a figure
  you typed in did nothing, and said nothing about doing nothing. Anything already
  typed in starts working on upgrade; there is nothing to do.

- **A figure typed into settings by mistake can now be taken back.** Clearing a
  field on the settings page put the old value straight back. This affected the
  battery charge level too, which the setup wizard already handled correctly and
  the settings page did not.

### Removed

- **Two editor fields that did nothing.** The status card's editor offered a
  Title and a "Show the numbers behind the verdict" switch, and the card has never
  read either. There is no heading for a title to go in, because the headline is
  the verdict, and the status entity publishes no measured figures to show: what
  it carries is the verdict, its explanation and the notes beside it. The numbers
  are on the other sensors and the full workings are in the diagnostics download.

  A dashboard whose YAML already carries either key is unaffected. The card
  ignored them before and ignores them now.

### Internal

- The committed version is bumped in the release pull request again, and two
  tests hold it there: one fails the pull request when the manifest and the newest
  changelog entry disagree, one fails the release when the manifest and the tag
  disagree. From 0.22.0 to 0.25.1 the committed version sat four releases behind
  and was correct only inside the released zips.

## [0.25.1] - 2026-09-01

### Fixed

- **The battery charge level is now offered during setup, not only afterwards in
  the options.** It is the last field of the sensor-mapping step, right below the
  two battery channels — which is where you would look for it, and where 0.25.0
  failed to put it.

  Reconfigure offers it too, pre-filled with whatever you already chose, and
  clearing it now genuinely clears it.

## [0.25.0] - 2026-09-01

### Added

- **You can now map your battery's charge level, and Solar Sanity will tell you
  whether a change was your equipment or your sensor.** Optional, in the
  integration's options, and it takes no part in the energy arithmetic.

  When a battery meter's reported throughput suddenly steps, the energy figures
  alone cannot say why. A meter that under-reports is exactly the case where the
  energy it fails to mention is still there and simply turns up as an unexplained
  residual — so every reading is implicated and none of them settles it.

  Your battery's charge level does settle it, because it comes from the battery
  rather than from the meter. A charge sensor reading a fifth of the truth leaves
  it completely unmoved. If the daily swing is the same either side of the change,
  the battery was always doing that work and only its reporting changed — which
  means **your older figures were the wrong ones, not your new ones**. If the
  swing changed too, the equipment really did change.

  Found on a real installation whose battery meter began reporting five times more
  throughput on one day. Its charge level had been swinging 42% a day before and
  49% after: the battery had been doing the same work all along, and three weeks
  of energy history had been understating it by about 13 kWh a day.

  Without the sensor mapped, nothing changes — the note names both causes and
  asserts neither, exactly as in 0.24.2.

## [0.24.2] - 2026-09-01

### Fixed

- **The note about a changed installation no longer guesses why.** It used to say
  the change "is usually a settings change rather than a fault". That was a guess
  presented as a fact, and on the first real installation to see it, it was the
  wrong guess — the owner had changed nothing.

  It now names both possibilities and asserts neither: either the equipment
  changed how it runs, or its sensor changed what it reports. From inside a
  window of energy readings those look identical. What separates them is your
  battery's state-of-charge history, which the note now points you at.

  Worth the distinction: if the sensor started reporting differently, it is the
  **older** figures that were wrong, not the new ones.

## [0.24.1] - 2026-09-01

### Fixed

- **The note about a changed installation no longer quotes a raw entity id.**
  It said "your sensor.your_inverter_..._battery_charge_energy started moving
  roughly 6 times more energy per day". It now says "your battery charging",
  using the same words the setup screen used when you mapped that sensor.

  The name came from Home Assistant's `friendly_name` attribute, which not every
  sensor has — on an MQTT-bridged inverter it is often absent, and the fallback
  is the entity id. Diagnostics downloads do not carry friendly names at all, so
  the sentence could not be reproduced by replaying one either.

## [0.24.0] - 2026-09-01

### Added

- **Solar Sanity now notices when your installation stops being the same
  installation.** If a channel's daily throughput steps up or down sharply and
  stays there — the usual cause is a settings change, such as starting to charge
  the battery from the grid overnight — everything is measured from that day
  onwards instead of being averaged across it.

  While the new arrangement is younger than a fortnight you will get a note
  saying what changed, when, and how long until a full verdict, rather than a
  verdict. That is a real wait, and it is the honest answer: there is no way to
  have fourteen days of evidence about a system that is eleven days old.

  Found on a real installation, where it mattered twice. Averaged across the
  change, the loss model could not fit the inverter at all — the coefficient
  came out at 0.31, which no window accepts. Measured on the days after it, the
  same model returns 0.04, an ordinary 96%-efficient inverter. And the report
  had been saying "nearly all of it cancels out by the end of each day", which
  was true of the month as a whole and much less true of how that house had
  actually been running for the previous eleven days.

  Deliberately hard to trigger: every day on one side of the change has to be
  clear of every day on the other by a factor of two. Seasonal drift does not
  qualify, and neither does a busy week. All 3,000 synthetic installations in
  the test corpus are unaffected.

## [0.23.0] - 2026-08-31

### Added

- **Both cards can now be configured by clicking them.** Until now the only way
  to point the status card at a particular installation, or the forecast card at
  a particular provider, was to edit the dashboard YAML by hand.

  The status card's editor exists mostly for one sentence. The card finds its own
  entity when you have a single installation, and nothing anywhere told you
  *which one it found*. Now it says so, and says that leaving the field empty is
  the right answer rather than an unfinished one.

  The forecast card's editor lists the providers actually writing an archive,
  read from the recorder rather than from a list of integration names this
  project knows about — so a provider it has never heard of appears the day it
  writes its first forecast.

  Neither editor is required. Both cards still work with no configuration at all.

## [0.22.0] - 2026-08-31

### Added

- **Tomorrow's forecast, corrected by what your provider actually does.** A new
  sensor takes tomorrow's figure and moves it by the bias measured against your
  own roof. It stays empty until there is a correction worth applying — a bias
  from eleven days is real arithmetic and not yet an answer, and a number you
  plan around should not be built from one.

- **A `rescore_forecasts` service**, for when you have just mapped a channel or
  just passed the twenty-first comparable day and would rather not wait six
  hours. It answers with what it found, including why it will not put a number
  to a provider yet.

### Removed

- **Two fault codes that could never be reported**, named here because these
  identifiers appear in entity attributes and in the `solar_sanity_finding_raised`
  event, so an automation could be watching for them:

  - `submeter_included_in_parent`
  - `stale_channel`

  Neither was waiting on a detector. The first needed a channel role that was
  declared and never assigned to anything; the second needed a field written in
  one place and read in none, on data that is discarded whole when it is stale.
  Nothing has stopped being detected, because neither was ever detectable.

## [0.21.1] - 2026-08-31

### Added

- **You can tell Solar Sanity to stop reporting a finding.** The engine has
  honoured this setting for a long time — it skips the check entirely, which
  also saves the most expensive work it does — and there was no way to switch
  it on. Now there is, in the integration's options.

  Only findings your installation has actually been given are listed. Being
  offered the chance to dismiss a diagnosis you have never been given is how a
  settings page teaches you to ignore it. Anything already dismissed stays on
  the list so you can change your mind.

### Fixed

- **The dashboard card could have shipped stamped with the wrong version.**
  The release build embedded the version into the card before writing that
  version down, so the card carried whatever the previous release said. Every
  release so far has been correct by luck rather than by design — the version
  happened to be committed by hand first. Reordered.

- **A settings field no longer describes itself as unused.** The guaranteed
  annual production figure said "nothing reads it yet". It has been read since
  0.21.0, which is the release that compares it against what you produced.

## [0.21.0] - 2026-08-31

### Added

- **Solar Sanity tells you when your roof has stopped producing.** Every other
  check it makes asks whether your numbers agree with each other. This one
  asks whether the panels are doing anything — because a tripped string is
  reported as zero by a sensor working perfectly, and the arithmetic can be
  flawless while the roof sits idle.

  It learns your daylight from your own history rather than from where the sun
  is. A roof behind a hill is dark at nine whatever the almanac says. It needs
  a run of empty hours rather than one — a single flat hour is a cloud — and it
  needs the rest of your system still reporting, because if everything went
  quiet then your broker stopped, not your panels, and that is not a reason to
  climb a ladder.

- **A year's production against the figure you were quoted.** Type the annual
  yield your installer promised into the options and, once there is a genuine
  year of history, Solar Sanity will tell you what you actually got.

  A note, never a fault. Falling short is a conversation with whoever fitted
  it, and a short year has honest causes this cannot tell apart — the weather,
  shading that grew, a panel derating exactly as its warranty allows.

  Never estimated from a partial year. Scaling nine months up to twelve
  compares three summer months against an average one, which flatters an
  installation checked in autumn and unfairly damns one checked in spring.

### Fixed

- **The card now shows what Solar Sanity has been saying all along.** Its
  explanations — which hours could be checked, what was assumed about a
  sensor, whether your energy adds up over the day even though the hours do
  not — reached the sensor and stopped there. The card never read them.

  On an installation reading OK those sentences are the entire explanation, so
  "Everything reconciles" was the whole message on a house where that is only
  true over a day, or only in the hours with no sun.

### Removed

- **Two declarations that did nothing.** `API_VERSION` was defined and never
  read. `websocket_api` sat in the integration's required components while the
  only mentions of that name in the code are comments about something else.
  Both gone, and a build step now catches the second coming back by accident.

## [0.20.0] - 2026-08-31

### Added

- **You can see how accurate your solar forecast actually is.** A new sensor
  per configured provider, comparing what it said the day before against what
  your roof then produced — signed the way you would read it, so negative
  means the system produced less than promised.

  Solar Sanity has been recording day-ahead forecasts since it was installed,
  precisely because that history cannot be reconstructed later: forecast
  entities carry no state class, so Home Assistant purges them within about
  ten days. The scoring existed too. The two had never been introduced.

  It says nothing for the first three weeks. Twenty-one comparable days is
  the floor, and a shorter window is one spell of weather seen repeatedly
  rather than a provider's habit — so until then the sensor reads unknown and
  its `reason` says which of those it is waiting for. A figure it will not
  stand behind is not shown at all.

  The sensor exists as soon as a provider is configured, not once it has
  earned a figure. Whether a figure is earned changes with the weather, and
  an entity that came and went with it would quietly break anything pointing
  at it.

  Deliberately not recorded as a statistic: it is a judgement over a rolling
  window rather than a measurement, and a history graph of it would be a
  graph of an opinion changing its mind.

## [0.19.0] - 2026-08-31

### Changed

- **What Solar Sanity measured itself now survives a restart.** The window it
  works from lived only in memory, so every restart threw it away and rebuilt
  it from Home Assistant's stored statistics. Those are not wrong, but they
  cannot tell you one thing your own measurements can: whether the hour was
  watched all the way through.

  An hourly average is computed from twelve five-minute pieces. If a sensor
  went quiet and only eight of them exist, the average of those eight is
  presented exactly like a complete hour — and there is no way to tell the
  difference afterwards. That is ordinary on an inverter bridged over MQTT,
  which starts publishing after Home Assistant is already running.

  Solar Sanity watches its channels continuously and knows which hours it saw
  end to end. Those hours are judged on a tenth rather than a sixth, and can
  support a confident answer. Until now every restart demoted them, and one
  reference installation had 55 of its own hours against 3,580 rebuilt.

  About 120 kB in the integration's own storage file at a full window.

### Fixed

- **A long-standing explanation in this project was wrong, and is corrected
  in nine places.** Solar Sanity's own notes said Home Assistant's hourly
  average over-weights the busy part of an hour. Checked against Home
  Assistant's source rather than argued about: it does not. Every reading is
  weighted by how long it actually stood, and for a complete hour the result
  matches what this integration computes exactly.

  The caution around those hours was right; the reason given for it was not.
  Nothing about the verdicts changes — but an explanation a reader could
  check and find false is worse than none, and this one was in the file that
  defines the distinction.

## [0.18.0] - 2026-08-31

### Added

- **A verdict for a house whose totals add up and whose hours do not.** Solar
  Sanity was telling one installation *"the numbers move around but not
  consistently enough to name"* — every six hours, indefinitely — about a month
  of readings that balance to within 1.4%. That was not a hedge. It was wrong,
  and it was never going to stop, because nothing about the house was going to
  change.

  Such a house is now told what is true: its energy is all accounted for, and
  what cannot be relied on is which hour each amount landed in. It gets a real
  verdict, with the figures behind it, instead of a permanent shrug.

  The reasoning is arithmetic rather than statistical, which is why it can be
  trusted. A sensor reading the wrong amount is wrong in the *same direction*
  every hour, so its error piles up and a day is out by the sum of its hours —
  measured across every fault this project can produce, none of it cancels. A
  disagreement about *when* energy moved cancels almost entirely, because the
  energy is genuinely there and genuinely counted.

  Three separate things have to hold before it is offered, and the third took
  hunting for: a battery whose charge **and** discharge are both mis-scaled
  cancels beautifully, since the same energy comes back out as went in. That
  one is caught by noticing the leftover *is* the battery — its residual tracks
  the battery exactly, where a genuine timing problem does not.

### Fixed

- **A surplus that comes back is no longer called export.** On a house with no
  export meter, the note reporting how much goes missing while you have a
  surplus counted only the surplus hours. It ignored every other hour of the
  day — so an installation whose afternoon runs short and whose night runs long
  by the same amount was told a specific number of kilowatt-hours a day was
  going to the grid, when nothing was leaving at all and its month balanced to
  within 1.4%.

  Energy that crossed the boundary does not come back. A genuine unmapped export
  path leaves the whole period short by what the surplus hours lost — measured
  at 0.96 to 1.02 of it, and that held under five per cent meter noise, a
  DC-metered inverter and an unmetered standby draw. A shortfall that is repaid
  after dark is describing something else.

  The measurement is still reported, because it is real and it is the largest
  thing about such an installation. What changed is the sentence after it: those
  houses are now told the two sides very largely cancel, and that this looks
  like their sensors disagreeing about *when* energy moved rather than about how
  much of it there was.


- **Solar Sanity no longer requires a component it never uses.** The manifest
  declared Home Assistant's `frontend` as a hard dependency, which means Home
  Assistant refuses to start this integration if it cannot start that one
  first. Nothing here ever called it: the card's static path goes through
  `http`, and the Lovelace resource registration already checks whether
  Lovelace is loaded and steps aside when it is not.

  On an ordinary installation the frontend is always there, so this cost
  nothing. On one without it — a headless or trimmed-down setup — Solar
  Sanity would not start at all, for a line that was never true.

- **A stored file this version cannot read no longer stops the integration
  starting.** Solar Sanity keeps its fitted loss model in a small file so a
  restart does not reset a month of learning. Home Assistant refuses to read a
  file written by a different version of that format unless an integration says
  how to convert it — and where that refusal was caught for the older shared
  file, it was not caught for the one every installation has. The exception
  travelled all the way out and the integration did not start.

  No released version writes a different version of that file, so this could not
  happen yet. It would have happened to the first person to *downgrade* after a
  future release changed it — somebody rolling back because something else had
  already gone wrong, who would have found Solar Sanity dead on arrival with
  nothing in the log to explain it.

  An unreadable file now costs the loss model and nothing else, with a warning
  saying so. The model is refitted from the same data within a day, which is
  why it is the safe thing to lose.

## [0.17.0] - 2026-08-30

### Added

- **When the model absorbs a loss, it now says so.** The three terms the loss
  model fits — a generation sensor reading before the inverter, a battery
  metered on its DC side, a continuous unmetered draw — were being subtracted
  from your numbers before anything was checked. Only the last of them was ever
  mentioned. So a generation sensor reading a tenth above the rest of the system
  was quietly accounted for, the verdict came back "no problem found", and the
  assumption behind that answer was shown to nobody.

  That was never the cautious option. It is the one where a wrong assumption
  never surfaces. Each term now appears as a note saying what was taken and
  what it was taken to mean.

  The generation note gives the figure both ways round, because a sensor a
  twentieth of whose reading never arrives is a sensor reading a nineteenth
  high, and **nothing in the data can tell those apart** — dividing by an
  efficiency and multiplying by its reciprocal produce the same series. So the
  note says what has been assumed and what to check if the assumption is wrong.

### Changed

- **One verdict window, twice as long, counting every day that is not clean.**
  Two things decided whether you got an answer: whether six of the last seven
  days were clean, and whether five of them were outright actionable. A day that
  was merely unsettled counted toward neither. A house could therefore sit for a
  fortnight visibly failing to add up and satisfy no rule at all — and the
  engine would say "the numbers move around but not consistently enough to name"
  without ever having generated a single explanation to reject. It would go on
  saying it indefinitely.

  The window is now fourteen days and the second test counts every day that is
  not clean. Measured over 108 healthy installations across three topologies,
  twelve seeds and three noise levels: nothing is blamed for a fault it does not
  have.

  Two houses that were being told the wrong thing are now told the right one:

  - **A house with no export meter is told so.** Its energy leaves unmeasured,
    the balance misses by a fifth, and the old rule reported "no problem found"
    with the whole discrepancy demoted to a footnote. It now says: you appear to
    be exporting, but nothing measures it — map a grid export sensor.
  - **A duplicated channel is named** even when the typical day stays inside the
    clean band. The detector could always find it; nothing ever asked.

  Your first verdict now takes two weeks rather than one. In exchange it stops
  changing when nothing about your house has.

  The same test decides whether removing a channel would settle the house, so it
  tightened there too, and a false positive went with it: on a grid of sixty
  two-array houses beside an unmetered draw, the case that used to call half of
  somebody's real generation a duplicate no longer arises anywhere.

- **The loss terms are fitted together rather than one at a time.** Generation
  and battery throughput rise and fall together and a continuous draw sits in
  both, so estimating each alone gives it a share of the others. Against a known
  96%-efficient inverter the generation term read 62% high. At 94% it read high
  enough to fall outside the window that would have accepted it, so *no* loss was
  subtracted at all and a healthy hybrid showed a 7% residual and reported "still
  looking" forever.

  On a real DC-coupled installation this took the reported difference from 16.4%
  to 3.8%, and the verdict from "still looking" to no problem found. Nothing was
  fitted that was not there: the correction it had been applying was for a loss
  the house does not have.

- **A generation sensor up to 15% above the rest of the system is now
  accounted for**, rather than leaving the house with no verdict at all. The old
  ceiling was a tenth, which cut through the middle of the range real inverters
  occupy: an installation whose generation is metered before a 88%-efficient
  conversion chain had nothing subtracted and reported "still looking"
  indefinitely.

  Beyond 15% — a conversion efficiency of 0.85, below anything currently sold —
  the engine goes back to saying it cannot explain the difference, because at
  that point it cannot.

- **The consumption sensor you told us about is now read.** Setup asks whether
  it covers the whole house; the answer was stored and never consulted. Answering
  no now opens the boundary, because whatever that sensor misses lands in the
  residual looking like a fault — so the engine was calling these houses fully
  measured on their owner's own word that they are not.

### Fixed

- **A house that never exports is no longer told that it does.** If your
  generation is metered before the inverter and the conversion loss is larger
  than the model will absorb, what is left over is large, daily, one-signed and
  concentrated in the sunny hours — which is exactly what energy leaving an
  unmetered export path looks like. So a self-consumption installation, whose
  surplus goes into its battery and which sends nothing to the grid at all, was
  told with high confidence to go and map an export sensor it has no use for.

  The hours that tell the two apart are the sunny ones where consumption is
  still ahead of generation. Nothing can leave the house in them, so unmeasured
  export accounts for nothing there, while a loss proportional to generation is
  present in proportion to generation. Night is quiet under either story and is
  most of the rest, so including it in the comparison had been hiding the
  difference.

  Being loud in those hours is not enough on its own to stay quiet, because a
  rented roof exporting its entire output is loud there too — at the full rate
  of generation rather than the small fraction an inverter loses. Both are
  required now, so a roof whose export really is unmapped is still told so,
  including when its inverter is metered on the DC side and both things are true
  at once.

- **A battery 90% efficient on its DC side is now recognised as one.** The two
  directions do not lose the same fraction — the charge side loses
  `gamma / (1 - gamma)` where the discharge side loses `gamma` — and they were
  being fitted as a single coefficient against the sum of the two. What comes
  back from that is a blend, and a blend is always above the smaller of the
  pair: at 90% efficiency the discharge coefficient is exactly 0.1000, which is
  exactly what the model accepts, while the blend was 0.1057, which it does not.
  So the model refused a loss its own bounds admit, subtracted nothing at all,
  and left 5.5% of the day's energy unexplained on a healthy installation.

  The two are now fitted apart, and the pair is checked against itself: one
  battery's directions imply one efficiency, and a pair that implies none is
  refused however small it is. That check earns its place immediately — on a
  house whose export is unmapped, the residual is largest exactly when the
  battery is charging, and without it the fit would take a spurious 4.4%
  discharge loss and silence a correct finding.

  When the pair is refused, the generation term is fitted again without it. A
  column fitted beside one that turned out to be explaining something other than
  loss carries part of whatever that was: two real battery banks beside a
  consumption clamp reading 55% put a spurious 2.6% into generation, which was
  enough to call the two banks a duplicated pair.

- **An inverter that idles at more than about 45 W is no longer left
  unexplained.** The continuous draw an inverter's own power supply makes is
  meant to be absorbed anywhere between 10 W and 120 W. It was also capped at a
  fifth of what the house uses overnight, and on an ordinary house that ceiling
  arrives at about 45 W — so most of the advertised range was unreachable, and a
  hybrid idling at 80 W, which is an unremarkable figure, reported "still
  looking" indefinitely.

  The cap was asking the right question the wrong way. What it was really for is
  telling a constant draw apart from a consumption sensor reading low by a
  fraction, and those are not distinguishable by size — but they are completely
  distinguishable by shape, because one is the same number every hour and the
  other is a share of whatever the house happened to use. Fitted side by side,
  each lands in its own column. A consumption channel reading even ten per cent
  low is still refused, as it was before.

  A draw larger than 120 W is still reported rather than absorbed, with the
  figure, as it was before.

- **A house with no battery can now have its inverter's own draw absorbed.**
  The continuous draw an inverter's power supply makes is measured at night, by
  fitting the residual against battery throughput — the slope is conversion
  loss, the intercept is whatever is drawn regardless. That fit refused to run
  at all without a battery discharge channel, so on an installation without a
  battery the draw was never subtracted, and in December, with ordinary meter
  noise, that was enough to leave the house reporting "still looking"
  indefinitely.

  A house with no battery is the degenerate case of the same line rather than a
  house the fit cannot speak about: there is no throughput to vary with, so
  there is no slope, and the intercept is the whole of it. The bounds on what
  may be called standby are unchanged.

  Found by a new corpus of 3,000 healthy installations, at eight of them.

## [0.16.0] - 2026-08-30

### Added

- **A verdict can now be replayed from a diagnostics download.** The analysis is
  a pure function of its hourly buckets, and until now none of them left the
  process — so every question about a live installation had to be settled by
  asking its owner to run something and wait a day. Three diagnoses of one house
  were made confidently and retracted that way.

  Diagnostics now carry the window itself: every hour, every channel, with where
  the number came from and how much it can be trusted. Anyone holding the file
  can run the engine over it and get the same answer, offline, as many times as
  they like.

  Values are written unrounded, unlike every figure shown to a person. Rounding
  them to milliwatt-hours was enough to move a replayed residual from 5e-16 to
  3e-06 — harmless in itself, and exactly the drift that stops a replay being
  evidence. Verified across eight different faults: the replayed report is
  identical in verdict, finding, residual, every measurement, loss model and
  notes.

  The download grows by about 89 kB for a month of six channels.

Nothing reads the new block yet. No behaviour changes.

## [0.15.0] - 2026-08-30

### Added

- **The night ledger separates the hours we measured from the hours we were
  told.** An hourly average of a sensor that only reports when it changes
  over-weights the busy part of that hour, so a power sensor read that way sits
  high while an energy counter beside it stays exact. The result is a night that
  does not add up with nothing whatever wrong — and in a single total it looks
  exactly like a sensor that genuinely under-reports.

  Solar Sanity's own hourly integration weights every reading by how long it
  stood, which makes it the control that was missing. If the shortfall sits in
  the hours taken from Home Assistant's statistics while the hours we integrated
  ourselves add up, the arithmetic is at fault rather than the house.

  A genuinely mis-measured channel still shows in both halves, so this cannot be
  read as excusing a house that really is wrong.

  Appears once both kinds of hour exist. A new installation is entirely
  backfilled, and a split with one empty half is the whole night under a second
  name.

Diagnostics only. No verdict changes.

## [0.14.0] - 2026-08-30

### Added

- **Hours with nothing supplying them are counted.** A month whose night does
  not add up looks the same whether every hour is a little short or a few hours
  are short of everything, and those have different causes. An hour drawing real
  power while every source reads under 25 Wh cannot be explained by any sensor
  reading the wrong amount — there is nothing to multiply — so it is proof that
  something stopped reporting rather than that something reports wrongly. The
  count is always present, so nought means nought.

  Measured on synthetic houses: a battery reading half, a battery reading a
  third and a consumption sensor reading double all produce residuals in the
  hundreds of watts and *no* such hours; supply sensors going blind one hour in
  six produce sixty of them and a smaller residual.

- **Where each channel's hours came from**, in the coverage section. An hourly
  arithmetic mean over a sensor that reports on change over-weights the busy
  part of an hour, so a power channel read that way can sit high while an
  energy counter beside it is exact — a difference that does not add up with
  nothing at all wrong. The integration already treated such hours with more
  caution; now you can see which ones they were.

Diagnostics only. No verdict changes.

## [0.13.0] - 2026-08-30

### Added

- **The night ledger now says *when* the gap happens, not just how big it is.**
  A whole-night total cannot tell you whether a shortfall is spread evenly or
  concentrated somewhere, and that difference is the diagnosis. The ledger is
  emitted three times: the whole night, the hours the grid was quiet, and the
  hours it was not.

  The three things that can be wrong leave different marks. A grid meter that
  under-reads shows a gap only in the hours the grid was involved. A battery
  that under-reports shows it where the battery works hardest. Consumption that
  over-reads shows it everywhere, because consumption happens in every hour.
  Reading those apart used to mean comparing two diagnostics downloads by hand.

  The halves are computed by the same path as the whole and are checked to sum
  back to it — hours, residual, and every channel individually. The split is
  omitted when one half would be the entire night, because publishing the same
  totals under a second name is how a reader comes to believe two numbers agreed
  when only one was ever computed.

  Diagnostics only. No verdict changes.

## [0.12.3] - 2026-08-29

### Fixed

- **Data completeness no longer reads 0% on every restart.** 0.12.2 taught it to
  remember through reloaded history, so that a sensor breaking while you
  restarted would still be reported. That history is loaded *before* the first
  reading is taken — so it remembered before there was anything to remember,
  and every restart on every installation showed 0% while the inverter was
  still coming up. Precisely the reading the behaviour exists to prevent,
  delivered to everybody rather than to the rare case it was meant for.

  It now waits on the clock instead, and only for the first answer: nothing read
  and under five minutes since startup is unknown; after that, nothing readable
  is 0%, because anything that was going to publish has published. Once
  something has been read the wait is over for good and an outage shows the
  moment it happens.

## [0.12.2] - 2026-08-29

The last two findings from the review that produced 0.12.1. Both concern what
the diagnostics download tells you; neither changes a verdict.

### Fixed

- **The night ledger reaches the installations that need it most.** It sat
  behind the night fit's preconditions rather than its own, and those are much
  stricter — a battery discharge channel, and two hundred night hours. So a
  house with no battery got no ledger at all, and every house got none for its
  first sixteen days. That fortnight is exactly when an installation reads
  "still looking" and its owner wants to know why, and the ledger is the thing
  that answers. Measured: fourteen days gave 182 usable hours and nothing,
  sixteen gave 208 and everything. It now runs first, and the fit adds to it
  when it can.

- **Data completeness no longer forgets what it knew when you restart.** It
  reads unknown until something has been read once, which is right at setup and
  wrong afterwards — the flag behind it was rebuilt on every reload. So a sensor
  would break, the figure would correctly drop to 0%, you would restart Home
  Assistant because that is the obvious thing to do, and the answer would go
  back to unknown for the rest of the outage. Information withdrawn precisely
  because you acted on it. It now remembers through the history it reloads.

  Deliberately not a time limit instead: a string inverter unreachable after
  dark, or an integration that publishes late, would then park a healthy house
  at 0% — which is the failure this behaviour was added to prevent. A genuinely
  new installation still has no history and still says unknown.

### Internal

- 874 tests here and 1021 with Home Assistant present, from 869 and 1015.

## [0.12.1] - 2026-08-29

An adversarial review of everything 0.11.0 and 0.12.0 shipped. Fourteen findings
raised, each one put to a verifier told to refute it by default; four were
refuted outright. Two of what survived were regressions introduced by those same
two releases, so this is worth taking before the last one.

### Fixed

- **A sensor dropout no longer deletes energy from the day.** The staleness
  guard added in 0.11.0 runs when a reading *arrives*, and no reading arrives
  while a sensor is away — so only the hour it came back in was ever
  distrusted. The hour it went quiet in kept a partial total, was stamped as
  good, and counted as a full hour. Dropping a whole hour is balanced: sources
  and sinks go together and the arithmetic still closes. A partial generation
  total beside a complete load and grid is not, and it has exactly the shape of
  a fault. Every hour a gap touches is now distrusted.

- **You will not be told to delete your own generation sensor.** On a roof that
  exports nearly everything with no export meter configured, the tie-break added
  in 0.12.0 reported "Solar production is being counted twice" and offered to
  remove the generation channel. Following that made the verdict `ok` by
  disposing of the sensor that was telling the truth. The right answer — that
  energy leaves by a path nothing measures — was passing every check and lost by
  a hundredth. It could not win: it names no channel, and the tie-break was a
  test only the other side was able to sit.

- **A correction is no longer called stale on a day or two of data.** The check
  that asks whether one of our own adjustments has outlived its fault ran ahead
  of the five-day floor every other stage answers to. On a one-day window any
  coincidence that cancelled the residual read as proof the adjustment was
  unwanted — a fifth of the time, measured, with the adjustment genuinely
  needed in every case. What that cost was a warning telling somebody to remove
  the one override keeping their generation channel honest.

- **The forecast card stops giving up for the day.** The release added in 0.11.0
  compared the current lifecycle phase against the phase the failure happened
  in, and a load can only ever run in one phase — so it always compared RUNNING
  against RUNNING and released nothing. A websocket blip, over in seconds, left
  the card reading "Cannot read the record" until local midnight. It now expires
  after five minutes. A load in flight across midnight also published the wrong
  day's figures permanently, and now checks the day it was started for.

### Changed

- **Two sentences that claimed more than the evidence supports.** A continuous
  unmetered draw was described as "Normal for this equipment" with nothing to
  fix — but an inverter idling and a circuit outside the clamp are the same
  signal to this fit, and a real 90 W circuit is 790 kWh a year. It now reports
  the figure, says where one like it usually comes from, and suggests finding it
  if you know of nothing that would draw it.

  A backwards sensor was described as one where "every reading it has made is
  negative", about channels that are mostly *exactly zero* — battery charging is
  flat zero in 79% of hours on a system it fires for. That is the one finding
  called certain, and anyone checking it against history had good reason to stop
  believing the rest. It now says zero or negative.

- **The night ledger published one number twice.** `night_sources_minus_sinks_wh`
  was the signed sum of the role totals, described as a check on the residual
  beside it. It is the same sum reassociated — the difference is exactly zero on
  every input. One name now. `night_ledger_hours` was also documented as a
  coverage signal, and it cannot be one: incomplete hours are discarded long
  before the ledger sees them, so it always equals `night_hours`. It stays,
  because it is what turns a shortfall into a rate.

- **Data completeness reads unknown before it knows anything**, rather than 0%.

### Internal

- The eight open dependency updates, taken as one batch. Vite 8 replaces Rollup
  with Rolldown, which needed two config migrations — and inverts the advice
  every card repository still carries: `codeSplitting: false` is the real option
  now and `inlineDynamicImports` is deprecated. The card came out smaller and
  faster: 41.2 kB raw and 12.9 kB gzipped, from 44.6 and 14.2.
- A smoke test on the built bundle. Every card test until now exercised the
  TypeScript and none of them touched the file you actually load, which is
  exactly the gap a change of minifier walks through.
- A card fixture that named a date literally and expired at midnight.
- 869 Python tests and 112 card tests, from 817 and 104.

## [0.12.0] - 2026-08-29

Found in a real installation's own diagnostics rather than in synthetic data.

### Added

- **The night ledger.** When the numbers do not add up overnight, the download
  now shows every channel's night total side by side — generation, import,
  export, battery in, battery out, consumption — over one agreed set of hours,
  along with the residual and the signed identity they should satisfy. Totals
  reconcile exactly, so the question becomes a subtraction you can check a line
  at a time, and the size of each line says which channel is short.

  What was there before was three medians: the middle hour of load, the middle
  hour of discharge, the middle hour of residual. Those are three *different*
  hours, so no arithmetic over them ever said whether the channels agreed — and
  grid import was not reported at all, which ruled out the most likely
  explanation for a night shortfall before anyone could look at it.

  Hours where any channel was silent are left out, so a channel that is merely
  absent for part of the night cannot look like one that is short.
  ``night_ledger_hours`` beside ``night_hours`` says whether that mattered. A
  channel you have not configured gets no line at all rather than a zero:
  reporting no export on a system with no export meter would state the one thing
  nobody can know without it.

- **The verdict now says what was measured.** A healthy installation used to get
  the word OK and nothing else, while the analysis behind it had fitted a loss
  model and measured a continuous draw nobody's sensors account for. If there is
  one, it is now said out loud — "About 35 W flows continuously that nothing
  measures" — and the figures behind every verdict, including OK and including a
  named fault, are in the diagnostics download. They were previously attached
  only where the engine was *unsure*, so the file was richest when there was
  least to say and empty on the verdict most worth auditing.

### Fixed

- **Data completeness no longer reads 0% before it knows anything.** Home
  Assistant gives an integration no way to wait for another one's entities, so
  at startup your inverter has usually not published yet, every channel reads as
  absent, and none-of-five was reported as 0%. On the device page that is
  indistinguishable from the failure the sensor exists to report, at the moment
  you are most likely to be looking at it. It now reads unknown until something
  has been read once, and 0 only after — nothing has arrived yet and everything
  has stopped are the same arithmetic and opposite facts.

- **Repair cards left behind by an installation you removed are cleared.**
  Removal has been handled since 0.3.2, but only from that version onward;
  anything orphaned earlier was stranded, because every other path matches
  against the current installation's id and an old card does not carry one. Its
  Fix button led to a flow that could only abort, making it a repair you could
  neither action nor permanently dismiss. Swept once at startup.

### Notes

- Two of the three loss-model notes are written and deliberately still silent.
  Their copy says a few per cent of loss is normal conversion and there is
  nothing to fix, and the fit cannot yet tell that apart from a continuous draw
  you are paying for — an 80 W draw on a system whose sensors both read AC
  produces a larger figure than a genuinely DC-measured one does. They wait for
  a fit that separates the terms.
- Nothing reports currency, and a check in CI keeps it that way.
- 817 tests, up from 750 at 0.11.0.

## [0.11.0] - 2026-08-28

Everything here was found by reading the code or by attacking it, not by a
failing test. Every one of them failed quietly — no exception, no entity going
unavailable, nothing turning red.

### Fixed

- **Forecast history was never being kept.** The recorder validates the ids we
  file forecasts under against a lowercase-only pattern, and Home Assistant has
  minted uppercase ids for new configuration entries since 2023.4 — so every
  write was refused, on every installation set up since then, and said so only
  in a debug line. Forecast history is the one record this integration cannot
  rebuild afterwards. **If you installed before this release, capture has not
  been running; it starts now, from today.**
- **A single net meter is no longer reported as a fault.** The setup screen asks
  you to put a meter that goes negative when exporting in the import slot and
  leave export empty. Doing exactly that produced a fault report, an instruction
  pointing at a slot that has never existed, and an offered correction that was
  implemented nowhere — it counted itself as applied and changed no number.
- **A sensor wired backwards now has a name.** It reads negative in every hour
  of its life, which is not a direction any of these measurements can flow in.
  Generation and consumption were never checked for it at all, so an inverted
  sensor left the numbers out by more than a hundred per cent and the verdict
  sat at "still investigating" indefinitely. Grid and battery were checked, and
  told they measured both directions at once — the wrong answer, and one with no
  remedy attached.
- **Two ways an hour could be credited with energy that never flowed.** An hour
  rolls over on the wall clock but nothing notices until the next five-minute
  tick, so a reading arriving in between was counted twice. And differencing a
  cumulative counter says energy flowed, never over how long — so an inverter
  reconnecting after a two-hour dropout put the whole gap into the hour it came
  back. Both inflated the mismatch on exactly the systems this exists to
  reassure.
- **The forecast card stopped giving up for the day.** The ordinary way to see
  the recorder refuse a query is a restart, before it has finished coming up,
  and the card latched that failure until midnight. It also still said
  "Tomorrow" at five past midnight while showing what was by then today.
- **The engine no longer depends on the order you mapped your channels in.** It
  measured a role — generation, say — by looking at whichever channel carrying
  that role happened to be listed first. On an installation with two arrays that
  made the loss model, and the verdict, depend on configuration order. The same
  fault was hiding a duplicated sensor on any system whose generation is
  measured before the inverter.
- **A finding you cannot act on is no longer shown as one you can.** The detail
  and the remedy lived only behind the Fix button, so every finding whose honest
  answer is a configuration change rather than an internal adjustment showed its
  headline and nothing else.
- **A non-finite reading no longer reaches the copy.** One could make a
  percentage come out as `nan`, and slip past its own guard, because every
  comparison against a `nan` is false. The gates are written the way round that
  rejects it now.

### Added

- **Two sensors measuring the same flow are named as a pair.** Each looks
  entirely spurious on its own, so the engine could not tell which of the two to
  blame and said nothing at all about an installation out by a third. It does
  not guess: it reports the pair, and offers no correction, because dropping
  either would close the balance and choosing between them would be a coin toss.
- **A near-copy is named on its own.** A second sensor reading a few per cent
  off its partner is a different case — one of them settles the numbers and the
  other does not — and that is now said plainly, with the one-click adjustment
  it has always had.
- **A correction that has outlived its fault is now noticed.** Adjustments here
  are applied so the rest of your system can keep being checked, never as a fix,
  so the underlying sensor usually does get repaired eventually. At that moment
  the adjustment stops compensating for a fault and becomes one. You are asked
  to remove it, rather than being told the now-correct sensor is broken and
  advised to break it again.
- **A forecast figure that describes no day is no longer published.** A month
  split between days well under and days well over has a perfectly ordinary
  average, and quoting it describes not one day you will actually see.

### Notes

- Nothing reports currency, and an AST check in CI keeps it that way.
- The analysis engine imports nothing from Home Assistant; purity is enforced
  structurally rather than by convention, and the report is now checked to be
  identical whatever order the channels arrive in.
- 750 tests, up from 466 at 0.10.0.

## [0.10.0] - 2026-08-28

### Added

- **A dashboard, laid out for you.** Settings → Dashboards → Add dashboard →
  **Solar Sanity** builds one with both cards already arranged. No YAML, nothing
  to configure.
- **A view strategy**, for a dashboard you already have:

  ```yaml
  views:
    - strategy:
        type: custom:solar-sanity
  ```

  The dashboard delegates to the view rather than repeating it, so there is one
  place that decides what a Solar Sanity view contains.

  The verdict goes first and the forecast under it — the forecast is what you
  look at when the verdict is boring, and putting it first would bury the
  answer. More than one installation gets one status card each, told which house
  it belongs to, rather than letting a card choose silently between them.

  With nothing installed the strategy still emits the status card, which already
  says "Solar Sanity is not set up yet" and offers a button that goes and sets it
  up. Writing that sentence a second time here would mean two copies to keep in
  step.

## [0.9.1] - 2026-08-28

### Fixed

- **A fault could overflow the status card.** The card is pinned to three rows
  on purpose — one that reflows the whole dashboard the moment something goes
  wrong is worse than no card — but a fault's explanation runs to 281
  characters, and nothing stopped it pushing the action button out of a box
  that cannot grow.

  The card now shows the first sentence, which is the observation about the
  reader's own house, and the rest stays where it was always going to be read
  properly: the Repairs entry that "Show me" opens, which carries the whole
  explanation and how to fix it. The unabridged text is also on the element, so
  hovering shows it.

  The sentence boundary requires a capital after the full stop, because the copy
  is full of figures like "1234.5" and splitting one mid-number would be worse
  than not splitting at all.

- Four explanations opened with a sentence too long to fit, and have been
  rewritten to lead with the observation and follow with the reasoning — the
  shape the other eighteen already had. There is now a test over every template
  that fails when a first sentence outgrows the card, so the copy and the space
  it has to live in cannot drift apart.

## [0.9.0] - 2026-08-28

### Added

- **The forecast scoring engine**, as a pure module with no entity behind it
  yet. It decides whether a bias figure has been earned, and its default answer
  is no.

  The residual check works because physics leaves an empty band — meter noise
  stops around five percent, the smallest real fault starts around fifty.
  **Forecast error has no such gap.** Model error, an omitted temperature
  derate, light soiling and a little shading all sit in the same five-to-fifteen
  percent range, so no forecast figure may ever be a fault, however large or
  however steady. At most it is a note, and the copy has to say plainly that it
  cannot tell a forecast running high apart from an array producing less than it
  could.

  The headline is the median of daily ratios, cross-checked against the
  energy-weighted figure and abandoned if the two disagree by more than five
  points. Then: twenty-one comparable days across at least twenty-eight, five in
  each third of the window with the sign agreeing in all three, day-to-day
  scatter under 0.60, no correlation between the gap and the size of the day, no
  steady drift, and split-half agreement. Only then a magnitude gate — eight
  percent to say a forecast runs low, twelve to say it runs high, because that
  second claim is the one that sends somebody onto a roof.

  Every threshold is multiplied by 1.6 when generation comes from hourly means,
  matching what the residual bands already do with the same data. An
  installation whose PV is mean-backed may therefore never qualify to state a
  bias at all. That is the correct outcome, not a reason to lower a constant.

  Reported to the nearest five points and never with a decimal, while the
  unrounded figure stays in `measurements` — measuring and asserting are
  different things.

## [0.8.2] - 2026-08-28

### Fixed

- **The status card reported "More than one installation" on a house with one.**
  0.7.1 fixed a lookup that broke on a renamed entity, and replaced it with one
  that matched *any* `sensor.*_status` — so a camera, an alarm panel, a router
  and an inverter all counted. Matching on the entity id does not work in either
  direction: requiring the domain slug breaks on a rename, and dropping it picks
  up the rest of the house.

  The card now recognises its entity by what it publishes. An enum sensor
  carries its `options`, and no other integration publishes this particular list
  of five verdicts. That survives a rename and cannot collide.
- **The forecast card labelled the provider "Home".** A Forecast.Solar entry is
  very often titled that, which says nothing about which integration it came
  from — and on a card whose point is comparing providers it is the one thing
  the reader needs. The archive metadata now leads with the product name, the
  way the setup flow already did. Existing archives are relabelled on the next
  capture.

## [0.8.1] - 2026-08-28

### Fixed

- **The test DOM added in 0.8.0 carried a critical advisory.** `happy-dom` 16
  has a VM context escape that can lead to remote code execution, plus two
  more. It is a development dependency and never reaches a user — the card
  ships as a bundle containing only Lit — but it runs in CI against this
  repository, and a public project should not carry that. Upgraded to 20.
- Added a Dependabot configuration, which the repository has never had. Finding
  the first advisory by cutting a release and reading the warning afterwards is
  not a process.

## [0.8.0] - 2026-08-28

### Added

- **A forecast card.** It shows one thing: what a provider said a day ahead
  about tomorrow, drawn from the archive that is written once per hour at real
  lead time and never revised. Hand-rolled inline SVG, so it re-themes with no
  JavaScript and the drawing is a string a test can read.

  It needs no new server-side API. The archives are Home Assistant external
  statistics, so `recorder/list_statistic_ids` and
  `recorder/statistics_during_period` already reach them — both non-admin, both
  stable, and the card reads only `state`.

  Every degraded state is a sentence: no provider configured, a provider whose
  record starts today, a recorder that will not answer. None of them is a red
  box, and none of them claims an accuracy nothing has measured yet.
- **Frontend tests.** There were none — the card's entire gate was that it
  typechecked and fitted in 90 kB. Sixty-four now run in CI, including a
  snapshot of the chart's `d` attribute, which is the drawing rather than the
  markup around it. It caught an axis that stopped below the data on the first
  run.

### Not added

- **A yield card**, deliberately. The guaranteed annual figure is a write-only
  option nothing reads; the only honest normalisation is year-over-year and it
  needs the year; and a headline built from the mean-derived generation data
  would be the first unhedged number in the product taken from the one grade
  the engine distrusts. Nothing is lost by waiting — long-term statistics are
  never purged, so the history a real version needs is accumulating either way.

## [0.7.1] - 2026-08-28

Groundwork for the forecast card, which turned up four defects that had nothing
to do with cards.

### Fixed

- **The options flow promised a feature that does not exist.** The guaranteed
  annual production field told users, in future tense, that Solar Sanity would
  track their production against it "corrected for how sunny each year actually
  was". No such correction has been designed — it is the open engineering
  question in that whole area — and nothing anywhere reads the figure. The help
  text now describes what actually happens: it is kept, and nothing reads it yet.
- **The no-currency rule was enforced over one directory of Python.** It never
  looked at the card sources, `strings.json`, the translations or
  `services.yaml`, which between them are most of the words this product says —
  and the cards are exactly where someone reaches for a payback figure. One
  expression now covers all of them.
- **The card reported a version six minors stale.** It was injected from
  `package.json`, which has never been bumped past 0.1.0, while the integration
  is at 0.7.x. Any compatibility check comparing the two would have been broken
  from the start. It now comes from `manifest.json`, so one number governs both
  halves.
- **The card told users to install what they already had.** Its entity lookup
  required the id to *end* in `_status`, so a renamed entity or a second
  installation fell through to "Solar Sanity is not set up yet" — an assertion
  about the world it had no grounds for, and one that invites adding a duplicate
  entry. It now matches the `_status` segment, reports honestly when the
  integration is present but not answering, and says so plainly rather than
  picking one at random when it finds more than one installation.
- The status type claimed an entity's state is always one of the five verdicts
  the engine emits. It is also `unavailable` and `unknown`, which is how those
  two came to share a branch with "not installed".

## [0.7.0] - 2026-08-27

### Fixed

- **Power channels are integrated over the durations they were actually held,
  not sampled every five minutes.** Assuming a reading held until the next tick
  put a standard deviation of roughly 570 Wh into a day on an event-reporting
  load channel — about 3.2% of a 25 kWh throughput once two power channels
  compound. A *healthy* installation therefore reported "Still looking" around
  half the time, and on a spikier load — an EV charger, resistive heating —
  closer to four times in five.

  It manufactured no false faults; the seven attribution gates held throughout.
  It manufactured false *doubt*, which for a product whose whole promise is a
  trustworthy verdict is its own kind of failure. It also quietly ate a slice of
  the 6%-to-50% gap the design rests on — the one the residual module describes
  as having nothing in it.

  Left-Riemann over state-change events, which is what Home Assistant's own
  integration helper does and what makes the result exact for a step-shaped
  signal rather than merely close. A kettle that runs for ninety seconds between
  two ticks is now counted; before, it was invisible.

  Every mapped entity is subscribed rather than only the ones that look like
  power at setup: an MQTT-backed inverter publishes *after* Home Assistant
  starts, so deciding then would have subscribed to nothing at all on exactly
  the installations this is written for.

  Energy channels are untouched — differencing is exact at any sampling rate.
- **A hole in an hour is no longer treated as a smaller hour.** If a power
  sensor is unreadable for more than three minutes of an hour, that hour is
  discarded for it rather than filled in with the energy that happened to be
  measured around the gap.
- `BucketSource.OWN_INTEGRAL` now deserves being the strongest grade in the
  model. It was previously applied to buckets that were statistically *worse*
  than the `LTS_MEAN` grade that exists specifically to be distrusted.

## [0.6.1] - 2026-08-27

### Fixed

- **Nothing caught the same house being configured twice.** The unique id was a
  join of the mapped entity ids, so remapping a single channel minted a new
  house — and remapping a channel is precisely why anyone was adding a second
  entry. Both then wrote the same forecast archive, each resuming its running
  total from what the other left, and neither ever reported a problem.

  Identity is now what an installation *measures*, checked against the entries
  that already exist. A shared **consumption** sensor is decisive and refused
  with a message naming the other installation: the balance is defined around
  load, so two claims on it describe the same house by construction. Any other
  shared sensor is named and then left to the user — one grid meter serving two
  sub-systems is a real arrangement, and refusing it outright would push them
  straight back to the workaround this exists to remove.

  Deliberately not an abort. `already_configured` is terminal and leaves the
  user with nowhere to go, which is the position that produced the duplicate in
  the first place.
- Reconfigure runs the same check, ignoring the entry being reconfigured.
- `unique_id` is no longer redacted from diagnostics. It held a join of the
  mapped entity ids, which appear unredacted in the same file two lines below —
  so the redaction concealed nothing while hiding which identity scheme an entry
  was created under, which is exactly what a duplicate-entry report needs.

## [0.6.0] - 2026-08-27

### Fixed

- **A battery nobody measures can now be described.** The finding and its copy
  have shipped since the first release and could never be reported, for two
  independent reasons — fixing either alone changed nothing.

  It had no residual model, so its explained fraction was zero on every input
  and it failed the first gate. And it was gated behind the daily bands, which
  ask how far a day's residual runs *in one direction* — while a store borrows
  in the afternoon and repays at night, so its net is near zero however much
  energy is moving. The band was not too strict; it was measuring the wrong
  quantity for this shape, and no threshold change would have helped.

  Storage is now reached on its own, carrying its own four conjunctive shape
  tests plus a per-day check that the trace swings like the fitted capacity and
  comes back where it started. An unmeasured export path is *not* exempted: it
  runs one way all day, the bands measure it exactly as intended, and where they
  keep it quiet they are right to.
- **The storage finding would have crashed the moment it won.** Its copy asks
  for `daily` and the probe supplied `daily_kwh`. The template-coverage suite
  added in 0.4.0 checks each template against a hand-declared field set, and
  that declaration was wrong — so the gate passed while the render raised. There
  is now a test that drives the engine to actually emit the finding, which is
  the only check a wrong declaration cannot fool.
- **The two floors for attribution were written down twice and had drifted.**
  A snap-table hypothesis needs about 160 valid hours, because its gamma is
  estimated from the upper quartile of them; the day floor said five. Every
  installation therefore spent two days being told no explanation was convincing
  when none had been generated. The day floor is now derived from the hour
  floor, a structural hypothesis is held to its own shorter one, and an
  invariant computes the required hour count from `percentile` itself rather
  than trusting a comment about it.

## [0.5.2] - 2026-08-27

### Fixed

- **A single UTC offset was applied to a month of history.** It was read once,
  from whatever the zone happened to be that afternoon, and added to every hour
  in the window. Twice a year that window contains a daylight-saving change, and
  on the wrong side of it every hour near local midnight lands on the
  neighbouring day — moving a night's energy into the wrong day on exactly the
  days the standby fit is already least trustworthy. Each hour now carries the
  local date it actually belongs to, resolved against the zone where the zone is
  known, and the analysis groups on that.
- **`is_dst_transition` was never set to `True` by anything.** The guard that
  drops a 23- or 25-hour day has existed since the first release and has never
  once fired. A day's length is now measured — local midnight to local midnight,
  converted to UTC before subtracting — so no transition rule has to be known.

  Israel's next change is **25 October 2026**, a 25-hour day. It would have been
  the first live test of a guard that did not work.

## [0.5.1] - 2026-08-27

### Fixed

- **Reconfigure could only change the sensor mapping.** The three topology
  answers and the forecast provider selection were write-once at setup, so a
  user who added a forecast integration afterwards, or realised their
  consumption sensor covers only the backup panel, had no way to say so. The
  only route that appeared to work was adding a second entry — which is how two
  installations end up fighting over one forecast archive. Reconfigure now has a
  second step asking exactly what setup asks, pre-filled from what is stored.
- **Reconfigure stamped every channel as the user's own**, including the ones
  they never touched. Origin is not cosmetic: an autodetected channel has its
  findings downgraded one confidence step, because a mapping nobody confirmed is
  weaker evidence than one somebody chose. A pass through the form without
  changing anything quietly promoted the confidence of the whole installation.
  A channel now keeps its origin unless its entity actually changed.
- The conditional question logic is shared between setup and reconfigure rather
  than written twice, so the two cannot drift into asking different things.

## [0.5.0] - 2026-08-27

Forecast scoring was designed, and the design's first conclusion was that the
archive it would have to read is not fit to be scored. This release fixes the
archive. No scoring number ships until it has clean history to stand on.

### Fixed

- **The running total was resumed from the wrong row, inflating it by a whole
  forecast horizon on every capture.** `get_last_statistics` returns the row
  with the *greatest* start — after any normal capture that is the far end of
  tomorrow's horizon, not the hour before the window about to be written. Each
  capture therefore added an entire horizon to a total that should have advanced
  by one hour, roughly fifty times a day. The `sum` column is bookkeeping the
  recorder's contract requires, so nothing user-facing was wrong — but anything
  reading `change` off that archive would have got a day's energy per hour.
- **A failed lookup restarted the total at zero**, which sends `sum` backwards
  and makes `change` massively negative at the join. It now declines to write.
  Zero is the truth only for an archive that is genuinely empty; everywhere else
  it is the "`None` silently became a number" idiom wearing a different hat.
- **Two installations shared one state file** and overwrote each other's fitted
  loss model on every analysis — the second write simply won, and the first
  entry silently inherited a model fitted on a different house. One file per
  entry now, read back from the old one once so nothing already learned is lost,
  and removed with the entry.
- **Two installations selecting the same forecast provider wrote to the same
  archive**, each resuming from what the other left. One owner is now elected
  per provider, deterministically and afresh every capture, so deleting the
  owning entry hands the archive over within one interval rather than silently
  stopping the one thing that cannot be backfilled.

### Added

- **A day-ahead archive, kept separately from the rolling one.** A provider
  revises its forecast all day and the rolling series keeps only the latest
  revision, so by the time an hour has passed, what was stored for it was issued
  minutes before — not the day before. Scoring that and calling it a day-ahead
  forecast would flatter every provider equally and mean nothing. An hour now
  lands in `solar_sanity:dayahead_<provider>` on its first sighting at twelve
  hours of lead, and is never revised afterwards.
- `async_forecast_series`, which reads `state` and only `state`. Never `sum`,
  never `change`.
- Diagnostics report both archives per provider: row counts, the day-ahead
  span, and whether this entry owns the archive at all.

## [0.4.2] - 2026-08-27

### Added

- **Diagnostics report what was measured and then not acted on.** A rejected
  fit leaves its term at `0.0` and reports the same empty `fitted_terms`
  whether the slope it saw was a quarter or a rounding error — completely
  different problems, indistinguishable from outside the process. The new
  `measurements` block carries the raw night slope and intercept, the median
  night load and battery throughput, the **signed** daily residual, and how many
  days fell in each band rather than only what the last one did.

  "Nothing could be established" is now a statement with numbers behind it.

## [0.4.1] - 2026-08-27

### Fixed

- **The standby term could never be fitted on a house with a battery.** It was
  estimated from night hours in which the battery was *also* idle, and on a
  house whose battery carries the load overnight those hours do not exist. So
  the term that exists to absorb an inverter's own draw was unmeasurable on
  exactly the systems that have one, and its energy stayed in the residual
  looking like a fault. It is now fitted jointly with the battery's conversion
  loss: at night the residual is a straight line in battery throughput, and the
  slope and intercept are the two numbers wanted.
- **The battery's DC conversion loss needed daylight to be established**, which
  is precisely what an open boundary makes unusable. It can now be taken from
  the night slope — but only once generation has independently been shown to be
  measured DC-side, since on such a system a DC-measured battery is the
  expected topology rather than a coincidence.
- **A day's absolute tolerance floor was applied at full-day size to a partial
  day.** The verifiable-hours check feeds it eleven-hour windows, so a night
  running a quarter out for a month was judged against a whole day's worth of
  "not enough energy in play to care" and read as quiet. The floor is now
  prorated by the hours actually covered.

### Added

- **A continuous unmetered draw is reported rather than absorbed.** The loss
  model refuses anything larger than an inverter idles at, which is right —
  quietly subtracting a kilowatt-hour a day as "normal" would hide the thing
  most worth knowing. Having measured it and said nothing was no better, so it
  now appears as a note: *"Something draws about 200 W continuously that nothing
  measures — roughly 4.8 kWh a day."*
- A guard so a consumption sensor reading half cannot be absorbed as standby.
  At 250 W of night load, half the house is a plausible-looking 125 W and no
  absolute bound separates them; the share of the load it would have to
  represent does.

## [0.4.0] - 2026-08-27

### Fixed

- **Four of the six inferred fault types crashed the analysis at the moment
  they were found.** Both half-coverage variants and both unit-scale variants
  needed copy fields the inferred renderer never supplied, so each raised
  `KeyError` on the way out. Inside Home Assistant that takes the coordinator
  update with it and every entity goes unavailable — on precisely the
  installations the product exists to help. `faults.render` raises on a missing
  field deliberately, which is right only if the crash happens in CI; no test
  had ever driven those codes as far as a rendered finding. There is now a
  suite that reads the placeholders out of every template and checks them
  against the field set each renderer actually builds.

### Added

- **A verdict for houses that cannot close their balance.** With no export
  meter there is no measurement separating energy that left from a sensor
  reading high — in a surplus hour those are the same number, and no amount of
  waiting produces a third one. "Still looking" was a promise that could never
  be kept.

  The hours with no generation are ordinary arithmetic: nothing can be
  exported, so import plus discharge really does have to equal consumption.
  Those hours are now checked on their own, and the status says exactly what
  the verdict covers — including that the generation sensor is not part of it,
  since it only produces energy during the hours that cannot be checked.

  A fault visible at night is found this way even on a system whose daylight
  hours are unfalsifiable.
- The status sensor carries `notes`: never a fault, never a Repairs issue, never
  an alarm — what a verdict does *not* cover. A clean status that quietly
  covered half the hours is worse than no status.

## [0.3.2] - 2026-08-27

### Fixed

- **Deleting an installation left its repair card behind**, offering to fix a
  configuration that no longer exists and able to do nothing but abort when
  clicked. It cleared on the next restart, which is exactly when a user is least
  likely to connect the two events. Issues are now removed with the entry — on
  removal, not on unload, since a reload unloads too and flapping the issue
  would lose the user's dismissal.

## [0.3.1] - 2026-08-27

### Added

- Diagnostics carry `identity_fails` and the loss model's `fitted_terms`. Both
  distinguish a measured answer from a defaulted one: "Still looking" is reached
  two ways with the same wording, and every loss term falls back to `0.0`
  whether it was established as zero or never established at all.

## [0.3.0] - 2026-08-27

Everything here came out of one real installation sitting at a 40% energy
imbalance while the integration reported "Still looking" and its **Data problem**
sensor reported **OK**.

### Fixed

- **A house with no export sensor was treated as fully measured.** Closure
  checked that *a* grid sensor existed, not that both directions were covered,
  so an import-only mapping was called closed — while every exported watt-hour
  arrived in the residual as generation that had gone missing. It is now
  reported as an open boundary, and the reason reaches the user instead of being
  computed and discarded.
- **"You appear to be exporting, but nothing measures it" could never be
  said.** The fault code and its copy have shipped since the first release with
  nothing able to emit them. There is now a hypothesis behind it, discriminating
  on *when* the energy goes missing: export cannot happen while consumption
  exceeds generation, and a miscounted sensor does not care what time it is.
- **The `Data problem` binary sensor reported OK on a proven imbalance.** It
  judged only whether a sensor had been named, so an installation whose balance
  had been shown to miss by more than a tenth of throughput on five of the last
  seven days read as healthy. Not knowing the cause is not the same as there not
  being one. This is the entity most likely to be in somebody's automation, and
  nothing had ever tested it.
- **A battery published as one signed figure was invisible.** The check for a
  net meter in a one-way slot covered the grid roles only, so a battery sensor
  swinging both ways passed every categorical screen and had its sign quietly
  absorbed into the arithmetic. It is now caught from ordinary hours, with no
  waiting and no statistics.
- **The loss model oscillated instead of converging.** It was fitted against the
  residual the previous model had already been subtracted from, so it estimated
  the loss that *remained* rather than the loss that was there — then that was
  persisted and fed back as the next prior. The reported status alternated with
  it on every refresh. The fit now runs against the raw residual and is
  idempotent.
- **A rejected fit was indistinguishable from a genuine result.** Every term
  defaults to `0.0`, so "we could not establish this" and "there is no loss
  here" were the same answer — and the second was then asserted downstream as
  fact ("battery measured on the AC side"). Terms now record whether they were
  established.
- **A categorical fault could be asserted at full confidence on hourly means.**
  The inferred path has always widened its uncertainty for mean-derived data;
  the screen path never did.
- **Discovery created the exact fault this product exists to detect.** A sensor
  named for both battery directions scored a confident match for each, because
  every role was scored in isolation, and the one listed first in the keyword
  table simply took it — leaving the other direction to a second sensor and the
  same energy counted twice. Such a name is now demoted below the auto-pick
  threshold: still offered, never chosen without a human.
- A shortage no longer accuses a sensor whose history merely starts later. A
  channel with gaps and a channel that is younger look identical in a coverage
  count and mean opposite things: one resolves by waiting, the other does not.
- When no explanation has been *generated* — as opposed to generated and
  rejected — the status now says so rather than implying candidates were weighed.

## [0.2.3] - 2026-08-26

### Added

- **Diagnostics now explain the verdict.** "Download diagnostics" on the device
  page carries per-channel coverage: which sensors the recorder holds
  statistics for, how many hourly rows each contributed to the backfill, how
  many hours hold a value, and how many hours of each local day are complete.
  Three backfill defects have been diagnosed by asking a user to read state
  attributes back one at a time; that button is on the same page as the sensor
  and answers the whole question at once.

### Fixed

- Data completeness and Live residual read 0% for five minutes after every
  restart — they refreshed only on the five-minute tick, so they showed exactly
  the failure they exist to report, at the moment a user is most likely to look
  at them. They now refresh on the 30-second tick.
- The backfill log counted statistic ids rather than hourly rows. Ids are never
  zero once classification succeeds, so it read as success during an
  investigation into why nothing had been backfilled.

## [0.2.2] - 2026-08-26

### Fixed

- **A channel with no recorded history reported "Not enough data yet" forever.**
  A bucket needs every balance channel, so one unrecorded sensor invalidated all
  720 of them — and the guard only bailed when *every* channel was unrecorded.
  The status now reports `not_checkable` and names the sensor, because waiting
  cannot fix a sensor nobody is recording.
- **The 20-valid-hours-per-day requirement was a cliff, not a floor.** Twenty
  valid hours yielded a full month of usable days; nineteen yielded none. Since
  a bucket needs every channel, each channel's outages union together, so five
  channels each missing a different hour lost the whole day. Lowered to 18.
- **An energy statistic could be mean-queried as though it were power**, which
  applies no unit conversion and stores a kWh mean as watt-hours — a
  thousandfold error producing valid-looking buckets.
- The backfill treated a dict of empty lists as success, and logged the number
  of statistic ids rather than rows, so its log actively misled during exactly
  the investigation it existed to support.

### Added

- Shortage reasons name the limiting channel: "Grid import has data for only 14
  of 720 hours, and an hour needs every sensor."
- Tests for the coverage-gap path. Every backfill bug so far shipped through
  that hole — nothing built a bucket from statistics-shaped data and checked
  what the engine concluded.

## [0.2.1] - 2026-08-26

### Fixed

- **The backfill still did nothing**, for a second reason. It classified each
  channel by reading the entity's live state at setup — but an MQTT-backed
  inverter publishes after Home Assistant starts, so every channel classified
  as neither power nor energy and no query ran at all. It now asks the recorder
  what statistics it holds, which describes history that already exists and
  does not care whether the entity has loaded.

### Added

- `Status` lists `unrecorded_entities` when a mapped sensor has no recorded
  history, with a note explaining why. Without statistics no verdict can come
  from history at all, and the user needs to know which sensor.
- `solar_sanity.validate_now` re-runs the backfill as well as the analysis, so
  diagnosing this no longer needs a restart.

## [0.2.0] - 2026-08-26

### Fixed

- **The statistics backfill produced nothing whenever a power sensor was
  mapped**, so "an answer on day one rather than day seven" was aspirational
  rather than true. It asked for `change`, which only exists for statistics
  with a sum; power sensors are `measurement` class and have none, so every row
  came back empty and one missing channel invalidated the whole hour.

  Power channels are now read as the hourly `mean` and multiplied by the hour.
  That is a better figure than our own sampling — the recorder observed every
  state change, where polling sees one in three hundred.

- **Mean-derived readings were discarded rather than graded.** `Bucket.value`
  treated anything that was not `OK` as absent, so even once the backfill
  returned data it would have been thrown away. They are now usable but weaker:
  the tolerance widens and no finding built on them can be called certain.
  `Quality.DERIVED_FROM_MEAN` and `BucketSource.LTS_MEAN` finally have a writer.

Closes #5.

## [0.1.6] - 2026-08-25

### Changed

- `Live residual` is no longer created on systems where it cannot report. It
  needs every balance channel to give a rate, so one energy channel rules it
  out — an amount cannot answer "what is flowing right now". Previously the
  entity existed and was permanently blank; being disabled by default hid that
  rather than fixing it.

## [0.1.5] - 2026-08-25

### Fixed

- **Data completeness stuck at 0%.** Sensors describing live state were only
  rewritten when the coordinator updated, which is every six hours. If the
  integration loaded before the inverter's entities had published — the normal
  case for MQTT-backed inverters — the first reading found nothing readable and
  nothing corrected it for six hours. The 5-minute sampling tick now refreshes
  those entities, without re-running the analysis.

## [0.1.4] - 2026-08-25

### Fixed

- **Three sensors could never produce a value.** `Expected tomorrow` and
  `Live residual` had value functions returning `None` unconditionally, so they
  advertised capabilities that did not exist. `Data completeness` reported days
  of history under a name and description promising the fraction of inputs
  present — a different question wearing the same unit.

  All three now measure what they claim: tomorrow's total comes from the
  captured forecast payload, the live residual from the most recent snapshot,
  and completeness from how many configured channels are currently readable.

  `Live residual` remains blank on any system with an energy channel, because
  the live tier is skipped there. That absence is the honest answer; a zero
  would not be.

## [0.1.3] - 2026-08-25

### Fixed

- **Energy sensors were integrated as if they were power.** A daily-resetting
  total deposited roughly the whole running day into every hour — wrong by
  ~11.5x per day on generation, and up to ~1900x on a lifetime battery counter.
  The engine's response was not silence: it would name a high-confidence
  "counted twice" fault with a one-click button that zeroed the channel. Power
  is now integrated, energy differenced, and a reset marks the hour untrusted
  rather than guessing.
- **Unit conversion could stop every channel dead.** Home Assistant's
  converters raise `HomeAssistantError`, which the handler did not catch, so
  one odd sensor produced a traceback every five minutes and no channel
  accumulated at all.
- **Discovery could suggest a forecast entity as the panels** — the
  predecessor's bug, reproduced.
- **"charge" matched "discharge"**, which could swap the two battery
  directions without ever surfacing as a fault.
- **The same entity could be mapped to two roles**, cancelling itself out of
  the balance.
- **Two "fix it" buttons did nothing** and the finding returned forever.
- **Days were UTC days**, splitting the solar curve in two away from Greenwich.
- A partial first bucket after a restart claimed to be a full hour.

### Changed

- Setup no longer asks whether you have a battery when you have just mapped
  battery sensors. Each question appears only when genuinely open.
- The forecast picker names the integration ("Forecast.Solar — Home")
  rather than showing a bare entry title.

## [0.1.2] - 2026-08-25

### Fixed

- **Setup failed with "Unknown error occurred".** The topology step built
  `selector.selector({"config_entry": {"multiple": True}})`, but
  `ConfigEntrySelector` has no `multiple` option — its config schema accepts
  `integration` and nothing else — so constructing that schema raised
  `vol.Invalid`. Home Assistant reported it on the *previous* step, which
  pointed at the wrong place entirely. Nobody could complete setup.

### Changed

- Forecast providers are now chosen from a named list rather than by config
  entry, so the picker shows "Forecast.Solar" instead of a UUID. The field is
  omitted entirely when no provider is installed.

### Added

- Tests that construct every config-flow and options-flow schema, and that
  import every module the way Home Assistant loads it. A selector's config is
  only validated when the schema is built, so nothing in the existing suite
  could have caught this.

## [0.1.1] - 2026-08-25

### Added

- Brand icons, shipped inside the integration at
  `custom_components/solar_sanity/brand/`. Home Assistant serves them from there
  as of 2026.3, taking priority over the brands CDN.

### Fixed

- The analysis package used absolute `from analysis.x import` internally, which
  resolved under pytest but failed on a real install with
  `No module named 'analysis'` — Home Assistant puts the *config* directory on
  the path, not the integration's own. The integration would not have loaded.
  Relative imports resolve either way.
- The release workflow could not attach assets; the default token is read-only
  on this repository and needed `contents: write`.

### Notes

- v0.1.0's archive predates the icons, so installs pinned to that tag have none.
- The `home-assistant/brands` repository stopped accepting custom-integration
  icons in 2026.3, so the HACS brands check now passes on locally-provided
  assets rather than being skipped.

## [0.1.0] - 2026-08-25

Complete rewrite. Replaces `ha_smart_solar_manager`, which was a threshold-based
optimizer; this is a data-verification tool and shares no code with it.

### Added

- **Energy balance validation.** Checks that solar + import + discharge equals
  load + export + charge, and names the responsible sensor when it does not.
  Findings surface in the Repairs panel with the real fix alongside.
- **Forecast retention.** Records each provider's day-ahead forecast as external
  statistics, because Home Assistant's forecast integrations set no
  `state_class` and their history is purged within about ten days.
- **A bundled dashboard card**, served and registered by the integration itself.
- **Discovery** from the Energy Dashboard, returning ranked candidates with
  reasons rather than a bare entity id.
- **Reconfigure flow**, so a mis-mapped sensor can be corrected without deleting
  the entry and orphaning its history.
- **Diagnostics** download, with location redacted.
- CI: hassfest, HACS validation, tests, lint, bundle-size budget, and explicit
  gates for the Home Assistant 2026.11 statistics and 2026.12 config-entry
  deprecations.

### Notes

- Nothing reports currency, and an AST check in CI keeps it that way.
- The analysis engine imports nothing from Home Assistant; purity is enforced
  structurally rather than by convention.
