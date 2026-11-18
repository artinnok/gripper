import logging
import stat
import sys

import click
import pytest
from textual.widgets import Static

from galloc.main import _configure_logging, cli
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


@pytest.mark.parametrize("mode", ["default", "flag", "config"])
def test_tui_diagnostics_stay_in_file_during_navigation(
    runner, monkeypatch, config_path, write_config, runpod_http, pod_data, mode
):
    """Launch through Click and keep threaded HTTP logs out of Textual on every screen."""

    write_config(defaults={"debug": mode == "config"})
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)
    log_path = config_path.with_name("debug.log")
    logger = logging.getLogger("galloc")
    original_handlers = logger.handlers[:]
    original_level, original_propagate = logger.level, logger.propagate

    async def scenario(pilot):
        """Refresh and navigate while checking the header and the file receiving requests."""

        header = pilot.app.screen.query_one("#header", Static).content
        assert (str(log_path) in header) == (mode != "default")
        await pilot.press("a")
        await settle(pilot)
        await pilot.press("r")
        await settle(pilot)
        if mode != "default":
            text = log_path.read_text()
            assert "REQUEST GET https://runpod.test/v2/catalog/gpus" in text
            assert "RESPONSE GET" in text and '"gpus": [' in text
            assert "test-token" not in text
            assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
        else:
            assert not log_path.exists()
        await pilot.press("escape")

    def run_headless(app):
        """Exercise the real TUI lifecycle without requiring a display in the test runner."""

        run_app(app, scenario)

    monkeypatch.setattr("galloc.main.GallocApp.run", run_headless)
    with runner.isolation() as (_, stderr, output):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        cli.main(args=["--debug"] if mode == "flag" else [], standalone_mode=False)
        stderr_text, output_text = stderr.getvalue().decode(), output.getvalue().decode()

    assert "REQUEST" not in output_text and "RESPONSE" not in output_text
    assert (f"Debug log: {log_path}" in stderr_text) == (mode != "default")
    assert logger.handlers == original_handlers
    assert (logger.level, logger.propagate) == (original_level, original_propagate)


def test_noninteractive_debug_help_creates_no_file(runner, config_path):
    """Keep a piped help invocation free of diagnostic files and TUI startup."""

    result = runner.invoke(cli, ["--debug"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert not config_path.with_name("debug.log").exists()


@pytest.mark.parametrize("failure", [OSError("Failed operation"), KeyboardInterrupt()])
def test_file_logging_closes_on_errors_and_interrupts(tmp_path, failure, capsys):
    """Close the log file and restore existing logger state even when the command is interrupted."""

    log_path = tmp_path / "debug.log"
    logger = logging.getLogger("galloc")
    original_handlers = logger.handlers[:]
    original_level, original_propagate = logger.level, logger.propagate

    with pytest.raises(type(failure)), click.Context(cli) as context:
        context.with_resource(_configure_logging(True, log_path=log_path))
        (handler,) = [handler for handler in logger.handlers if handler not in original_handlers]
        logger.debug("Before interruption")
        raise failure

    assert handler.stream is None
    assert logger.handlers == original_handlers
    assert (logger.level, logger.propagate) == (original_level, original_propagate)
    assert "Before interruption" in log_path.read_text()
    assert capsys.readouterr().err == ""
