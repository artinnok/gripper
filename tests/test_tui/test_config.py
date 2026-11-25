import json

from textual.widgets import Static

from gripper.settings import load_settings
from gripper.tui.app import GripperApp
from tests.conftest import SSH_PUBLIC_KEY
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


def test_creation_uses_shared_disk_and_registers_no_key(config_path, write_config, home, runpod_http, pod_data):
    """Apply the same TOML defaults as the CLI and leave the account's SSH keys untouched."""

    (home / "provider.pub").write_text(SSH_PUBLIC_KEY)
    write_config(
        defaults={"provider": "runpod", "disk_gb": 100, "ssh_public_key": "unused.pub"},
        runpod={"ssh_public_key": "~/provider.pub"},
    )
    settings = load_settings(config_path)
    runpod_http.side_effect = lambda request: serve_catalog(request, pod_data)

    async def scenario(pilot):
        """Create through the TUI using settings read from the temporary TOML file."""

        pilot.app.screen._create_gpu(("runpod", "H200", "US-KS-2", "1h", None))
        await settle(pilot)
        assert f"ssh -i {home / 'provider'} root@" in pilot.app.screen.query_one("#info", Static).content

    run_app(GripperApp(settings, config_path), scenario)
    request_list = [call.args[0] for call in runpod_http.call_args_list]
    (pod_request,) = [request for request in request_list if request.method == "POST"]
    assert json.loads(pod_request.content)["disk"] == 100
    assert not any(request.url.path.endswith("account/ssh-keys") for request in request_list)
