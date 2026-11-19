import json
from decimal import Decimal
from pathlib import Path

import click

from galloc.core.pricing import calculate_cost
from galloc.core.schemas import GPU, Creation
from galloc.core.ssh_keys import format_ssh_command
from galloc.core.ttl import format_ttl
from galloc.providers.schemas import Instance


NAME_COLUMN_WIDTH = 12
PROVIDER_COLUMN_WIDTH = 10
STATUS_COLUMN_WIDTH = 12


def print_creation_estimate(creation: Creation, budget: str | None) -> None:
    """Show the in-memory cost estimate before a billable request."""

    cost = calculate_cost(creation.duration, creation.hourly_price)
    estimate = f"Compute  ~${cost:.2f} · ${creation.hourly_price:.2f}/h · {format_ttl(creation.duration)}"
    if budget is not None:
        estimate += f" · budget ${Decimal(budget):f}"
    click.echo(f"\n{estimate}", err=True)
    click.echo("Estimate only; disk/tax extra. Auto-delete requires an open galloc TUI; no spending cap.", err=True)


def print_created_gpu(gpu: GPU, output: str, instance: Instance, public_key_path: Path | None) -> None:
    """Show a compact ready result and the commands needed to use and remove it."""

    if output == "json":
        print_gpu(gpu, output, instance, public_key_path)
    else:
        click.echo()
        click.echo(f"{'NAME':12}  {'STATUS':10}  {'REGION':12}  TTL ENDS (UTC)")
        click.echo(f"{gpu.name:12}  {instance.status:10}  {instance.region or '-':12}  {gpu.expires_at:%Y-%m-%d %H:%M}")
        click.echo(f"\nSSH     {format_ssh_command(instance.ssh_command, public_key_path)}")
    click.echo(f"Delete  galloc rm {gpu.name}", err=True)


def print_removal(gpu: GPU, output: str) -> None:
    """Confirm removal without returning an instance or keeping a history entry."""

    if output == "json":
        click.echo(json.dumps({"name": gpu.name, "removed": True}))
        return
    click.echo(gpu.name)


def print_gpu(gpu: GPU, output: str, instance: Instance, public_key_path: Path | None) -> None:
    """Combine a local name and deadline with the current provider response."""

    if output == "json":
        click.echo(json.dumps(_gpu_data(gpu, instance), indent=2))
        return

    field_list = [
        ("Name", gpu.name),
        ("Provider", gpu.provider),
        ("Status", instance.status),
        ("Instance ID", gpu.provider_instance_id or "-"),
        ("Expires at", gpu.expires_at.isoformat()),
        ("GPU", instance.gpu or "-"),
        ("Region", instance.region or "-"),
        ("Created at", instance.created_at.isoformat()),
        ("SSH", format_ssh_command(instance.ssh_command, public_key_path) or "-"),
    ]
    for label, value in field_list:
        click.echo(f"{label}: {value}")


def print_gpu_list(result_list: list[tuple[GPU, Instance]], output: str) -> None:
    """Display provider results without presenting a local status snapshot."""

    if output == "json":
        click.echo(json.dumps([_gpu_data(gpu, instance) for gpu, instance in result_list], indent=2))
        return
    if not result_list:
        click.echo("No GPUs.")
        return

    click.echo(
        f"{'NAME':{NAME_COLUMN_WIDTH}}  {'PROVIDER':{PROVIDER_COLUMN_WIDTH}}  "
        f"{'STATUS':{STATUS_COLUMN_WIDTH}}  TTL ENDS (UTC)"
    )
    for gpu, instance in result_list:
        click.echo(
            f"{gpu.name:{NAME_COLUMN_WIDTH}}  {gpu.provider:{PROVIDER_COLUMN_WIDTH}}  "
            f"{instance.status:{STATUS_COLUMN_WIDTH}}  "
            f"{gpu.expires_at:%Y-%m-%d %H:%M}"
        )


def _gpu_data(gpu: GPU, instance: Instance) -> dict:
    """Keep JSON metadata separate from the live instance while exposing one current status."""

    return gpu.model_dump(mode="json") | {
        "status": instance.status,
        "instance": instance.model_dump(mode="json"),
    }
