import re

import httpx
from prompt_toolkit.utils import get_cwidth

from galloc.main import cli
from tests.conftest import make_gpu_data
from tests.test_cli.conftest import TerminalInput


def test_stock_and_low_price_marker(runner, runpod_http, keyboard):
    """Show regional stock, a strict price threshold, and aligned columns with a text lightning marker."""

    gpu_list = [
        make_gpu_data(
            id=name,
            name=name,
            price={"secure": price},
            dataCenters=[{"id": "US-KS-2", "availability": stock}],
        )
        for name, price, stock in [("Cheaper", 0.39, "LOW"), ("Boundary", 0.40, "MEDIUM"), ("Higher", 0.41, "HIGH")]
    ]
    runpod_http.side_effect = [httpx.Response(200, json={"gpus": gpu_list})]
    keyboard.send_bytes(b"\x1b")

    result = runner.invoke(cli, ["add", "--region", "US-KS-2", "--ttl", "30m"], input=TerminalInput())

    assert result.exit_code == 1
    assert "STOCK" in result.stderr
    assert "Availability may change before creation." in result.stderr
    assert re.search(r"Cheaper +ϟ +\$0.39 +141 +188 +12 +Low", result.stderr)
    assert re.search(r"Boundary +\$0.40 +141 +188 +12 +Medium", result.stderr)
    assert re.search(r"Higher +\$0.41 +141 +188 +12 +High", result.stderr)
    row_list = [line for line in result.stderr.splitlines() if re.match(r"\s*[●○] (Cheaper|Boundary|Higher)", line)]
    assert len(row_list) == 3
    assert len({get_cwidth(row.split("$")[0]) for row in row_list}) == 1
