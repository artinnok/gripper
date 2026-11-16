import json

import httpx
from textual.widgets import Static

from galloc.settings import load_settings
from galloc.tui.app import GallocApp
from tests.conftest import OTHER_SSH_PUBLIC_KEY, SSH_PUBLIC_KEY
from tests.test_tui.conftest import run_app, settle
from tests.test_tui.test_add import serve_catalog


def test_creation_uses_shared_disk_and_provider_key(config_path, write_config, home, runpod_http, pod_data):
    """Apply the same TOML defaults as the CLI while preserving unrelated registered keys."""

    public_key = home / "provider.pub"
    public_key.write_text(SSH_PUBLIC_KEY)
    write_config(
        defaults={"provider": "runpod", "disk_gb": 100, "ssh_public_key": "unused.pub"},
        runpod={"ssh_public_key": "~/provider.pub"},
    )
    settings = load_settings(config_path)

    def handler(request):
        """Keep the existing key when adding the configured one before Pod creation."""

        if request.url.path.endswith("account/ssh-keys"):
            return httpx.Response(200, json={"keys": [OTHER_SSH_PUBLIC_KEY]})
        return serve_catalog(request, pod_data)

    runpod_http.side_effect = handler

    async def scenario(pilot):
        """Create through the TUI using settings read from the temporary TOML file."""

        pilot.app.screen._create_gpu(("H200", "US-KS-2", "1h", None))
        await settle(pilot)
        assert "ssh root@" in pilot.app.screen.query_one("#info", Static).content

    run_app(GallocApp(settings, config_path), scenario)
    (key_request,) = [call.args[0] for call in runpod_http.call_args_list if call.args[0].method == "PUT"]
    (pod_request,) = [call.args[0] for call in runpod_http.call_args_list if call.args[0].method == "POST"]
    assert json.loads(key_request.content) == {"keys": [OTHER_SSH_PUBLIC_KEY, SSH_PUBLIC_KEY]}
    assert json.loads(pod_request.content)["disk"] == 100
