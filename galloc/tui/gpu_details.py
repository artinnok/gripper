from datetime import datetime, timedelta
from pathlib import Path

from textual.markup import escape

from galloc.core.schemas import GPU
from galloc.core.ssh_keys import format_ssh_command
from galloc.providers.schemas import Instance, InstanceStatus
from galloc.tui.theme import AMBER_THEME, MUTED_COLOR


LOW_TIME_LEFT = timedelta(minutes=10)
STATUS_COLOR_MAP = {
    InstanceStatus.PROVISIONING: AMBER_THEME.warning,
    InstanceStatus.RUNNING: AMBER_THEME.success,
    InstanceStatus.TERMINATING: AMBER_THEME.warning,
    InstanceStatus.STOPPED: MUTED_COLOR,
    InstanceStatus.ERROR: AMBER_THEME.error,
}


def format_gpu(gpu: GPU, instance: Instance, public_key_path: Path | None) -> str:
    """Show a selected GPU's hardware, price, deadline, and connection in the detail column."""

    ssh_command = format_ssh_command(instance.ssh_command, public_key_path)
    return (
        f"[b $primary]{gpu.name}[/]\n"
        f"{escape(instance.gpu or '-')}\n"
        f"{escape(instance.region or '-')}\n"
        f"{format_status(instance)}\n\n"
        f"[$muted]price[/]\n${instance.hourly_price:.2f}/hour\n\n"
        f"[$muted]expires[/]\n{gpu.expires_at:%Y-%m-%d %H:%M} UTC\n\n"
        f"[$muted]ssh[/]\n{escape(ssh_command or '-')}\n\n"
        f"[$muted]instance[/]\n{escape(instance.id)}"
    )


def format_status(instance: Instance) -> str:
    """Color the provider status the same way in the table and in the detail column."""

    return f"[{STATUS_COLOR_MAP[instance.status]}]{instance.status}[/]"


def format_time_left(gpu: GPU, now: datetime) -> str:
    """Show the remaining TTL: seconds near the deadline, coarser units further away."""

    time_left = gpu.expires_at - now
    if time_left <= timedelta(0):
        return f"[{AMBER_THEME.warning}]expired[/]"

    minutes, seconds = divmod(int(time_left.total_seconds()), 60)
    if time_left < LOW_TIME_LEFT:
        return f"[{AMBER_THEME.warning}]{minutes}m {seconds:02d}s[/]"

    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
