import httpx
import pytest

from galloc.providers.exceptions import GPUUnavailable
from galloc.providers.runpod.provider import RunpodProvider
from tests.conftest import make_gpu_data


def test_global_and_regional_stock(make_runpod_client):
    """Expose stock in the selected geography and exclude unavailable models consistently."""

    gpu_list = [
        make_gpu_data(dataCenters=[{"id": "US-KS-2", "availability": "LOW"}]),
        make_gpu_data(id="inconsistent", name="Unavailable", availability="NONE"),
        make_gpu_data(id="no-regions", name="No regions", dataCenters=[]),
    ]

    def handler(request):
        """Supply the same catalog to listing and region selection."""

        assert request.method == "GET"
        assert request.url.path == "/v2/catalog/gpus"
        return httpx.Response(200, json={"gpus": gpu_list})

    provider = RunpodProvider(make_runpod_client(handler), image="example/image", disk_gb=50)

    (global_gpu,) = provider.get_gpu_list()
    (regional_gpu,) = provider.get_gpu_list(" us-ks-2 ")
    assert global_gpu.id == regional_gpu.id == "NVIDIA H200"
    assert global_gpu.availability == "HIGH"
    assert regional_gpu.availability == "LOW"
    with pytest.raises(GPUUnavailable):
        provider.get_region_list("inconsistent")
