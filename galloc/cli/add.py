import sqlite3
import sys

import click

from galloc.core import services
from galloc.core.schemas import GPU, Creation
from galloc.core.storage import open_database
from galloc.providers.base import Provider
from galloc.providers.exceptions import CreationRejected
from galloc.providers.factory import PROVIDER_NAME_LIST, open_provider
from galloc.providers.schemas import Instance
from galloc.settings import Settings
from galloc.terminal.add import prepare_add
from galloc.terminal.output import print_created_gpu, print_creation_estimate
from galloc.terminal.wait import wait_for_gpu


@click.command("add")
@click.option("--provider", "provider_name", type=click.Choice(PROVIDER_NAME_LIST), default="runpod", show_default=True)
@click.option("--gpu", help="GPU catalog name or ID. Omit to choose in a terminal; required in scripts.")
@click.option("--region", help="Provider region ID. Omit to choose in a terminal; required in scripts.")
@click.option("--ttl", help="Duration such as 30m, 2h, or 1d. Mutually exclusive with --budget.")
@click.option("--budget", metavar="USD", help="Compute budget, e.g. 5 or 12.50. Estimates TTL; not a spending cap.")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", show_default=True)
@click.pass_obj
def add_command(
    settings: Settings,
    provider_name: str,
    gpu: str | None,
    region: str | None,
    ttl: str | None,
    budget: str | None,
    output: str,
) -> None:
    """Add a billable GPU instance."""

    _validate_options(gpu, region, ttl, budget)

    with open_database(settings.database_path) as connection, open_provider(settings, provider_name) as provider:
        creation = prepare_add(
            connection, provider, provider_name=provider_name, gpu=gpu, region=region, ttl=ttl, budget=budget
        )
        print_creation_estimate(creation, budget)

        created_gpu, instance = _create_gpu(connection, provider, creation)
        instance = wait_for_gpu(provider, created_gpu, instance, settings)

    print_created_gpu(created_gpu, output, instance)


def _validate_options(gpu: str | None, region: str | None, ttl: str | None, budget: str | None) -> None:
    """Check conflicting limits and required script options before accessing dependencies."""

    if ttl is not None and budget is not None:
        raise click.UsageError("Supply exactly one of --ttl or --budget.")
    if sys.stdin.isatty():
        return

    if gpu is None:
        raise click.UsageError("Pass --gpu when running without an interactive terminal.")
    if region is None:
        raise click.UsageError("Pass --region when running without an interactive terminal.")
    if ttl is None and budget is None:
        raise click.UsageError("Pass --ttl or --budget when running without an interactive terminal.")


def _create_gpu(connection: sqlite3.Connection, provider: Provider, creation: Creation) -> tuple[GPU, Instance]:
    """Create the prepared GPU and identify an uncertain result for provider inspection."""

    click.echo("Creating…", err=True)
    try:
        return services.create_gpu(connection, provider, creation)
    except CreationRejected:
        raise
    except BaseException:
        created_gpu = creation.gpu
        if created_gpu.number is not None:
            click.echo(
                f"Creation of {created_gpu.name} is unresolved. "
                f"Check galloc-{created_gpu.id} in the {created_gpu.provider} console.",
                err=True,
            )
        raise
