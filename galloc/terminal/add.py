import sqlite3
import sys

import click

from galloc.core.schemas import Creation
from galloc.core.validators import prepare_creation
from galloc.providers.base import Provider
from galloc.providers.exceptions import GPUUnavailable
from galloc.terminal.prompts import prompt_creation_options


def prepare_add(
    connection: sqlite3.Connection,
    provider: Provider,
    *,
    provider_name: str,
    gpu: str | None,
    region: str | None,
    ttl: str | None,
    budget: str | None,
) -> Creation:
    """Prepare the add command and return to GPU selection if stock changes."""

    interactive_gpu = gpu is None and sys.stdin.isatty()
    requested_region = region
    while True:
        try:
            gpu, region, ttl = prompt_creation_options(
                provider, provider_name, gpu=gpu, region=region, ttl=ttl, budget=budget
            )
            return prepare_creation(
                connection, provider, provider_name=provider_name, gpu=gpu, region=region, ttl=ttl, budget=budget
            )
        except GPUUnavailable as exception:
            if not interactive_gpu:
                raise
            click.echo(f"\n{exception} Choose another GPU.\n", err=True)
            gpu = None
            region = requested_region
