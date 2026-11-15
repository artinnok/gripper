from time import monotonic, sleep

from galloc.core.exceptions import InstanceNotReady
from galloc.providers.base import Provider
from galloc.providers.schemas import Instance, InstanceStatus


def wait_for_instance(provider: Provider, instance_id: str, *, timeout: float, poll_interval: float) -> Instance:
    """Poll a created instance until ready, failed, removed, or timed out."""

    deadline = monotonic() + timeout
    while True:
        instance = provider.get(instance_id)
        if instance is None:
            raise InstanceNotReady("The instance disappeared while waiting for startup.")
        if instance.status == InstanceStatus.RUNNING:
            return instance
        if instance.status != InstanceStatus.PROVISIONING:
            raise InstanceNotReady(f"Instance entered {instance.status} during startup.")

        remaining = deadline - monotonic()
        if remaining <= 0:
            raise InstanceNotReady(f"Instance was not ready after {timeout:g} seconds.")
        sleep(min(poll_interval, remaining))
