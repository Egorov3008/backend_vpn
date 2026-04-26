from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    bot_secret_key: str = "changeme"
    admin_api_key: str = "changeme"
    log_level: str = "INFO"


settings = Settings()
