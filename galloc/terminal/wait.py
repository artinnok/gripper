import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import click
from prompt_toolkit.output import create_output
from prompt_toolkit.shortcuts import ProgressBar
from prompt_toolkit.shortcuts.progress_bar.formatters import SpinningWheel, Text, TimeElapsed

from galloc.core.readiness import wait_for_instance
from galloc.core.schemas import GPU
from galloc.providers.base import Provider
from galloc.providers.schemas import Instance, InstanceStatus
from galloc.settings import Settings


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
        click.echo(f"\nInspect  galloc get {gpu.name}\nDelete   galloc rm {gpu.name}", err=True)
        raise


@contextmanager
def startup_spinner(message: str) -> Iterator[None]:
    """Animate one stderr line in terminals and print a plain message in scripts."""

    if not sys.stdin.isatty() or not sys.stderr.isatty():
        click.echo(message, err=True)
        yield
        return

    wheel = SpinningWheel()
    wheel.characters = "◜◠◝◞◡◟"
    with ProgressBar(
        formatters=[wheel, Text(f" {message}  "), TimeElapsed(), Text("  (Ctrl+C to stop waiting)")],
        output=create_output(stdout=sys.stderr),
    ) as progress:
        counter = progress(total=None, remove_when_done=True)
        try:
            yield
        finally:
            counter.done = True
