from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    database: str = "data/flowforge.db"
    documents: str = "storage/documents"
    timezone: str = "Asia/Calcutta"
    max_upload_mb: int = 20
    max_parallel_nodes: int = 4
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = ""
    github_token: str = ""
    github_owner: str = ""
    model_config = SettingsConfigDict(env_prefix="FLOWFORGE_", env_file=ROOT / ".env", extra="ignore")

    @property
    def db_path(self) -> Path:
        p = Path(self.database)
        return p if p.is_absolute() else ROOT / p

    @property
    def document_path(self) -> Path:
        p = Path(self.documents)
        return p if p.is_absolute() else ROOT / p

settings = Settings()
