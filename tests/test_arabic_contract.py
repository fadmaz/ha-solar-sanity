"""Arabic covers every configured label without changing public translation keys."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components/solar_sanity"


def test_arabic_has_the_same_keys_and_placeholders_as_english():
    """Missing placeholders break configuration flows, not just display copy."""
    english = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
    arabic = json.loads((ROOT / "translations/ar.json").read_text(encoding="utf-8"))

    def compare(left, right):
        if isinstance(left, dict):
            assert left.keys() == right.keys()
            for key in left:
                compare(left[key], right[key])
        else:
            assert sorted(re.findall(r"\{\w+\}", left)) == sorted(re.findall(r"\{\w+\}", right))
            assert right

    compare(english, arabic)
