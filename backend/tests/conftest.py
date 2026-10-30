import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).parents[1]))
from frame.config import Settings
from frame.main import create_app


@pytest.fixture
def app(tmp_path):
    data = tmp_path / "data"
    application = create_app(Settings(data, f"sqlite:///{data / 'test.db'}", data / "thumbnails"))
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
