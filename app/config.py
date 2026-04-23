"""Configurações carregadas do .env via pydantic-settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Credenciais
    lefisc_username: str
    lefisc_password: str

    # URLs
    lefisc_login_url: str = "https://www.lefisc.com.br/"
    lefisc_ncm_url: str = "https://www.lefisc.com.br/ncm/conteudo.aspx"

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_reload: bool = True

    # Scraper
    headless: bool = True
    browser_timeout_ms: int = 30000

    # Cache
    cache_ttl_seconds: int = 86400
    cache_max_size: int = 1000


settings = Settings()
