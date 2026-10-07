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
        try:
            a = kwargs.get("a")
            b = kwargs.get("b")
            if a is None or b is None:
                return "错误：缺少参数 a 或 b。"
            # 布尔是 int 子类，显式排除；非数字返回错误文本而不是抛异常
            if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                return f"错误：参数 a 和 b 必须是数字，收到 a={a!r}, b={b!r}。"
            return a * b
        except Exception as exc:
            return f"错误：乘法计算失败：{exc}"


calculator = MultiplyTool()