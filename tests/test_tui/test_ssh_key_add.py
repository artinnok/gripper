import httpx
from textual.widgets import DataTable, Input, OptionList, Static

from gripper.core.ssh_keys import read_public_key
from gripper.tui.app import GripperApp
from gripper.tui.message import Message
from gripper.tui.ssh_key.add import SSHKeyAddScreen
from gripper.tui.ssh_key.list import SSHKeyListScreen
from tests.conftest import SSH_PUBLIC_KEY
from tests.test_tui.conftest import run_app, settle


def test_local_key_selection_previews_and_registers(settings, config_path, registered_key_list, home):
    """Offer discovered public keys on the left and their details on the right."""

    config_path.touch()
    key_path = home / ".ssh" / "id_ed25519.pub"
    key_path.parent.mkdir()

    async def scenario(pilot):
        """Choose a visible key, register it, and see the current provider list."""

        await pilot.press("k")
        await settle(pilot)
        await pilot.press("a")
        await settle(pilot)
        screen = pilot.app.screen
        assert isinstance(screen, SSHKeyAddScreen)
        key_path.write_text(SSH_PUBLIC_KEY)
        await pilot.press("r")
        await settle(pilot)
        assert screen.query_one(OptionList).region.right <= screen.query_one("#detail").region.x
        assert SSH_PUBLIC_KEY in screen.query_one("#info", Static).content
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, SSHKeyListScreen)
        assert pilot.app.screen.query_one(DataTable).row_count == 1

    run_app(GripperApp(settings, config_path), scenario)
    assert registered_key_list == [SSH_PUBLIC_KEY]


def test_custom_path_is_labelled_on_left_and_validated(settings, config_path, registered_key_list, tmp_path):
    """Keep invalid entries open and register only a real public key file."""

    config_path.touch()
    key_path = tmp_path / "custom.pub"
    key_path.write_text(SSH_PUBLIC_KEY)

    async def scenario(pilot):
        """Try an empty and missing path, then submit the correct file."""

        await pilot.press("k")
        await settle(pilot)
        await pilot.press("a", "enter", "enter")
        screen = pilot.app.screen
        prompt = screen.query_one(Input)
        assert prompt.parent.id == "key-source"
        assert screen.query_one("#path-label", Static).content == "Public key file (.pub)"
        assert "Enter a key file path" in screen.query_one(Message).content
        prompt.value = str(tmp_path / "missing.pub")
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, SSHKeyAddScreen)
        assert screen.query_one(Message).content
        assert not prompt.disabled
        prompt.value = str(key_path)
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, SSHKeyListScreen)

    run_app(GripperApp(settings, config_path), scenario)
    assert registered_key_list == [SSH_PUBLIC_KEY]


def test_generation_retries_registration_without_replacing_key(settings, config_path, runpod_http, home):
    """Keep a newly generated pair when registration fails and retry only its public-key upload."""

    config_path.touch()
    runpod_http.side_effect = [httpx.Response(200, json={"keys": []}), httpx.Response(403)]
    private_path = home / ".ssh" / "gripper_ed25519"
    public_path = private_path.with_suffix(".pub")

    async def scenario(pilot):
        """Generate explicitly, observe failure, then register the same generated public key."""

        await pilot.press("k")
        await settle(pilot)
        await pilot.press("a", "down", "enter")
        assert "generate" in pilot.app.screen.query_one("#keys", Static).content
        await pilot.press("enter")
        await settle(pilot)
        assert private_path.exists()
        private_contents = private_path.read_bytes()
        public_key = read_public_key(public_path).public_key
        assert "403" in pilot.app.screen.query_one(Message).content
        assert pilot.app.screen.query_one(Input).value == str(public_path)
        runpod_http.side_effect = [
            httpx.Response(200, json={"keys": []}),
            httpx.Response(200, json={"keys": [public_key]}),
            httpx.Response(200, json={"keys": [public_key]}),
        ]
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(pilot.app.screen, SSHKeyListScreen)
        assert private_path.read_bytes() == private_contents
        assert b"PRIVATE KEY" not in runpod_http.call_args_list[-2].args[0].content

    run_app(GripperApp(settings, config_path), scenario)


def test_generation_preserves_existing_files(settings, config_path, registered_key_list, home):
    """Reject a conflicting generation path without writing files or provider registrations."""

    config_path.touch()
    private_path = home / ".ssh" / "gripper_ed25519"
    private_path.parent.mkdir()
    private_path.write_text("existing private key")

    async def scenario(pilot):
        """Choose generation with its existing default path and cancel the error."""

        await pilot.press("k")
        await settle(pilot)
        await pilot.press("a", "down", "enter", "enter")
        await settle(pilot)
        assert "already exist" in pilot.app.screen.query_one(Message).content
        await pilot.press("escape", "escape")
        assert isinstance(pilot.app.screen, SSHKeyListScreen)

    run_app(GripperApp(settings, config_path), scenario)
    assert private_path.read_text() == "existing private key"
    assert registered_key_list == []
