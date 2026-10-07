"""文档相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class DeleteDocumentRequest(BaseModel):
    document_id: str = ""
