from pydantic import BaseModel, Field
from typing import Any, Literal
from zoneinfo import available_timezones

class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    due_at: str | None = None
    priority: str = "medium"
    category: str = "personal"
    recurrence: Literal["daily", "weekdays", "weekly"] | None = None
    reminder_at: str | None = None
    reminder_enabled: bool = True

class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    due_at: str | None = None
    priority: Literal["low","medium","high","urgent"] | None = None
    category: str | None = Field(default=None, max_length=80)
    status: Literal["open","completed","cancelled"] | None = None
    recurrence: Literal["daily", "weekdays", "weekly"] | None = None
    reminder_at: str | None = None
    reminder_enabled: bool | None = None
    model_config = {"extra":"forbid"}

class SessionPatch(BaseModel):
    status: Literal["planned","completed","cancelled"] | None = None
    actual_minutes: int | None = Field(default=None, ge=0, le=1440)
    notes: str | None = Field(default=None, max_length=10000)
    model_config = {"extra":"forbid"}

class SubjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color: str = "#52796f"

class SessionIn(BaseModel):
    subject_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    starts_at: str
    duration_minutes: int = Field(gt=0, le=1440)
    notes: str = ""

class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    nodes: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    edges: list[dict[str, str]] = Field(default_factory=list)
    schedule_at: str | None = None
    schedule_recurrence: Literal["daily", "weekdays", "weekly"] | None = None
    schedule_timezone: str = "Asia/Calcutta"

class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    purpose: str = ""
    instructions: str = ""
    model: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
