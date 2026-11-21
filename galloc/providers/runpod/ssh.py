import argparse
import shlex

from galloc.api_clients.runpod.schemas import Pod
from galloc.providers.ssh import SSHConnection


def get_ready_connection(pod: Pod) -> SSHConnection | None:
    """Prefer direct SSH once the container has reported its GPU and uptime."""

    if pod.runtime is None or not pod.runtime.gpus or pod.runtime.uptime is None:
        return None
    for endpoint in (pod.ssh.direct, pod.ssh.proxy):
        if endpoint is None:
            continue
        connection = parse_ssh_command(endpoint.command)
        if connection is not None:
            return connection
    return None


def parse_ssh_command(command: str) -> SSHConnection | None:
    """Read the connection options in Runpod's direct and proxy commands."""

    parser = argparse.ArgumentParser(prog="ssh", add_help=False, exit_on_error=False)
    parser.add_argument("destination")
    parser.add_argument("-p", dest="port", type=int)
    parser.add_argument("-i", dest="identity_list", action="append", default=[])
    parser.add_argument("-t", dest="tty_count", action="count", default=0)

    try:
        executable, *argument_list = shlex.split(command)
        if executable != "ssh":
            return None
        options = parser.parse_args(argument_list)
        return SSHConnection(
            destination=options.destination,
            port=options.port,
            identity=options.identity_list[0] if options.identity_list else None,
            tty_count=options.tty_count,
        )
    except argparse.ArgumentError, ValueError:
        return None
