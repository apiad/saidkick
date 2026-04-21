import pytest
from saidkick.server import parse_tab_id


def test_parse_tab_id_valid():
    assert parse_tab_id("br-a1b2:42") == ("br-a1b2", 42)
    assert parse_tab_id("br-0000:1") == ("br-0000", 1)
    assert parse_tab_id("br-ffff:999999") == ("br-ffff", 999999)


@pytest.mark.parametrize("bad", [
    "",
    "br-a1b2",
    "br-a1b2:",
    "br-a1b2:abc",
    "br-XYZ1:42",         # non-hex chars
    "br-a1b:42",          # too few hex chars
    "br-a1b2c:42",        # too many hex chars
    "a1b2:42",            # missing br- prefix
    "br-a1b2:42:extra",   # extra segment
    "br-a1b2:-1",         # negative
])
def test_parse_tab_id_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_tab_id(bad)
