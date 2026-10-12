import logging
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


plugin_root = Path(__file__).resolve().parents[1]
configured_source = os.environ.get("KIRAAI_SOURCE")
framework_root = Path(configured_source).resolve() if configured_source else next(
    (parent for parent in plugin_root.parents if (parent / "core/provider/provider_manager.py").is_file()),
    None,
)
if framework_root is None or not (framework_root / "core/provider/provider_manager.py").is_file():
    raise pytest.UsageError("Set KIRAAI_SOURCE to a KiraAI v3 checkout with its dependencies installed")
sys.path.insert(0, str(framework_root))

from core.utils.path_utils import init_paths

# Isolate framework logs/config paths from both checkouts during tests.
_test_data = TemporaryDirectory(prefix="kira-openai-api-tests-")
init_paths(data_dir=_test_data.name)


def pytest_sessionfinish(session, exitstatus):
    logging.shutdown()
    _test_data.cleanup()


@pytest.fixture
def anyio_backend():
    return "asyncio"
