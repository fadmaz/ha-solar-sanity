"""The maintainer explicitly requires an English-only product."""

from pathlib import Path


def test_only_english_integration_translation_is_shipped():
    """Chat language and Home Assistant user language do not change product scope."""
    translations = (
        Path(__file__).resolve().parents[1] / "custom_components/solar_sanity/translations"
    )
    assert {path.name for path in translations.glob("*.json")} == {"en.json"}
