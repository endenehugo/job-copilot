"""内置求职技能工具：按需加载技能的完整操作指南（SKILL.md）。

工具结果给的是"怎么做这件事的方法论"，模型拿到后结合会话内已有材料
（简历、JD、检索上下文）按指南执行。错误按约定以字符串返回，不抛异常。
"""

from typing import Any, Type

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

from app.utils import skill_loader


class LoadSkillInput(BaseModel):
    skill_name: str = Field(description="技能名称，如 job-match、make-resume、interview")
    reference: str = Field(
        default="",
        description="可选。技能目录下的 reference 文件相对路径，例如 references/page-balance-qa.md；留空则加载 SKILL.md 正文",
    )


class SkillTool(BaseTool):
    """加载内置求职技能指南"""

    name: str = "load_skill"
    description: str = (
        "加载内置求职技能的完整操作指南（来自 ASu-skills）。"
        "当用户任务匹配某个技能场景时（岗位匹配判断 job-match、简历经历改写 great-resume、"
        "项目讲解与面经 project-guide、开发过程证据整理 evidence-recap），"
        "必须先调用本工具加载对应指南，再严格按指南执行。"
        "制作 HTML 简历请用 resume_generate_tool，模拟面试请用 mock_interview_tool——"
        "这两个能力已内置在工具里，不要 load_skill。"
        "参数 skill_name 为技能名称；不确定有哪些技能时，可传 skill_name='list' 查看目录。"
        "SKILL.md 中提到某个 reference 时，把路径传进 reference 参数即可读取该细化规则。"
    )
    args_schema: Type[BaseModel] = LoadSkillInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        try:
            skill_name = (kwargs.get("skill_name") or "").strip()
            reference = (kwargs.get("reference") or "").strip()
            if not skill_name or skill_name.lower() == "list":
                skills = skill_loader.list_skills()
                lines = [f"- {s['name']}：{s['summary']}" for s in skills]
                return "可用技能列表：\n" + "\n".join(lines)
            if reference:
                return skill_loader.load_reference(skill_name, reference)
            content = skill_loader.load_skill(skill_name)
            refs = skill_loader.list_references(skill_name)
            if refs:
                content += (
                    "\n\n---\n\n本技能还有以下细化 reference，需要判断排版/证据细节时用 "
                    "reference 参数按需读取（不要一次性全读）：\n"
                    + "\n".join(f"- {ref}" for ref in refs)
                )
            return content
        except FileNotFoundError:
            available = "、".join(s["name"] for s in skill_loader.list_skills())
            return f"错误：技能 {kwargs.get('skill_name')!r} 或 reference {kwargs.get('reference')!r} 不存在。可用技能：{available}"
        except ValueError as exc:
            return f"错误：{exc}"
        except Exception as exc:
            return f"错误：加载技能失败：{exc}"


skill_tool = SkillTool()
