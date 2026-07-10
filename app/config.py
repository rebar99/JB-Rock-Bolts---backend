import os

from pydantic_settings import BaseSettings
from typing import List, Optional
from urllib.parse import urlparse, unquote


def _parse_db_url(url: Optional[str]) -> dict:
    """Extract host/port/user/password/database from a SQLAlchemy/MySQL
    connection string (e.g. mysql://user:pass@host:port/dbname)."""
    if not url:
        return {}
    try:
        parsed = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
        return {
            "host": parsed.hostname,
            "port": parsed.port,
            "user": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None,
            "database": parsed.path.lstrip("/") if parsed.path else None,
        }
    except Exception:
        return {}


# Railway's reference variable (e.g. DATABASE_URL=${{MySQL.MYSQL_URL}})
# injects a ready-to-use connection string directly as DATABASE_URL.
# Parse it up-front so DB_HOST/DB_PORT/etc (used for the "no database"
# connection during startup) stay in sync with it instead of silently
# falling back to localhost.
_PARSED_FROM_DATABASE_URL = _parse_db_url(os.getenv("DATABASE_URL"))
_PARSED_FROM_MYSQL_URL = _parse_db_url(os.getenv("MYSQL_URL"))


class Settings(BaseSettings):
    APP_NAME: str = "JB Rock Bolts API"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000

    # Railway's MySQL plugin injects a ready-to-use connection string via
    # MYSQL_URL. When present, it takes precedence over the individual
    # DB_* settings below.
    MYSQL_URL: Optional[str] = os.getenv("MYSQL_URL")

    # Fall back, in order, to: DATABASE_URL's components, Railway's
    # MYSQLHOST/MYSQLPORT/MYSQLUSER/MYSQLPASSWORD/MYSQLDATABASE variables,
    # MYSQL_URL's components, and finally localhost defaults for local
    # development.
    DB_HOST: str = os.getenv(
        "DB_HOST",
        os.getenv(
            "MYSQLHOST",
            _PARSED_FROM_DATABASE_URL.get("host")
            or _PARSED_FROM_MYSQL_URL.get("host")
            or "localhost",
        ),
    )
    DB_PORT: int = int(
        os.getenv(
            "DB_PORT",
            os.getenv(
                "MYSQLPORT",
                str(
                    _PARSED_FROM_DATABASE_URL.get("port")
                    or _PARSED_FROM_MYSQL_URL.get("port")
                    or 3306
                ),
            ),
        )
    )
    DB_NAME: str = os.getenv(
        "DB_NAME",
        os.getenv(
            "MYSQLDATABASE",
            _PARSED_FROM_DATABASE_URL.get("database")
            or _PARSED_FROM_MYSQL_URL.get("database")
            or "jbrockbolts_db",
        ),
    )
    DB_USER: str = os.getenv(
        "DB_USER",
        os.getenv(
            "MYSQLUSER",
            _PARSED_FROM_DATABASE_URL.get("user")
            or _PARSED_FROM_MYSQL_URL.get("user")
            or "root",
        ),
    )
    DB_PASSWORD: str = os.getenv(
        "DB_PASSWORD",
        os.getenv(
            "MYSQLPASSWORD",
            _PARSED_FROM_DATABASE_URL.get("password")
            or _PARSED_FROM_MYSQL_URL.get("password")
            or "",
        ),
    )

    CORS_ORIGINS: str = "http://localhost:8080,http://localhost:3000,http://localhost:5173,http://localhost:8081,http://127.0.0.1:8080,http://127.0.0.1:5173,http://127.0.0.1:8081"

    SECRET_KEY: str = "change-this-secret-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 200

    @property
    def DATABASE_URL(self) -> str:
        # Railway's reference variable (e.g. DATABASE_URL=${{MySQL.MYSQL_URL}})
        # injects a ready-to-use connection string directly as DATABASE_URL.
        # This takes precedence over everything else.
        env_database_url = os.getenv("DATABASE_URL")
        if env_database_url:
            if env_database_url.startswith("mysql+pymysql://"):
                return env_database_url
            return env_database_url.replace("mysql://", "mysql+pymysql://", 1)
        if self.MYSQL_URL:
            # Railway provides a "mysql://" scheme; SQLAlchemy needs the
            # pymysql driver specified explicitly.
            return self.MYSQL_URL.replace("mysql://", "mysql+pymysql://", 1)
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    @property
    def DATABASE_URL_WITHOUT_DB(self) -> str:
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}"
        )

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    class Config:
        env_file = ".env"


settings = Settings()
