from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_email_recipients() -> list[str]:
    return ["2356357995@qq.com", "1601155817@qq.com"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    app_name: str = "Key Manager"
    debug: bool = False
    database_url: str = "sqlite:///./keys.db"
    secret_key: str
    login_password: str
    fofa_api_keys: list[str] = []

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = "nkepig@gmail.com"
    smtp_password: str = "qsnfekjysybykiav"
    email_recipients: list[str] = Field(default_factory=_default_email_recipients)


settings = Settings()
