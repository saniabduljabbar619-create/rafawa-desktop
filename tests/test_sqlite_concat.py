from sqlalchemy import text

from database.session import get_session


def test_concat_works_even_on_a_sqlite_without_it():
    with get_session() as s:
        assert s.execute(text("SELECT CONCAT('SAL-', 7)")).scalar() == "SAL-7"
        assert s.execute(text("SELECT CONCAT('SAL-', NULL)")).scalar() is None
