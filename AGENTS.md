# AGENTS.md

Instructions for any coding agent working in this repository: Codex, Claude Code, Cursor,
Copilot, Gemini CLI, Aider and the rest. `CLAUDE.md` imports this file, and `.gemini/` and
`.aider.conf.yml` point at it, so this is the one to edit. Keep it under 32 KiB: Codex stops
reading project instructions there by default.

Solar Sanity is a Home Assistant custom integration (`custom_components/solar_sanity`) with a
Lit card (`frontend/src`): a battery outlook projected from the house's own history and a solar
forecast archive, corrected for what the roof really delivers. This file holds only what the
code does not tell you.

## Read first

- `README.md`: what a user sees, every entity and option.
- `docs/ARCHITECTURE.md`: the structure, the outlook, the persistent store, the cards, CI and the
  ranked known risks. Section 9 says where to start reading the code. Keep it current when
  structure changes.
- `CHANGELOG.md`: why each behaviour is the way it is, release by release. Most constants are
  explained there and in the comment beside them.
- `docs/OPEN_THREADS.md`: where the work stands and what is next. Update it when you close or
  open a thread.

## Setting up

- Python 3.13 or newer for the full suite: `pip install -r requirements-dev.txt` installs Home
  Assistant and unlocks `tests/integration`. Without it the suite still runs and the integration
  tier skips (see below).
- Node 22, as CI and `release.yml` use: `npm ci`, then `npm run build` before `npm test`.
- The backtest needs the IANA time-zone database. On Windows that is `pip install tzdata`;
  without it `--tz Asia/Jerusalem` fails before anything is read.

## Working with the maintainer

- **Write to the maintainer in Arabic.** They write in Arabic, and English replies have been
  taken as not listening. Code, comments, commit messages, PRs and the CHANGELOG stay English.
- **Finish the work.** The standing instruction is to carry a change all the way:
  - branch and commit;
  - open the PR, with Auto-fix on where the tool offers it;
  - wait for CI to be green at both Home Assistant versions;
  - merge with a merge commit, delete the branch;
  - release.

  Do not stop to ask at each step. Propose a product choice before building it (new entities,
  removals, behaviour the user sees); once it is agreed, build and ship it without asking again.
- **Merge yourself.** GitHub auto-merge is not enabled on the repository and `main` has no
  required checks, so nothing merges on its own. Changing either is the maintainer's call.
- **Product preferences:**
  - **English-only product.** Cards, editors, setup, sensors, reasons and docs
    are English only. Do not add translations or a language selector. Arabic
    is for communication with the maintainer, not the product (2026-11-10).
  - Publish forecast figures only after the roof correction. The provider's own sensors
    already show the figure for an ideal roof (0.33.0).
  - No countdown sensors: the timestamp sensors already count down in the frontend, and
    `format: datetime` shows the exact time.
  - No copies of another integration's sensors as new entities. The card reads them directly.
- **Diagnostics stay private:**
  - Downloads go in the git-ignored `diagnostics/` and are never committed, attached or quoted
    whole. They carry a real house's entity ids, inverter brand, Home Assistant setup and a
    month of its consumption.
  - Test fixtures are synthetic.
  - Do not put the reference installation's entity ids or inverter brand in commits, PRs or
    issues.

## The reference installation

The maintainer's own house. Every threshold was measured on it, and a change that breaks its
signature needs evidence from it first.

- **Setup:**
  - a DC-coupled hybrid inverter with no grid export at all;
  - Forecast.Solar for the forecast;
  - time zone `Asia/Jerusalem`.
- **Battery:**
  - 30 kWh usable, reserve 35%, target 100%.
  - The inverter hands the house to the grid at about 35%, then charges the battery from the
    grid until morning. So nights bottom at 34-35, and charge-level hours after that are not
    the battery's own.
  - Charging ceiling about 3.4 kWh an hour.
  - The inverter draws about 90 W of its own that the house meter never sees.
  - The charge level reports whole percent.
- **Channels:** solar, house load and grid import are power sensors (W). Battery charge and
  discharge are lifetime energy counters (kWh), which is why the card's live battery figure
  needs the battery power option.
- **The forecast's gap to the roof:**
  - Forecast.Solar runs at about twice the roof overall, but not evenly: about a third of a
    sunny day's forecast arrives, and anything from half to one and a half times a cloudy
    day's. That is why the scale is learned by the kind of day (0.32.0).
  - Its last capture of a day rewrites the passed hours to a clear sky.
  - Its archive is keyed by period end before 2026-11-08 (`--period-end-before`).
- **A recurring load** of about 3 kWh around 06:00 local.

## Commands

