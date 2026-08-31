# Changelog

All notable changes are documented here. This project follows Semantic Versioning.

## [Unreleased]

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
