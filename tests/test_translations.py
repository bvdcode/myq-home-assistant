import json
import re
from pathlib import Path
from string import Formatter

import pytest

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "myq"
TRANSLATIONS = sorted((INTEGRATION / "translations").glob("*.json"))
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


def _placeholder_names(text: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(text) if name is not None}


STRINGS = _load(INTEGRATION / "strings.json")


def test_english_translation_matches_strings() -> None:
    assert _load(INTEGRATION / "translations" / "en.json") == STRINGS


@pytest.mark.parametrize("path", TRANSLATIONS, ids=[path.stem for path in TRANSLATIONS])
def test_translation_has_every_string_with_its_placeholders(path: Path) -> None:
    translation = _load(path)

    assert translation.keys() == STRINGS.keys()
    for key, text in translation.items():
        assert text.strip(), key
        assert _placeholder_names(text) == _placeholder_names(STRINGS[key]), key
        assert CODE_SPAN.findall(text) == CODE_SPAN.findall(STRINGS[key]), key