```bash
python scripts/check.py                                    # every gate in CI order: ruff, tsc, pytest, build, vitest, size budget; stops at the first failure
python -m pytest tests -q                                  # what CI runs; about a second without Home Assistant
python -m pytest tests/analysis/test_outlook.py -q         # one file
python -m pytest "tests/analysis/test_purity.py::TestPurity::test_no_homeassistant_imports" -q   # one node id
ruff check . && ruff format --check .                      # the only Python gates; no mypy, no pre-commit
pip install -r requirements-dev.txt                        # Python 3.13 or newer only; installs Home Assistant and unlocks tests/integration
npm ci && npm run lint && npm test                         # tsc --noEmit, then vitest
npx vitest run frontend/src/chart.test.ts -t "substring"   # one card file, one test
npm run build                                              # writes the git-ignored bundle into custom_components/solar_sanity/frontend/
python scripts/check_size.py                               # gzip budget for that bundle; from the repo root, after a build
npm run start                                              # rebuild on change and serve the bundle on :4000; nothing documents how a dev HA consumes it
python scripts/make_brand_assets.py                        # regenerates the tracked brand PNGs; needs Pillow, which no requirements file lists
```

Grade the battery outlook against what a real battery did, before changing any outlook
constant (put downloads in the git-ignored
`diagnostics/`; a download older than 0.28.0 needs the charge level as a history export via
`--soc-csv`, and its nights are graded against production history rather than a forecast):

```bash
python scripts/backtest_outlook.py diagnostics/<download>.json --tz Asia/Jerusalem --evening 18 20 22 --period-end-before 2026-11-08T00:00:00+00:00 --verbose
```

`--period-end-before` is for a Forecast.Solar archive written on Home Assistant older than
2026.11, which keyed each hour by its end. Take the instant off the archive (the first day whose
sunrise hour holds a full hour of energy), not off the upgrade date: day-ahead rows are written up
to two days ahead and never revised. Capacity and reserve default to the download's own settings.
`--raw` replays without the learned standby and forecast scale.

The gate the outlook passed before the energy-balance check was deleted (at least 14 distinct
nights, not projections; mean absolute lowest error at most 5 points; reserve calls right at
least 85%) is the bar for any change to it.

Without Home Assistant installed, `tests/integration` is not collected and nine test modules
skip, silently. At 0.34.1 that is 292 tests locally, plus 165 card tests; CI adds the integration tier. Say in the PR when the HA tier did not run locally. The `Unknown config option:
asyncio_mode` warning on such a machine is expected.

## Branch, commit, PR and release conventions

These date from v0.22.0 (2026-08-31). Earlier history used release commits, annotated tags and an
Unreleased section, and is not precedent.

- Branches are `<type>/<kebab-phrase>` with types fix, feat, test, docs, chore. Everything lands
  through a pull request into main. Nobody reviews. Merge with a merge commit, never squash or rebase.
- Commit subjects are `type: lowercase statement of the behaviour or finding`, no scope, no period.
  Bodies are prose saying what was found and why, wrapped near 80 columns, ending with a
  `Co-Authored-By: Claude ... <noreply@anthropic.com>` trailer.
- PR titles are a `type:` sentence, written fresh for a multi-commit PR rather than copied from a
  commit. Bodies are prose with `##` headings and end with the test counts, local and CI.
- Delete the branch on GitHub after merging; auto-delete is off. Local `origin/*` refs go stale, so
  `git fetch --prune`.
- A release ships inside its PR: add `## [X.Y.Z] - YYYY-MM-DD` at the top of CHANGELOG.md on the
  branch, written for the user, under Added, Changed, Fixed, Removed, Notes or Internal, and bump
  `version` in manifest.json in the **same commit** as that heading. No Unreleased section and no
  release commit. `tests/test_release_contract.py` fails the PR when the two disagree, and
  release.yml fails a published release when the manifest and the tag disagree. Home Assistant reads
  the committed file for the integrations page, for diagnostics and for the card's `?v=`
  cache-buster, so a checkout that lags the tags is a checkout that lies. It lagged four releases
  before 0.26.0 because release.yml stamps the tag in and nothing else looked.
- After merging: `gh release create vX.Y.Z --target main --title "X.Y.Z — <sentence>"` with the
  changelog section as the body, taking `X.Y.Z` verbatim from the top heading of CHANGELOG.md. That
  is the actual guard against a mistyped tag; the release job's check is the receipt. feat gives a
  minor version, fix a patch; test and docs PRs get no release. Re-run release.yml from
  workflow_dispatch with the tag to retry a failed upload.
- Never cut a tag from a variable you have not checked. On 2026-11-09 a `grep -P` that the
  Windows shell's locale did not support returned nothing, and a release tagged `v` went out
  and had to be deleted. Read the version with `sed`, compare it with the manifest, and stop
  on a mismatch or an empty string:

  ```bash
  V=$(sed -n 's/^## \[\([0-9.]*\)\].*/\1/p' CHANGELOG.md | head -1)
  M=$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' custom_components/solar_sanity/manifest.json)
  [ -n "$V" ] && [ "$V" = "$M" ] || { echo "version mismatch: '$V' vs '$M'"; exit 1; }
  ```
