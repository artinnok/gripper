import logging
import sqlite3
import sys

import click
from pydantic import ValidationError

from galloc.cli.add import add_command
from galloc.cli.delete import delete_command
from galloc.cli.extend import extend_command
from galloc.cli.get import get_command
from galloc.cli.list import list_command
from galloc.cli.providers import providers_command
from galloc.cli.regions import regions_command
from galloc.cli.ssh_key.main import ssh_key_command
from galloc.config import CONFIG_PATH
from galloc.exceptions import GallocError
from galloc.settings import Settings, load_settings
from galloc.tui.app import GallocApp


class CLIGroup(click.Group):
    """Present expected domain, provider, and storage failures consistently across commands."""

    def invoke(self, context: click.Context):
        """Translate expected failures into stderr messages and a nonzero exit code."""

        try:
            return super().invoke(context)
        except (GallocError, sqlite3.Error, OSError) as exception:
            logging.getLogger("galloc").debug("Command failed", exc_info=True)
            raise click.ClickException(str(exception)) from None


@click.group(cls=CLIGroup, invoke_without_command=True)
@click.option("--debug", is_flag=True, default=None, help="Show request diagnostics and tracebacks on stderr.")
@click.pass_context
def cli(context: click.Context, debug: bool | None) -> None:
    """Manage GPU instances with a time to live (TTL).

    Run without a command in a terminal to open the interactive interface.
    """

    settings = _load_settings(debug)
    context.obj = settings
    _configure_logging(context, settings.debug)

    if context.invoked_subcommand is None:
        _open_tui(context, settings)


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


def _configure_logging(context: click.Context, debug: bool) -> None:
    """Keep galloc diagnostics on stderr for the lifetime of this CLI invocation."""

    logger = logging.getLogger("galloc")
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    context.call_on_close(lambda: logger.removeHandler(handler))


def _open_tui(context: click.Context, settings: Settings) -> None:
    """Open the terminal interface in an interactive terminal; print help anywhere else."""

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        click.echo(context.get_help())
        return

    GallocApp(settings, CONFIG_PATH).run()


cli.add_command(add_command)
cli.add_command(list_command)
cli.add_command(get_command)
cli.add_command(extend_command)
cli.add_command(delete_command)
cli.add_command(providers_command)
cli.add_command(regions_command)
cli.add_command(ssh_key_command)
