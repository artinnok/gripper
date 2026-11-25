import pytest

from gripper.core.ssh_keys import generate_key_pair, read_public_key
from gripper.exceptions import SSHKeyGenerationFailed


def test_generated_key_pair_is_valid_and_never_replaced(tmp_path):
    """Create a usable public key and refuse to overwrite an existing pair."""

    private_key_path = tmp_path / ".ssh" / "gripper_ed25519"

    public_key_path = generate_key_pair(private_key_path)

    assert public_key_path == tmp_path / ".ssh" / "gripper_ed25519.pub"
    assert read_public_key(public_key_path).comment == "gripper"
    private_key = private_key_path.read_text()

    with pytest.raises(SSHKeyGenerationFailed):
        generate_key_pair(private_key_path)
    assert private_key_path.read_text() == private_key


def test_generation_preserves_an_existing_public_key(tmp_path):
    """Do not replace a public key when its private counterpart is not on this machine."""

    private_key_path = tmp_path / "id_ed25519"
    public_key_path = tmp_path / "id_ed25519.pub"
    public_key_path.write_text("existing public key\n")

    with pytest.raises(SSHKeyGenerationFailed, match="already exist"):
        generate_key_pair(private_key_path)

    assert public_key_path.read_text() == "existing public key\n"
    assert not private_key_path.exists()
