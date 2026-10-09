from pathlib import Path

import pytest

from realtymap import db
from realtymap.config import load_region

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def region():
    return load_region("bay_county_mi")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.sqlite3")
    yield c
    c.close()
