# test fixtures and shared setup
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
