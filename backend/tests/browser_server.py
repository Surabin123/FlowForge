"""Local browser-test server with isolated storage and a deterministic planner."""

import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

storage = tempfile.TemporaryDirectory(prefix="flowforge-browser-")
os.environ.update({
    "FLOWFORGE_DATABASE": str(Path(storage.name) / "browser.db"),
    "FLOWFORGE_DOCUMENTS": str(Path(storage.name) / "documents"),
    "FLOWFORGE_AI_API_KEY": "browser-test-only",
    "FLOWFORGE_AI_MODEL": "deterministic-test-planner",
    "FLOWFORGE_REQUIRE_AUTH": "false",
    "FLOWFORGE_CONTAINER_MODE": "false",
    "FLOWFORGE_ALLOWED_HOSTS": "127.0.0.1,localhost,testserver",
})

from app import automation  # noqa: E402
from app.main import app  # noqa: E402


async def fake_outbound_request(method, url, **kwargs):
    if method != "POST" or kwargs.get("operation") != "automation_plan":
        raise RuntimeError("Unexpected external request in browser test")
    proposal = {
        "title": "Browser daily plan",
        "summary": "Read real tasks and save a verified local plan.",
        "steps": [
            {"id": "open", "name": "Read open tasks", "kind": "tool", "tool_id": "tasks.list_open", "arguments": {"limit": 30}, "depends_on": []},
            {"id": "prioritize", "name": "Prioritize tasks", "kind": "tool", "tool_id": "plans.daily", "arguments": {"source_node": "open"}, "depends_on": ["open"]},
            {"id": "save", "name": "Save plan", "kind": "tool", "tool_id": "artifacts.save", "arguments": {"title": "Browser plan artifact", "kind": "daily_plan", "source_node": "prioritize"}, "depends_on": ["prioritize"]},
        ],
        "schedule": None,
        "clarifying_questions": [],
    }
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(proposal)}}]})


automation.outbound_request = fake_outbound_request

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18731, workers=1, log_level="warning")
