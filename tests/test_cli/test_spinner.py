from io import StringIO

import pytest

from galloc.terminal.wait import startup_spinner
from tests.test_cli.conftest import TerminalInput


class TerminalOutput(StringIO):
    """Capture the actual terminal rendering without a real terminal."""

    def isatty(self) -> bool:
        """Enable Rich's terminal rendering for this output stream."""

        return True


@pytest.mark.parametrize("interrupted", [False, True])
def test_spinner_restores_terminal(monkeypatch, interrupted):
    """Animate on stderr and restore the cursor after success or Ctrl+C."""

    stdout = StringIO()
    stderr = TerminalOutput()
    monkeypatch.setattr("sys.stdin", TerminalInput())
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)
    monkeypatch.setenv("TERM", "xterm-256color")

    def wait():
        """Exercise the spinner with a successful or interrupted operation."""

        with startup_spinner("gpu-1  Preparing…"):
            if interrupted:
                raise KeyboardInterrupt

    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            wait()
    else:
        wait()

    assert stdout.getvalue() == ""
    assert "gpu-1  Preparing…" in stderr.getvalue()
    assert "Ctrl+C to stop waiting" in stderr.getvalue()
    assert "\x1b[?25l" in stderr.getvalue()
    assert "\x1b[?25h" in stderr.getvalue()
