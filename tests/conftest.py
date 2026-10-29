import os

import pytest

os.environ.setdefault("SAMATVA_LIVE", "0")  # the API's live engine is started explicitly in the tests that need it

from samatva.workspace import Workspace  # noqa: E402


@pytest.fixture(scope="session")
def ws() -> Workspace:
    """The sample workspace, built once and shared (it is never mutated)."""
    return Workspace.sample()
