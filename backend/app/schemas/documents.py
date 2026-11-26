from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    size_bytes: int
    page_count: int
    doc_type: str | None
    status: str
    doc_type_source: str
    doc_type_confidence: float | None
    num_questions: int
    generated_question_count: int | None = None
    difficulty: str
    error_message: str | None
    created_at: datetime
