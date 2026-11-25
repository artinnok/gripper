import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from gripper.core.readiness import wait_for_instance
from gripper.core.schemas import GPU
from gripper.providers.base import Provider
from gripper.providers.schemas import Instance, InstanceStatus
from gripper.settings import Settings


def wait_for_gpu(provider: Provider, gpu: GPU, instance: Instance, settings: Settings) -> Instance:
    """Wait for provider readiness without persisting any instance state."""

    if instance.status == InstanceStatus.RUNNING:
        return instance

    try:
        with startup_spinner(f"{gpu.name}  Preparing…"):
            return asyncio.run(
                wait_for_instance(
                    provider,
                    gpu.provider_instance_id,
                    timeout=settings.startup_timeout,
                    poll_interval=settings.startup_poll_interval,
                )
            )
    except Exception, KeyboardInterrupt:
        click.echo(f"\nInspect  gripper get {gpu.name}\nDelete   gripper rm {gpu.name}", err=True)
        raise


@contextmanager
def startup_spinner(message: str) -> Iterator[None]:
    """Animate one stderr line in terminals and print a plain message in scripts."""

    if not sys.stdin.isatty() or not sys.stderr.isatty():
        click.echo(message, err=True)
        yield
        return

    with Progress(
        SpinnerColumn("arc"),
        TextColumn("{task.description}", markup=False),
        TimeElapsedColumn(),
        TextColumn("(Ctrl+C to stop waiting)", markup=False),
        console=Console(file=sys.stderr),
        transient=True,
    ) as progress:
        progress.add_task(message, total=None)
        yield
