# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Solar Sanity is a Home Assistant custom integration (`custom_components/solar_sanity`) with a
Lit card (`frontend/src`): a battery outlook projected from the house's own history and a solar
forecast archive. The architecture, the outlook, the persistent store and the ranked known risks
are in `docs/ARCHITECTURE.md`; keep it current when structure changes.
This file holds only what the code does not tell you.

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
skip, silently. At 0.30.0 that is 232 tests locally, plus 139 card tests; CI adds the integration tier. Say in the PR when the HA tier did not run locally. The `Unknown config option:
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
  rather than anything a projection saw.

Traps whose error message points elsewhere:

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
