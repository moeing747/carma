import shutil
import subprocess
from functools import cache

import pytest


@cache
def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return probe.returncode == 0


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    integration_items = [item for item in items if "integration" in item.keywords]
    if not integration_items or _docker_available():
        return
    skip = pytest.mark.skip(reason="integration tests need a running Docker daemon")
    for item in integration_items:
        item.add_marker(skip)
