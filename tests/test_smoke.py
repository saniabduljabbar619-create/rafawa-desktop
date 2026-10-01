from shared.config.constants import WALKIN_PARTY_ID
from tests.conftest import CROP_ID, stock


def test_world_is_seeded():
    assert stock(CROP_ID) == (20, 2000)
    assert WALKIN_PARTY_ID == 11
