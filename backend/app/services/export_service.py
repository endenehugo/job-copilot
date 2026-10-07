from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.repositories import DatabaseManager
from app.repositories.job_analysis_repository import JobAnalysisRepository
from app.repositories.interview_repository import InterviewSessionRepository, InterviewMessageRepository


@dataclass
class ExportService:

    def export_analysis_markdown(self, analysis_id: int) -> str:
        """导出 JD 分析结果为 Markdown"""
        db_manager = DatabaseManager()
        session = db_manager.get_session()
        try:
            analysis = JobAnalysisRepository.get_by_id(session, analysis_id)
            if analysis is None:
                raise ValueError("分析记录不存在")

            def _safe_json_load(value: str) -> list:
                if not value:
                    return []
                try:
                    return json.loads(value) if isinstance(value, str) else value
                except (json.JSONDecodeError, TypeError):
                    return []

            keywords = _safe_json_load(analysis.keywords)
            requirements = _safe_json_load(analysis.requirements)
            bonus_points = _safe_json_load(analysis.bonus_points)
            strengths = _safe_json_load(analysis.strengths)
            gaps = _safe_json_load(analysis.gaps)
            suggestions = _safe_json_load(analysis.suggestions)

            lines = [
                "# JD 分析报告",
                "",
                f"**岗位名称**：{analysis.job_role}",
                f"**分析时间**：{analysis.created_at.strftime('%Y-%m-%d %H:%M') if analysis.created_at else '-'}",
                f"**匹配总分**：{analysis.total_score}/100",
                "",
                "---",
                "",
                "## 维度评分",
                "",
                f"- 技能匹配度：{analysis.skill_match_score}/100",
                f"- 项目相关性：{analysis.project_relevance_score}/100",
                f"- 表达质量：{analysis.expression_quality_score}/100",
                f"- 岗位适配度：{analysis.job_fitness_score}/100",
                "",
                "---",
                "",
                "## 核心关键词",
                "",
            ]
            if keywords:
                lines.append(", ".join(f"`{kw}`" for kw in keywords))
            else:
                lines.append("（无）")

            lines += ["", "---", "", "## 硬性要求", ""]
            if requirements:
                for req in requirements:
                    lines.append(f"- {req}")
            else:
                lines.append("（无）")

            lines += ["", "---", "", "## 加分项", ""]
            if bonus_points:
                for bp in bonus_points:
                    lines.append(f"- {bp}")
            else:
                lines.append("（无）")

            lines += ["", "---", "", "## 优势", ""]
            if strengths:
                for s in strengths:
                    lines.append(f"- {s}")
            else:
                lines.append("（无）")

            lines += ["", "---", "", "## 缺口", ""]
            if gaps:
                for g in gaps:
                    lines.append(f"- {g}")
            else:
                lines.append("（无）")

            lines += ["", "---", "", "## 改进建议", ""]
            if suggestions:
                for s in suggestions:
                    lines.append(f"- {s}")
            else:
                lines.append("（无）")

            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("*由 求职助手 Agent 自动生成*")

            return "\n".join(lines)
        finally:
            session.close()

    def export_interview_markdown(self, session_id: str) -> str:
        """导出模拟面试回顾为 Markdown"""
        db_manager = DatabaseManager()
        db_session = db_manager.get_session()
        try:
            interview_session = InterviewSessionRepository.get_by_session_id(db_session, session_id)
            if interview_session is None:
                raise ValueError("面试会话不存在")

            messages = InterviewMessageRepository.list_by_session_id(db_session, session_id)

            lines = [
                "# 模拟面试回顾",
                "",
                f"**方向**：{interview_session.direction}",
                f"**状态**：{'已完成' if interview_session.status == 'completed' else '进行中'}",
                f"**轮次**：{interview_session.round_count} 轮",
            ]
            if interview_session.total_score is not None:
                lines.append(f"**总分**：{interview_session.total_score}/100")
            if interview_session.overall_summary:
                lines.append(f"**总结**：{interview_session.overall_summary}")
            lines.append(f"**时间**：{interview_session.created_at.strftime('%Y-%m-%d %H:%M') if interview_session.created_at else '-'}")
            lines += ["", "---", "", "## 对话记录", ""]

            for msg in messages:
                if msg.msg_type == "question":
                    lines.append(f"### 面试官\n\n{msg.content}\n")
                elif msg.msg_type == "answer":
                    lines.append(f"### 候选人\n\n{msg.content}\n")
                elif msg.msg_type == "evaluation":
                    score_str = f"（评分：{msg.score}/100）" if msg.score is not None else ""
                    lines.append(f"> **面试官评价**{score_str}：{msg.content}\n")

            lines.append("")
            lines.append("---")
            lines.append("*由 求职助手 Agent 自动生成*")

            return "\n".join(lines)
        finally:
            db_session.close()

    def export_project_rewrite_markdown(self, result: dict) -> str:
        """导出项目优化结果为 Markdown"""
        issues = result.get("original_issues", [])
        improved = result.get("improved_version", "")
        python_ver = result.get("python_backend_version", "")
        agent_ver = result.get("agent_version", "")

        lines = [
            "# 项目经历优化报告",
            "",
            "---",
            "",
            "## 原描述问题",
            "",
        ]
        if issues:
            for issue in issues:
                lines.append(f"- {issue}")
        else:
            lines.append("（无）")

        lines += ["", "---", "", "## 优化版本（通用）", "", improved, ""]
        lines += ["---", "", "## Python 后端导向版本", "", python_ver, ""]
        lines += ["---", "", "## Agent / AI 导向版本", "", agent_ver, ""]
        lines += ["", "---", "", "*由 求职助手 Agent 自动生成*"]

        return "\n".join(lines)
