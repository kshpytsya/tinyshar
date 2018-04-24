import os
import pytest
import shlex


@pytest.fixture(scope="session")
def run_wrapper():  # pragma: no cover
    v = os.environ.get("TINYSHAR_TEST_RUN_WRAPPER")

    if v is None:
        return []
    else:
        return shlex.split(v, posix=False)
