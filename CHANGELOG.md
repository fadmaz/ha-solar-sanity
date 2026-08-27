# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

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
