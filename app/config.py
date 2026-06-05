from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    APP_NAME: str = "JB Rock Bolts API"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "production"
    DEBUG: bool = False
    PORT: int = 8000

    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "jbrockbolts_db"
    DB_USER: str = "root"
    DB_PASSWORD: str = ""

    # Full MySQL connection URL — Railway provides this as DATABASE_URL.
    # Set MYSQL_URL in Render to Railway's DATABASE_URL value; takes priority over DB_* vars.
    MYSQL_URL: Optional[str] = None

    CORS_ORIGINS: str = "http://localhost:8080,http://localhost:3000,http://localhost:5173,http://localhost:8081,http://127.0.0.1:8080,http://127.0.0.1:5173,http://127.0.0.1:8081"

    SECRET_KEY: str = "change-this-secret-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 200

    # ── Cloudflare R2 ─────────────────────────────────────────────────────────
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "jb-rock-bolts"
    # Public URL for the bucket — R2.dev subdomain or your custom domain
    R2_PUBLIC_URL: str = ""

    @property
    def r2_enabled(self) -> bool:
        return bool(
            self.R2_ACCOUNT_ID
            and self.R2_ACCESS_KEY_ID
            and self.R2_SECRET_ACCESS_KEY
            and self.R2_PUBLIC_URL
        )

    @property
    def DATABASE_URL(self) -> str:
        if self.MYSQL_URL:
            url = self.MYSQL_URL
            if url.startswith("mysql://") and "pymysql" not in url:
                url = "mysql+pymysql://" + url[len("mysql://"):]
            return url
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
