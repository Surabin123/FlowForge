from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    database: str = "data/flowforge.db"
    documents: str = "storage/documents"
    timezone: str = "Asia/Calcutta"
    max_upload_mb: int = Field(default=20, ge=1, le=100)
    max_parallel_nodes: int = Field(default=4, ge=1, le=16)
    max_parallel_runs: int = Field(default=4, ge=1, le=32)
    max_workflow_duration_seconds: int = Field(default=300, ge=30, le=3600)
    workflow_max_attempts: int = Field(default=3, ge=1, le=5)
    workflow_retry_base_seconds: float = Field(default=0.25, ge=0, le=30)
    workflow_retry_cap_seconds: float = Field(default=2.0, ge=0, le=60)
    max_workflow_output_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    max_ai_input_chars: int = Field(default=100_000, ge=1_000, le=1_000_000)
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = ""
    github_token: str = ""
    github_owner: str = ""
    require_auth: bool = False
    access_token: str = ""
    container_mode: bool = False
    allowed_hosts: str = "127.0.0.1,localhost,testserver"
    model_config = SettingsConfigDict(env_prefix="FLOWFORGE_", env_file=ROOT / ".env", extra="ignore")

    @property
    def db_path(self) -> Path:
        p = Path(self.database)
        return p if p.is_absolute() else ROOT / p

    @property
    def document_path(self) -> Path:
        p = Path(self.documents)
        return p if p.is_absolute() else ROOT / p

    @property
    def trusted_hosts(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    def validate_runtime(self) -> None:
        if self.require_auth and not self.access_token:
            raise RuntimeError("FLOWFORGE_ACCESS_TOKEN is required when FLOWFORGE_REQUIRE_AUTH is enabled")
        if not self.container_mode:
            return
        if not self.require_auth:
            raise RuntimeError("Container mode requires FLOWFORGE_REQUIRE_AUTH=true")
        if len(self.access_token) < 32:
            raise RuntimeError("Container mode requires a FLOWFORGE_ACCESS_TOKEN of at least 32 characters")
        if not self.trusted_hosts or "*" in self.trusted_hosts:
            raise RuntimeError("Container mode requires explicit FLOWFORGE_ALLOWED_HOSTS")
        if not Path(self.database).is_absolute() or not Path(self.documents).is_absolute():
            raise RuntimeError("Container mode requires absolute FLOWFORGE_DATABASE and FLOWFORGE_DOCUMENTS paths")

settings = Settings()
