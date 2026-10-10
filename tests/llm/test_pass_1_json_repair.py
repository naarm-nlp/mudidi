"""Pass 1 guide replies often carry MDF markers with a single backslash."""

import json

import pytest

from mudidi.llm.pass_1 import _extract_json_object


def test_unescaped_mdf_markers_in_the_guide_reply_are_repaired() -> None:
    reply = r'{"rules": ["Use \lx for headwords and \ge for glosses"]}'

    assert _extract_json_object(reply) == {
        "rules": ["Use \\lx for headwords and \\ge for glosses"]
    }


def test_valid_escapes_are_left_alone() -> None:
    reply = r'{"rules": ["say \"hi\"", "already \\se", "a\nb", "bad \ps"]}'

    assert _extract_json_object(reply) == {
        "rules": ['say "hi"', "already \\se", "a\nb", "bad \\ps"]
    }


def test_reply_that_is_still_not_json_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        _extract_json_object('{"rules": [unquoted]}')
