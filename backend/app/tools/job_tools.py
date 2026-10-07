from typing import Any, Type

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool


class JdParserInput(BaseModel):
    jd_text: str = Field(description="职位描述原文")


class JdParserTool(BaseTool):
    """JD 解析工具：分析职位描述，提取岗位名称、关键词、要求、加分项等"""
    name: str = "jd_parser_tool"
    description: str = "分析职位描述（JD），提取岗位名称、核心关键词、硬性要求、加分项和建议的项目角度。输入为原始 JD 文本。"
    args_schema: Type[BaseModel] = JdParserInput

    _service: Any = None

    def _run(self, jd_text: str) -> str:
        try:
            from app.services.job_description_service import JobDescriptionService
            service = JobDescriptionService()
            result = service.analyze(jd_text)
            import json
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"JD 解析失败：{str(e)}"


class ResumeScoreInput(BaseModel):
    resume_text: str = Field(description="简历文本")
    jd_text: str = Field(description="职位描述原文")


class ResumeScoreTool(BaseTool):
    """简历评分工具：根据 JD 对简历进行结构化评分"""
    name: str = "resume_score_tool"
    description: str = "根据职位描述（JD）对候选人简历进行评分，返回总分、四维评分（技能匹配度、项目相关性、表达质量、岗位适配度）、优势、缺口和建议。需要同时提供简历文本和 JD 文本。"
    args_schema: Type[BaseModel] = ResumeScoreInput

    def _run(self, resume_text: str, jd_text: str) -> str:
        try:
            from app.services.resume_scoring_service import ResumeScoringService
            service = ResumeScoringService()
            result = service.score(resume_text, jd_text)
            import json
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"简历评分失败：{str(e)}"


class ProjectRewriteInput(BaseModel):
    project_description: str = Field(description="简历中的项目经历描述")


class ProjectRewriteTool(BaseTool):
    """项目经历优化工具：优化简历中的项目描述"""
    name: str = "project_rewrite_tool"
    description: str = "优化简历中的项目经历描述，生成通用优化版本、Python 后端导向版本和 Agent/AI 导向版本。输入为原始项目描述文本。"
    args_schema: Type[BaseModel] = ProjectRewriteInput

    def _run(self, project_description: str) -> str:
        try:
            from app.services.project_rewrite_service import ProjectRewriteService
            service = ProjectRewriteService()
            result = service.rewrite(project_description)
            import json
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"项目优化失败：{str(e)}"


class MockInterviewInput(BaseModel):
    jd_text: str = Field(description="职位描述原文")
    resume_text: str = Field(description="简历文本")


class MockInterviewTool(BaseTool):
    """模拟面试工具：根据 JD 和简历生成面试题"""
    name: str = "mock_interview_tool"
    description: str = "根据职位描述（JD）和候选人简历，生成模拟面试题，包含题目、参考答题点和难度级别。需要同时提供 JD 文本和简历文本。"
    args_schema: Type[BaseModel] = MockInterviewInput

    def _run(self, jd_text: str, resume_text: str) -> str:
        try:
            from app.services.interview_simulation_service import InterviewSimulationService
            service = InterviewSimulationService()
            result = service.start_interview(jd_text, resume_text)
            import json
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"面试题生成失败：{str(e)}"
