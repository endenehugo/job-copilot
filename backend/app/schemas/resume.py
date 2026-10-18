"""简历生成相关请求模型。"""

from __future__ import annotations

from pydantic import BaseModel


class ResumeGenerateRequest(BaseModel):
    """自动生成简历。

    extra_fields 是用户在生成前的少量补充（如学校名称、毕业时间），
    键为中文标签、值为文本；抽出后会以 basis=user 记入出处。
    """

    conversation_id: str = ""
    # 空串 = 服务端按角色取默认值；越权模型由服务端 400
    model: str = ""
    extra_fields: dict[str, str] = {}


class ResumeRegenerateRequest(BaseModel):
    """在已有版本上补字段后重渲染（0 次 LLM）。

    只认「占位符级」字段（姓名/学校/专业/联系方式等），认不出的键会在
    ignored_fields 里原样返回，不会瞎猜。
    """

    extra_fields: dict[str, str] = {}
