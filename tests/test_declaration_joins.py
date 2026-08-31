"""Every name declared in code must resolve in the files that translate it.

Home Assistant joins three sets of declarations by string. Code names a
``translation_key``; ``strings.json`` and ``translations/en.json`` are supposed
to define it. Nothing in Python checks that join, and the failure it produces is
silent rather than loud: an unresolvable entity key with ``has_entity_name=True``
falls back to the *device* name, so two sensors quietly become "Solar Sanity"
and "Solar Sanity" and the only symptom is a duplicate in somebody's dashboard.

``hassfest`` checks the services half of this in CI, which means a mismatch is
found about three minutes after it is pushed rather than in the second it takes
to run here. The entity half it does not check at all.

None of this needs Home Assistant, which is why it lives in the pure suite: the
question is only whether two files agree with a third.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "solar_sanity"
STRING_FILES = ("strings.json", "translations/en.json")

#: Which top-level section of the string files a key declared in a given source
#: file has to appear under. An entity key that resolves as a selector is still
#: an unresolvable entity key.
SECTION_FOR = {
    "sensor.py": ("entity", "sensor"),
    "binary_sensor.py": ("entity", "binary_sensor"),
    "config_flow.py": ("selector",),
}

TRANSLATION_KEY = re.compile(r'translation_key\s*=\s*"([^"]+)"')
SERVICE_BLOCK = re.compile(r"^([a-z_]+):", re.MULTILINE)


def _strings(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def _section(data: dict, path: tuple[str, ...]) -> dict:
    for step in path:
        data = data.get(step, {})
    return data


def _declared_keys() -> list[tuple[str, tuple[str, ...], str]]:
    """Every ``translation_key`` in the integration, with where it must resolve."""
    found = []
    for source, path in SECTION_FOR.items():
        text = (COMPONENT / source).read_text(encoding="utf-8")
        for key in sorted(set(TRANSLATION_KEY.findall(text))):
            found.append((source, path, key))
    return found


def test_there_is_something_to_check() -> None:
    """A regex that silently matches nothing would make every test below pass."""
    declared = _declared_keys()

    assert len(declared) >= 6, declared
    assert {key for _, _, key in declared} >= {"status", "data_healthy"}


@pytest.mark.parametrize(
    ("source", "path", "key"),
    _declared_keys(),
    ids=lambda value: value if isinstance(value, str) else "",
)
@pytest.mark.parametrize("strings", STRING_FILES)
def test_every_translation_key_resolves(
    strings: str, source: str, path: tuple[str, ...], key: str
) -> None:
    section = _section(_strings(strings), path)

    assert key in section, (
        f"{source} declares translation_key={key!r} but {strings} has no "
        f"{'.'.join(path)}.{key} — an entity whose key does not resolve falls "
        f"back to the device name and collides with its siblings"
    )


@pytest.mark.parametrize("path", sorted({path for _, path, _ in _declared_keys()}))
def test_the_two_string_files_declare_the_same_keys(path: tuple[str, ...]) -> None:
    """Drift between them is invisible until somebody switches language.

    ``strings.json`` is what Home Assistant validates and
    ``translations/en.json`` is what an English user actually reads. A key added
    to one and not the other resolves in development and not in the product.
    """
    sections = [set(_section(_strings(name), path)) for name in STRING_FILES]

    assert sections[0] == sections[1], (
        f"{'.'.join(path)} differs between the two: "
        f"only in {STRING_FILES[0]}: {sorted(sections[0] - sections[1])}, "
        f"only in {STRING_FILES[1]}: {sorted(sections[1] - sections[0])}"
    )


def test_every_service_is_declared_in_all_three_places() -> None:
    """The join hassfest fails the build over, checked in a second instead.

    A service in ``services.yaml`` with no matching entry in the string files is
    a build failure rather than a runtime one, so this costs nothing to get
    right — but only if something says so before the push.
    """
    in_yaml = set(SERVICE_BLOCK.findall((COMPONENT / "services.yaml").read_text(encoding="utf-8")))
    assert in_yaml, "no services found — the block regex has stopped matching"

    for name in STRING_FILES:
        declared = set(_strings(name).get("services", {}))

        assert declared == in_yaml, (
            f"services.yaml and {name} disagree: "
            f"only in services.yaml: {sorted(in_yaml - declared)}, "
            f"only in {name}: {sorted(declared - in_yaml)}"
        )