- Dependabot PRs are not merged as opened; bumps are redone as one `chore/` batch PR run through the
  full gate.
- Constants and non-obvious branches carry a comment naming the measurement or failure that set
  them; tests are sentence-named with a docstring recording what motivated them. Keep both when
  changing a threshold.

## Gotchas

CI-only checks that nothing local runs:

- `async_add_external_statistics` may appear only in statistics_source.py (a comment elsewhere
  fails), which must contain the literal `mean_type=StatisticMeanType.NONE` and
  `unit_class="energy"`. `add_update_listener` and `async_register_command` must not appear
  anywhere under custom_components, comments included.
- hassfest and the HACS action float on `@master` and `@main`, and ruff is unpinned, so a red
  Monday run with no commits is upstream.

Tests that grep source text, so the failure names nothing near the edit:

- forecast-card.ts must say "currency" in a comment and nowhere else. main.ts must carry the GitHub
  URL in code. sensor.py and config_flow.py must write `translation_key="literal"`; a constant
  silently drops out of the join, and the card finds the outlook sensors by those literals.
  services.yaml keys are matched by `^[a-z_]+:`.
- Currency words (price, cost, bill, saving, tariff, currency, cent, their plurals, and `$` next to
  a digit) fail in analysis/ strings, in .ts with comments stripped, and in strings.json,
  translations/ and services.yaml with nothing stripped: a YAML comment saying "cost" fails.
- analysis/ is AST-policed from tests/analysis/test_purity.py: single-dot relative imports
  only; a stdlib allowlist of dataclasses, datetime, enum, math, typing, collections, __future__,
  abc, functools and itertools (so `re`, `json` and `statistics` fail); no random, time, secrets or
  uuid; `x or 0` and `x or False` banned. ruff's PLC0415 also applies inside analysis/, unlike the
  top-level component files.
- Adding an entity or service: strings.json and translations/en.json stay identical, and a
  service goes in services.yaml and both string files.
- Learn and grade from the `dayahead` forecast series, never `latest`: for an elapsed hour
  `latest` holds the day's last capture, which on the reference installation was clear-sky-like
  rather than anything a projection saw. The rule is about hours that have passed: the
  forecast card draws tomorrow from `latest` on purpose, because every hour of tomorrow is
  still to come and `dayahead` holds its first sighting, about two days old.

Traps whose error message points elsewhere:

- A function given to `async_track_time_interval`, a state listener or the bus must be `async def`
  or `@callback`. A plain `def` runs in a worker thread, where `async_write_ha_state` raises; the
  error lands in the log as a future nobody awaited, and the sensors just stop moving. Both ticks
  in `__init__.py` were plain until 0.31.1. `tests/test_event_loop_callbacks.py` walks the source,
  and a test that calls a refresh by hand cannot see it: fire the timer instead.
- In tests/integration request `recorder_mock` before `hass`, request `enable_custom_integrations`
  yourself, and call `entry.add_to_hass(hass)`. The wrong fixture order fails as a bare
  `assert not [True]`.
- Setting up one config entry sets up every configured entry of the domain. An entry meant to be
  down needs `disabled_by=ConfigEntryDisabler.USER`.
- Every analysis enum exists twice in one pytest session (`analysis.model` and
  `custom_components.solar_sanity.analysis.model`); compare `.value` across that seam.
- Entity ids, translation keys and attribute names are public: automations and the card bind to
  them. A removed one goes under `### Removed`, and a retired entity is added to
  `RETIRED_ENTITIES` in `__init__.py` so setup takes it out of the registry.
- Entries made before 0.30.0 hold six keys nothing reads (`has_battery`, `grid_is_net`,
  `load_whole_house` in data; `suppressed_codes`, `corrections`, `guaranteed_annual_kwh` in
  options). They are kept for a lossless rollback; do not reuse those names.
- A checkout has no card until `npm run build`; its absence is logged at debug only, and
  `npm test` is green without a bundle because the smoke test skips. Build, then test, then
  check_size. vitest does not typecheck, so `node:*` imports pass it and fail tsc.
- hacs.json declares Home Assistant 2025.11.0, which is the real floor: `unit_class` is not a
  statistics-metadata column before it, and `OptionsFlowWithReload` needs 2025.8. CI runs the suite
  at that floor and at 2026.2.3. `tests/integration/test_collection_guard.py` reads the declared
  number from hacs.json, so moving the floor moves the assertion; the `tests-floor` pin in
  validate.yml must move with it.
- `filterwarnings` in pyproject.toml matches no module and enforces nothing.
- A key's prefix says which store it lives in: `CONF_` is what the wizard gathered and lives in
  `entry.data`, `OPT_` is a setting changed afterwards and lives in `entry.options`. Reading one
  from the other returns `None`, which is indistinguishable from unset.
  `tests/test_option_stores.py` walks the source for it.
- Changing the stored schema: docs/ARCHITECTURE.md section 5.5. Known risks: section 8.
