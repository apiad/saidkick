from typer.testing import CliRunner

from saidkick.cli import app

runner = CliRunner()


def test_start_command_is_gone():
    """`saidkick start` belonged to the extension era."""
    assert runner.invoke(app, ["start", "--help"]).exit_code != 0


def test_mirror_command_is_gone():
    assert runner.invoke(app, ["mirror", "--help"]).exit_code != 0


def test_exec_command_is_gone():
    assert runner.invoke(app, ["exec", "--help"]).exit_code != 0


def test_doctor_command_is_gone():
    assert runner.invoke(app, ["doctor", "--help"]).exit_code != 0


def test_serve_command_exists():
    assert runner.invoke(app, ["serve", "--help"]).exit_code == 0


def test_quick_command_exists():
    assert "quick" in runner.invoke(app, ["--help"]).output


def test_contexts_command_exists():
    assert runner.invoke(app, ["contexts", "--help"]).exit_code == 0


def test_ported_verbs_still_exist():
    for verb in ("click", "type", "press", "select", "find", "snapshot",
                 "screenshot", "navigate", "open", "close", "scroll", "highlight"):
        assert runner.invoke(app, [verb, "--help"]).exit_code == 0, verb


def test_click_accepts_the_full_locator_vocabulary():
    out = runner.invoke(app, ["click", "--help"]).output
    for flag in ("--css", "--xpath", "--by-text", "--by-label",
                 "--by-placeholder", "--by-role", "--within-css", "--nth", "--wait-ms"):
        assert flag in out, flag


def test_connect_error_is_reported_cleanly(monkeypatch):
    """No traceback when the daemon is not running.

    Pinned to a dead port via SAIDKICK_URL so the result does not depend on
    whether something happens to be listening on the default one.
    """
    monkeypatch.setenv("SAIDKICK_URL", "http://127.0.0.1:1")
    result = runner.invoke(app, ["contexts"])
    assert result.exit_code == 1
    assert "saidkick is not running" in result.output


def test_base_url_comes_from_the_environment(monkeypatch):
    from saidkick.client import SaidkickClient

    monkeypatch.setenv("SAIDKICK_URL", "http://elsewhere:9999")
    assert SaidkickClient().base_url == "http://elsewhere:9999"
    assert SaidkickClient("http://explicit:1234").base_url == "http://explicit:1234"
