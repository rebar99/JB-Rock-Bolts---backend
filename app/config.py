import os

from pydantic_settings import BaseSettings
from typing import List, Optional


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

    # Fall back to Railway's MYSQLHOST/MYSQLPORT/MYSQLUSER/MYSQLPASSWORD/
    # MYSQLDATABASE variables when the app-specific DB_* variables aren't
    # set, and finally to localhost defaults for local development.
    DB_HOST: str = os.getenv("DB_HOST", os.getenv("MYSQLHOST", "localhost"))
    DB_PORT: int = int(os.getenv("DB_PORT", os.getenv("MYSQLPORT", "3306")))
    DB_NAME: str = os.getenv("DB_NAME", os.getenv("MYSQLDATABASE", "jbrockbolts_db"))
    DB_USER: str = os.getenv("DB_USER", os.getenv("MYSQLUSER", "root"))
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", os.getenv("MYSQLPASSWORD", ""))

    CORS_ORIGINS: str = "http://localhost:8080,http://localhost:3000,http://localhost:5173,http://localhost:8081,http://127.0.0.1:8080,http://127.0.0.1:5173,http://127.0.0.1:8081"

    SECRET_KEY: str = "change-this-secret-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 200

    @property
    def DATABASE_URL(self) -> str:
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
