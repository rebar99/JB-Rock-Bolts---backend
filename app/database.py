from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.exc import OperationalError
from app.config import settings
import logging

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def create_database_if_not_exists():
    # Railway and other managed hosts pre-create the database — skip creation.
    if settings.MYSQL_URL:
        logger.info("Using external database URL — skipping CREATE DATABASE.")
        return

    engine_no_db = create_engine(settings.DATABASE_URL_WITHOUT_DB, echo=False)
    try:
        with engine_no_db.connect() as conn:
            conn.execute(
                text(f"CREATE DATABASE IF NOT EXISTS `{settings.DB_NAME}` "
                     f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            )
            conn.commit()
        logger.info(f"Database '{settings.DB_NAME}' ready.")
    except OperationalError as e:
        logger.warning(f"Could not create database '{settings.DB_NAME}': {e}. Assuming it already exists.")
    finally:
        engine_no_db.dispose()


engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
