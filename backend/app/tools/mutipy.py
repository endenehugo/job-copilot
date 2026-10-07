from typing import Any, Type

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool


class MultiplyInput(BaseModel):
    a: int = Field(description="第一个数字")
    b: int = Field(description="第二个数字")


class MultiplyTool(BaseTool):
    """乘法计算工具"""
    name: str = "multiply_tool"
    description: str = "将传递的两个数字相乘后返回"
    args_schema: Type[BaseModel] = MultiplyInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """将传入的a和b相乘后返回"""
        return kwargs.get("a") * kwargs.get("b")


calculator = MultiplyTool()