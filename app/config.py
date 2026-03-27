from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    app_name: str = "Key Manager"
    debug: bool = False
    database_url: str = "sqlite:///./keys.db"
    secret_key: str
    login_password: str
    fofa_api_keys: list[str] = []


settings = Settings()
