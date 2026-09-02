# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Solar Sanity is a Home Assistant custom integration (`custom_components/solar_sanity`) with a
Lit card (`frontend/src`). The architecture, the persistent store, the analysis pipeline and
the ranked known risks are in `docs/ARCHITECTURE.md`; keep it current when structure changes.
This file holds only what the code does not tell you.

## Commands

```bash
python scripts/check.py                                    # every gate in CI order: ruff, tsc, pytest, build, vitest, size budget; stops at the first failure; --slow adds the corpus
python -m pytest tests -q                                  # what CI runs; the 3,000-case corpus is deselected by addopts (about 70 s)
python -m pytest tests/analysis/test_detection.py -q       # one file
python -m pytest "tests/analysis/test_invariants.py::TestPurity::test_no_homeassistant_imports" -q   # one node id
python -m pytest tests -q -m slow -n auto                  # the full clean corpus; needs pytest-xdist; run before changing any threshold constant
ruff check . && ruff format --check .                      # the only Python gates; no mypy, no pre-commit
pip install -r requirements-dev.txt                        # Python 3.13 or newer only; installs Home Assistant and unlocks tests/integration
npm ci && npm run lint && npm test                         # tsc --noEmit, then vitest
npx vitest run frontend/src/chart.test.ts -t "substring"   # one card file, one test
npm run build                                              # writes the git-ignored bundle into custom_components/solar_sanity/frontend/
python scripts/check_size.py                               # gzip budget for that bundle; from the repo root, after a build
npm run start                                              # rebuild on change and serve the bundle on :4000; nothing documents how a dev HA consumes it
python scripts/make_brand_assets.py                        # regenerates the tracked brand PNGs; needs Pillow, which no requirements file lists
```

Replay a real installation from its diagnostics download (no CLI exists, and no such file is in the repo):

```bash
python -c "import sys; sys.path[:0] = ['.', 'custom_components/solar_sanity']; from tests.synth import replay; from analysis.engine import analyse; print(analyse(replay.request_from(replay.load('diagnostics.json'))))"
```

Without Home Assistant installed, `tests/integration` is not collected and eight top-level test
modules skip, silently. At v0.25.1 that is 1,791 tests locally against 2,028 on CI, plus 130 card
tests. Say in the PR when the HA tier did not run locally. The `Unknown config option:
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
  branch, written for the user, under Added, Changed, Fixed, Removed, Notes or Internal. No
  Unreleased section, no release commit, and never bump manifest.json: release.yml stamps the tag
  into it and builds the card afterwards, so the committed version lags the tags on purpose.
- After merging: `gh release create vX.Y.Z --target main --title "X.Y.Z — <sentence>"` with the
  changelog section as the body. feat gives a minor version, fix a patch; test and docs PRs get no
  release. Re-run release.yml from workflow_dispatch with the tag to retry a failed upload.
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
- The 3,000-case corpus runs on every push and on Mondays, never on the pull_request event. The
  comments in pyproject.toml and test_clean_corpus.py saying it runs on main only are wrong. A
  healthy house must reach `ok`; `investigating` is not silence.
- hassfest and the HACS action float on `@master` and `@main`, and ruff is unpinned, so a red
  Monday run with no commits is upstream.

Tests that grep source text, so the failure names nothing near the edit:

- forecast-card.ts must say "currency" in a comment and nowhere else. main.ts must carry the GitHub
  URL in code. sensor.py, binary_sensor.py and config_flow.py must write `translation_key="literal"`;
  a constant silently drops out of the join. repairs.py needs a literal
  `translation_placeholders={...}` dict. services.yaml keys are matched by `^[a-z_]+:`.
- Currency words (price, cost, bill, saving, tariff, currency, cent, their plurals, and `$` next to
  a digit) fail in analysis/ strings, in .ts with comments stripped, and in strings.json,
  translations/ and services.yaml with nothing stripped: a YAML comment saying "cost" fails.
- analysis/ is AST-policed from tests/analysis/test_invariants.py: single-dot relative imports
  only; a stdlib allowlist of dataclasses, datetime, enum, math, typing, collections, __future__,
  abc, functools and itertools (so `re`, `json` and `statistics` fail); no random, time, secrets or
  uuid; `x or 0` and `x or False` banned. ruff's PLC0415 also applies inside analysis/, unlike the
  top-level component files.
- KNOWN_UNRAISED in that file is edited in both directions: a fault code without a producer must be
  listed, and wiring a producer must delist it. A new code also needs `faults._TEMPLATES` and the
  hand-declared field sets in tests/analysis/test_templates.py; `faults.render` raises on a missing
  field, and inside HA that takes every entity unavailable.
- Adding an entity, service or repairs issue: strings.json and translations/en.json stay identical;
  a repairs key carries `description` or `fix_flow`, never both; unfixable templates need
  `{detail}` and `{source_fix}`; a service goes in services.yaml and both string files.

Traps whose error message points elsewhere:

- In tests/integration request `recorder_mock` before `hass`, request `enable_custom_integrations`
  yourself, and call `entry.add_to_hass(hass)`. The wrong fixture order fails as a bare
  `assert not [True]`.
- Setting up one config entry sets up every configured entry of the domain. An entry meant to be
  down needs `disabled_by=ConfigEntryDisabler.USER`.
- Every analysis enum exists twice in one pytest session (`analysis.model` and
  `custom_components.solar_sanity.analysis.model`); compare `.value` across that seam.
- Notes name channels by role, never `friendly_name`: diagnostics lack it and the replay asserts
  notes are identical. Finding text may use it.
- Fault code strings are public, as the `finding_code` attribute and the
  `solar_sanity_finding_raised` event; a removed code goes under `### Removed`.
- A checkout has no card until `npm run build`; its absence is logged at debug only, and
  `npm test` is green without a bundle because the smoke test skips. Build, then test, then
  check_size. vitest does not typecheck, so `node:*` imports pass it and fail tsc.
- hacs.json declares Home Assistant 2025.1.0; the code needs 2025.11, and only 2026.2.3 is ever
  tested.
- `filterwarnings` in pyproject.toml matches no module and enforces nothing.
- Changing the stored schema: docs/ARCHITECTURE.md section 5.5. Known bugs, including the yield
  guarantee written to `entry.options` but read from `entry.data`: section 8.
