import pytest

from saidkick.runlog import NULL_RUNLOG, RunLog, redact_value

PASSWORD = "hunter2-super-secret-password"


def test_null_sink_is_a_no_op():
    """The library path must work with no beaver and no file."""
    assert NULL_RUNLOG.enabled is False
    NULL_RUNLOG.record("click", ctx="ctx_a", ok=True)  # no raise
    assert NULL_RUNLOG.query() == []


def test_records_and_queries(tmp_path):
    rl = RunLog(tmp_path / "runs.db")
    rl.record("click", ctx="ctx_a", tab="ctx_a:1", ok=True, ms=12)
    rl.record("click", ctx="ctx_b", ok=False, error="LocatorNotFound")
    rows = rl.query()
    assert len(rows) == 2
    assert {r["kind"] for r in rows} == {"click"}
    assert any(r.get("error") == "LocatorNotFound" for r in rows)


def test_query_filters_by_context(tmp_path):
    rl = RunLog(tmp_path / "runs.db")
    rl.record("click", ctx="ctx_a", ok=True)
    rl.record("click", ctx="ctx_b", ok=True)
    assert [r["ctx"] for r in rl.query(ctx="ctx_a")] == ["ctx_a"]


def test_records_survive_a_reopen(tmp_path):
    path = tmp_path / "runs.db"
    rl = RunLog(path)
    rl.record("click", ctx="ctx_a", ok=True)
    rl.close()
    assert RunLog(path).count() == 1


def test_redaction_replaces_text_with_length_and_hash(tmp_path):
    rl = RunLog(tmp_path / "runs.db")
    rl.record("type_text", ctx="ctx_a", text=PASSWORD, ok=True)
    entry = rl.query()[0]["text"]
    assert entry["len"] == len(PASSWORD)
    assert len(entry["sha256"]) == 12
    assert PASSWORD not in str(entry)


def test_the_password_is_absent_from_the_bytes_on_disk(tmp_path):
    """The guarantee that matters: a stolen run log yields no credentials."""
    path = tmp_path / "runs.db"
    rl = RunLog(path)
    rl.record("type_text", ctx="ctx_a", text=PASSWORD, ok=True)
    rl.close()

    blob = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert PASSWORD.encode() not in blob, "the run log stored a credential verbatim"


def test_redaction_can_be_disabled_deliberately(tmp_path):
    rl = RunLog(tmp_path / "runs.db", redact=False)
    rl.record("type_text", ctx="ctx_a", text="not-a-secret", ok=True)
    assert rl.query()[0]["text"] == "not-a-secret"


def test_redaction_is_on_by_default():
    """A default of False would turn the log into a credential store."""
    assert RunLog(None).redact is True


def test_same_text_hashes_the_same_and_different_text_differs():
    assert redact_value("abc") == redact_value("abc")
    assert redact_value("abc") != redact_value("abd")


def test_a_broken_sink_never_raises(tmp_path):
    """A logging failure must not take down a browser action."""
    rl = RunLog(tmp_path / "runs.db")
    rl.close()
    # Beaver BLOCKS on a write to a closed handle rather than raising, so this
    # would hang the calling browser action without the closed-guard.
    rl.record("click", ctx="ctx_a", ok=True)
    assert rl.query() == []


@pytest.mark.browser
async def test_actions_are_recorded_with_duration_and_outcome(tmp_path, fixture_url):
    from saidkick import actions as A
    from saidkick.engine import Engine
    from saidkick.locators import Locator
    from saidkick.profiles import ProfileStore

    rl = RunLog(tmp_path / "runs.db")
    engine = Engine(store=ProfileStore(root=tmp_path / "p"), runlog=rl)
    await engine.start()
    try:
        ctx = await engine.open_context()
        tab = await ctx.open_tab(f"{fixture_url}/form.html")
        await A.type_text(tab, Locator(css="#u"), PASSWORD)
        await A.click(tab, Locator(css="#go"))
        with pytest.raises(Exception):
            await A.click(tab, Locator(css="#nope"), timeout_ms=300)
    finally:
        await engine.stop()

    rows = rl.query()
    kinds = [r["kind"] for r in rows]
    assert "type_text" in kinds and "click" in kinds
    assert any(r["ok"] is False and r.get("error") == "LocatorNotFound" for r in rows)
    assert all(isinstance(r["ms"], int) for r in rows)

    typed = next(r for r in rows if r["kind"] == "type_text")
    assert typed["locator"] and "css" in typed["locator"]
    # The password was passed POSITIONALLY; it must still be captured and redacted.
    assert typed["text"]["len"] == len(PASSWORD)
    rl.close()
    blob = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert PASSWORD.encode() not in blob
