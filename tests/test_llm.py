import pytest

from app.core.llm import extract_json


def test_extract_json_from_fenced_block():
    text = '```json\n{"a": 1}\n```'
    assert extract_json(text) == {"a": 1}


def test_extract_json_from_plain():
    assert extract_json('{"a": 2}') == {"a": 2}


def test_extract_json_from_trailing_text():
    assert extract_json('结果如下：{"a": 3} 完') == {"a": 3}


def test_extract_json_raises_for_garbage():
    with pytest.raises(ValueError):
        extract_json("nothing here")