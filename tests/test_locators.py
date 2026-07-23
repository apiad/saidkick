import pytest

from saidkick import errors as E
from saidkick.locators import Locator, resolve, validate_locator

PRIMARIES = ["css", "xpath", "by_text", "by_label", "by_placeholder", "by_role"]


@pytest.mark.parametrize("field", PRIMARIES)
def test_single_primary_is_valid(field):
    validate_locator(Locator(**{field: "x"}), required=True)


def test_two_primaries_rejected():
    with pytest.raises(E.LocatorAmbiguous):
        validate_locator(Locator(css="a", by_text="b"), required=True)


def test_by_role_plus_by_text_is_the_one_legal_pair():
    """by_role uses by_text as the accessible name, so this pair is not ambiguous."""
    validate_locator(Locator(by_role="button", by_text="Send"), required=True)


def test_exact_and_regex_mutually_exclusive():
    with pytest.raises(E.LocatorAmbiguous):
        validate_locator(Locator(by_text="a", exact=True, regex=True), required=True)


def test_no_locator_when_required():
    with pytest.raises(E.LocatorNotFound):
        validate_locator(Locator(), required=True)


def test_no_locator_allowed_when_optional():
    validate_locator(Locator(), required=False)


def test_describe_is_stable_and_mentions_the_field():
    assert "by_text" in Locator(by_text="Send").describe()
    assert "Send" in Locator(by_text="Send").describe()


@pytest.mark.browser
@pytest.mark.parametrize(
    "loc,expected_id",
    [
        (Locator(css="#go"), "go"),
        (Locator(by_label="Username"), "u"),
        (Locator(by_placeholder="your name"), "u"),
        (Locator(by_role="button", by_text="Send"), "go"),
        (Locator(xpath="//button[@id='go']"), "go"),
    ],
)
async def test_resolve_finds_element(tab, loc, expected_id):
    assert await resolve(tab.page, loc).get_attribute("id") == expected_id


@pytest.mark.browser
async def test_by_text_returns_leafmost(tab):
    # #nested contains <span>Outer <b>Send</b></span>; the <b> is leaf-most.
    el = resolve(tab.page, Locator(by_text="Send", within_css="#nested"))
    assert (await el.evaluate("e => e.tagName")) == "B"


@pytest.mark.browser
async def test_within_css_scopes(tab):
    el = resolve(tab.page, Locator(by_text="Send", within_css="#f"))
    assert await el.get_attribute("id") == "go"


@pytest.mark.browser
async def test_nth_disambiguates(tab):
    el = resolve(tab.page, Locator(css="label", nth=1))
    assert (await el.text_content()) == "Country"


@pytest.mark.browser
async def test_regex_matching(tab):
    el = resolve(tab.page, Locator(by_text="^Se", regex=True, within_css="#f"))
    assert await el.get_attribute("id") == "go"
