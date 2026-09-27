from database.session import get_session
from sqlalchemy import text

with get_session() as s:
    print(s.execute(text("SELECT 1")).scalar())
    print("Database connection successful.")