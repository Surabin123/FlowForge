import asyncio
import io

import httpx
from starlette.datastructures import UploadFile

from app import config
from app.database import init_db
from app.main import summarize_document, upload_document


def test_ai_summary_is_persisted_and_sends_content_to_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "database", str(tmp_path / "summary.db"))
    monkeypatch.setattr(config.settings, "documents", str(tmp_path / "documents"))
    monkeypatch.setattr(config.settings, "ai_api_key", "test-key")
    monkeypatch.setattr(config.settings, "ai_model", "test-model")
    config.settings.document_path.mkdir(parents=True, exist_ok=True)
    init_db()
    uploaded = asyncio.run(upload_document(UploadFile(file=io.BytesIO(b"Study notes about biology."), filename="notes.txt", headers=None)))
    original_client = httpx.AsyncClient

    async def handler(request):
        assert request.headers["authorization"] == "Bearer test-key"
        assert "Study notes about biology." in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "Notes about biology."}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: original_client(*args, transport=httpx.MockTransport(handler), **kwargs))
    result = asyncio.run(summarize_document(uploaded["id"]))
    assert result["summary"] == "Notes about biology."
    assert result["summary_status"] == "completed"
    assert result["summary_updated_at"]
