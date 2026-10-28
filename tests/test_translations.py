import json
import re
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "myq"
TRANSLATIONS = sorted((INTEGRATION / "translations").glob("*.json"))
PLACEHOLDER = re.compile(r"\{\w+\}")
CODE_SPAN = re.compile(r"`[^`]*`")


def _flatten(node: object, prefix: str = "") -> dict[str, str]:
    if isinstance(node, str):
        return {prefix: node}
    assert isinstance(node, dict), prefix
    flat: dict[str, str] = {}
    for key, value in node.items():
        flat.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    return flat


def _load(path: Path) -> dict[str, str]:
    return _flatten(json.loads(path.read_text(encoding="utf-8")))


STRINGS = _load(INTEGRATION / "strings.json")


def test_english_translation_matches_strings() -> None:
    assert _load(INTEGRATION / "translations" / "en.json") == STRINGS


@pytest.mark.parametrize("path", TRANSLATIONS, ids=[path.stem for path in TRANSLATIONS])
def test_translation_has_every_string_with_its_placeholders(path: Path) -> None:
    translation = _load(path)

    assert translation.keys() == STRINGS.keys()
    for key, text in translation.items():
        assert text.strip(), key
        # Home Assistant falls back to English when placeholders differ, and code spans
        # quote literal values such as the browser console message.
        assert sorted(PLACEHOLDER.findall(text)) == sorted(PLACEHOLDER.findall(STRINGS[key])), key
        assert CODE_SPAN.findall(text) == CODE_SPAN.findall(STRINGS[key]), key
