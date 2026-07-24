import pytest

from saidkick.profiles import ProfileStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SAIDKICK_HOME", str(tmp_path))
    return ProfileStore()


def test_root_honours_saidkick_home(store, tmp_path):
    assert str(tmp_path) in str(store.path("x"))
    assert store.path("x").name == "x"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", ".", "with space", "dots.."])
def test_invalid_names_rejected(store, bad):
    with pytest.raises(ValueError):
        store.path(bad)


@pytest.mark.parametrize("ok", ["github", "alex-personal", "client_acme", "test1"])
def test_valid_names_accepted(store, ok):
    assert store.path(ok).name == ok


def test_save_and_load_state_round_trip(store):
    state = {"cookies": [{"name": "sid", "value": "abc"}], "origins": []}
    store.save_state("github", state)
    assert store.load_state("github") == state


def test_load_unknown_profile_is_none(store):
    assert store.load_state("nope") is None


def test_exists_reflects_a_save(store):
    assert not store.exists("github")
    store.save_state("github", {"cookies": [], "origins": []})
    assert store.exists("github")


def test_list_reports_has_state(store):
    store.save_state("github", {"cookies": [{"name": "x", "value": "1"}], "origins": []})
    entries = {e["name"]: e for e in store.list()}
    assert entries["github"]["has_state"] is True
    assert entries["github"]["has_userdata"] is False


def test_list_is_sorted(store):
    for name in ("zeta", "alpha", "mid"):
        store.save_state(name, {"cookies": [], "origins": []})
    assert [e["name"] for e in store.list()] == ["alpha", "mid", "zeta"]


def test_delete_removes_the_profile(store):
    store.save_state("github", {"cookies": [], "origins": []})
    store.delete("github")
    assert not store.exists("github")
    assert store.load_state("github") is None


def test_userdata_path_is_under_the_profile(store):
    assert store.userdata("github").parent == store.path("github")


def test_save_leaves_no_temp_file_behind(store):
    store.save_state("github", {"cookies": [], "origins": []})
    assert list(store.path("github").glob("*.tmp")) == []


def test_a_failed_save_leaves_the_previous_state_readable(store, monkeypatch):
    """An interrupted write must not corrupt a working profile."""
    good = {"cookies": [{"name": "sid", "value": "keepme"}], "origins": []}
    store.save_state("github", good)

    import json as _json

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(_json, "dumps", boom)
    with pytest.raises(OSError):
        store.save_state("github", {"cookies": [], "origins": []})

    monkeypatch.undo()
    assert store.load_state("github") == good
