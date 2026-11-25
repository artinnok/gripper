import logging
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import click
from pydantic import ValidationError

from gripper.cli.add import add_command
from gripper.cli.delete import delete_command
from gripper.cli.extend import extend_command
from gripper.cli.get import get_command
from gripper.cli.list import list_command
from gripper.cli.providers import providers_command
from gripper.cli.regions import regions_command
from gripper.cli.ssh_key.main import ssh_key_command
from gripper.config import CONFIG_PATH
from gripper.exceptions import GripperError
from gripper.settings import Settings, load_settings
from gripper.tui.app import GripperApp


class CLIGroup(click.Group):
    """Present expected domain, provider, and storage failures consistently across commands."""

    def invoke(self, context: click.Context):
        """Translate expected failures into stderr messages and a nonzero exit code."""

        try:
            return super().invoke(context)
        except (GripperError, sqlite3.Error, OSError) as exception:
            logging.getLogger("gripper").debug("Command failed", exc_info=True)
            raise click.ClickException(str(exception)) from None


@click.group(cls=CLIGroup, invoke_without_command=True)
@click.option(
    "--debug", is_flag=True, default=None, help="Log HTTP requests and responses: TUI to debug.log, CLI to stderr."
)
@click.pass_context
def cli(context: click.Context, debug: bool | None) -> None:
    """Manage GPU instances with a time to live (TTL).

    Run without a command in a terminal to open the interactive interface.
    """

    settings = _load_settings(debug)
    context.obj = settings

    if context.invoked_subcommand is None:
        _open_tui(context, settings)
    else:
        context.with_resource(_configure_logging(settings.debug))


def _load_settings(debug: bool | None) -> Settings:
    """Load settings for the CLI and apply an explicitly supplied debug flag."""

    try:
        settings = load_settings(CONFIG_PATH)
    except ValidationError as exception:
        field_list = [".".join(str(part) for part in error["loc"]) for error in exception.errors()]
        raise click.ClickException(f"Invalid settings: {', '.join(field_list)}. Check {CONFIG_PATH}.") from None

    if debug is not None:
        settings.debug = debug
    return settings


@contextmanager
def _configure_logging(debug: bool, *, log_path: Path | None = None) -> Iterator[None]:
    """Route diagnostics away from the TUI and release the handler when this invocation ends."""

    logger = logging.getLogger("gripper")
    previous_level, previous_propagate = logger.level, logger.propagate
    if log_path is None:
        handler = logging.StreamHandler()
    elif debug:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(mode=0o600)
        log_path.chmod(0o600)
        handler = logging.FileHandler(log_path, encoding="utf-8")
    else:
        handler = logging.NullHandler()

    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    logger.propagate = False
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)

    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def _open_tui(context: click.Context, settings: Settings) -> None:
    """Open the terminal interface in an interactive terminal; print help anywhere else."""

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        click.echo(context.get_help())
        return

    log_path = CONFIG_PATH.with_name("debug.log")
    context.with_resource(_configure_logging(settings.debug, log_path=log_path))
    GripperApp(settings, CONFIG_PATH).run()
    if settings.debug:
        click.echo(f"Debug log: {log_path}", err=True)


cli.add_command(add_command)
cli.add_command(list_command)
cli.add_command(get_command)
cli.add_command(extend_command)
cli.add_command(delete_command)
cli.add_command(providers_command)
cli.add_command(regions_command)
cli.add_command(ssh_key_command)
