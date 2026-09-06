from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All config comes from env vars (or a .env file). Nothing here is
    hardcoded so the same code runs against any user's local image
    folder / DB without editing source."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./jobs.db"

    # Directory the user's images actually live in. image_path in job
    # requests is resolved relative to this, so images never need to be
    # committed to the repo (see IMAGE_DIR in README.md / .env.example).
    image_dir: str = "./sample_images"


@lru_cache
def get_settings() -> Settings:
    return Settings()
