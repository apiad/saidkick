import pytest
from fastapi import HTTPException
from saidkick.server import _raise_for_extension_error, _validate_http_url


@pytest.mark.parametrize("msg,code", [
    ("element not found", 404),
    ("Element not found", 404),
    ("option not found: foo", 404),
    ("tab not found: 7", 404),
    ("Ambiguous selector: found 3 matches", 400),
    ("Element is not a <select>", 400),
    ("No selector provided", 400),
    ("invalid url: 'ftp://x'", 400),
    ("navigation timeout after 15000ms", 504),
    ("selector not resolved within 3000ms", 504),
    ("Browser response timeout", 504),
    ("some weird chrome error we never saw", 502),
    ("", 502),
])
def test_classifier_maps_messages_to_codes(msg, code):
    with pytest.raises(HTTPException) as exc:
        _raise_for_extension_error(msg)
    assert exc.value.status_code == code
    assert exc.value.detail == msg


@pytest.mark.parametrize("url", [
    "http://example.com/",
    "https://example.com/path?q=1",
    "https://sub.example.com:8080/",
])
def test_validate_http_url_accepts(url):
    _validate_http_url(url)  # no raise


@pytest.mark.parametrize("url", [
    "",
    "example.com",
    "ftp://example.com/",
    "javascript:alert(1)",
    "http://",
    "about:blank",
])
def test_validate_http_url_rejects(url):
    with pytest.raises(HTTPException) as exc:
        _validate_http_url(url)
    assert exc.value.status_code == 400
