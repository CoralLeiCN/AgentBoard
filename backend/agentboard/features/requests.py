from pydantic import BaseModel, Field


class ReplayRequest(BaseModel):
    input_id: str
    replacement: str = Field(min_length=1, max_length=30000)
