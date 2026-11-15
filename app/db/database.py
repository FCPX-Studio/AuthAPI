from config import settings

from functools import cache

from sqlalchemy.orm import Session, sessionmaker

from db import Database, make_session_factory


@cache
def default_session_factory() -> sessionmaker[Session]:
    """
    Build the engine once per process, on first use.
    Every repository shares the same connection pool.
    The schema itself is managed by the shared `db` package migrations.
    """
    return make_session_factory(settings.DB_URL.get_secret_value(), pool_size=settings.DB_SIZE)


class Repository(Database):
    """
    Base class for AuthAPI repositories, bound to the shared session factory by default
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        """
        Args:
            session_factory (sessionmaker | None): Override for tests, defaults to the shared engine
        """
        super().__init__(session_factory or default_session_factory())
