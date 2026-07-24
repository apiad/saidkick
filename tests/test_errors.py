import pytest

from saidkick import errors as E


@pytest.mark.parametrize(
    "cls,status,code",
    [
        (E.ProfileLocked, 409, "ProfileLocked"),
        (E.NoSuchContext, 404, "NoSuchContext"),
        (E.NoSuchTab, 404, "NoSuchTab"),
        (E.StaleHandle, 410, "StaleHandle"),
        (E.LocatorNotFound, 404, "LocatorNotFound"),
        (E.LocatorAmbiguous, 400, "LocatorAmbiguous"),
        (E.NavigationFailed, 502, "NavigationFailed"),
        (E.HumanHoldsControl, 409, "HumanHoldsControl"),
        (E.HumanTimeout, 504, "HumanTimeout"),
        (E.DialogBlocked, 409, "DialogBlocked"),
        (E.EngineCrashed, 502, "EngineCrashed"),
        (E.TooManyContexts, 429, "TooManyContexts"),
    ],
)
def test_error_has_status_and_code(cls, status, code):
    exc = cls("boom")
    assert exc.status == status and exc.code == code
    assert isinstance(exc, E.SaidkickError)


def test_ambiguous_carries_candidates():
    exc = E.LocatorAmbiguous("found 3", candidates=[{"tag": "b"}, {"tag": "span"}])
    d = E.http_detail(exc)
    assert d["error"] == "LocatorAmbiguous"
    assert d["detail"] == "found 3"
    assert len(d["candidates"]) == 2


def test_extra_is_never_shared_between_instances():
    E.LocatorAmbiguous("a", candidates=[1])
    b = E.LocatorAmbiguous("b")
    assert E.http_detail(b).get("candidates", []) == []


def test_code_is_derived_from_class_name():
    """code and status must not be able to drift from the class."""
    for cls in E.ALL_ERRORS:
        assert cls.code == cls.__name__
