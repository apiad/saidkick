import pytest
from fastapi import HTTPException
from saidkick.server import Locator, _validate_locator, _validate_required_locator


def _loc(**kw):
    return Locator(**kw)


def test_required_locator_zero_set_raises_400():
    with pytest.raises(HTTPException) as exc:
        _validate_required_locator(_loc())
    assert exc.value.status_code == 400
    assert "No locator" in exc.value.detail


def test_required_locator_two_set_raises_400():
    with pytest.raises(HTTPException) as exc:
        _validate_required_locator(_loc(css=".a", by_text="b"))
    assert exc.value.status_code == 400
    assert "Ambiguous locator options" in exc.value.detail


@pytest.mark.parametrize("kw", [
    {"css": ".a"},
    {"xpath": "//div"},
    {"by_text": "hi"},
    {"by_label": "hi"},
    {"by_placeholder": "hi"},
])
def test_required_locator_exactly_one_passes(kw):
    _validate_required_locator(_loc(**kw))


def test_optional_locator_zero_set_is_fine():
    _validate_locator(_loc())


def test_exact_and_regex_mutex():
    with pytest.raises(HTTPException) as exc:
        _validate_locator(_loc(by_text="x", exact=True, regex=True))
    assert exc.value.status_code == 400
    assert "mutually exclusive" in exc.value.detail
