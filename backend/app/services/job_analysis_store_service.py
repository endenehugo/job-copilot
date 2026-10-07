"""JD 分析结果的持久化。

合并旧版 JobHandler._save_analysis 与 ImageHandler.save_analysis 两份重复实现。
合并点：旧 image 版漏掉了"同步更新最新简历版本分数"，统一后两条链路行为一致。
"""

from __future__ import annotations

import json
from datetime import datetime

from app.repositories import DatabaseManager, JobAnalysisRepository, ResumeVersionRepository
from app.repositories.job_analysis_repository import JobAnalysis

_SCORE_FIELD_MAP = {
    "skill_match": "skill_match_score",
    "project_relevance": "project_relevance_score",
    "expression_quality": "expression_quality_score",
    "job_fitness": "job_fitness_score",
}


def save_analysis(conversation_id: str, jd_text: str, jd_analysis: dict, scoring_result: dict) -> None:
    """保存一次 JD 解析 + 简历评分结果，并同步最新简历版本的分数。"""
    now = datetime.now()
    dimensions = scoring_result.get("dimensions", {}) or {}
    total_score = float(scoring_result.get("total_score", 0) or 0)

    session = DatabaseManager.get_session()
    try:
        with session.begin():
            JobAnalysisRepository.create(
                session,
                conversation_id=conversation_id,
                jd_text=jd_text,
                job_role=jd_analysis.get("job_role", ""),
                keywords=json.dumps(jd_analysis.get("keywords", []), ensure_ascii=False),
                requirements=json.dumps(jd_analysis.get("requirements", []), ensure_ascii=False),
                bonus_points=json.dumps(jd_analysis.get("bonus_points", []), ensure_ascii=False),
                total_score=total_score,
                skill_match_score=float(dimensions.get("skill_match", 0) or 0),
                project_relevance_score=float(dimensions.get("project_relevance", 0) or 0),
                expression_quality_score=float(dimensions.get("expression_quality", 0) or 0),
                job_fitness_score=float(dimensions.get("job_fitness", 0) or 0),
                strengths=json.dumps(scoring_result.get("strengths", []), ensure_ascii=False),
                gaps=json.dumps(scoring_result.get("gaps", []), ensure_ascii=False),
                suggestions=json.dumps(scoring_result.get("suggestions", []), ensure_ascii=False),
                created_at=now,
            )

            latest_version = ResumeVersionRepository.get_latest_by_conversation_id(session, conversation_id)
            if latest_version is not None:
                ResumeVersionRepository.update_scores(
                    session, latest_version, total_score=total_score, dimensions=dimensions
                )
    finally:
        DatabaseManager.remove_session()


def analysis_entity_to_dict(entity: JobAnalysis) -> dict:
    """JobAnalysis 实体 → 响应字典（与旧版 JobHandler._entity_to_dict 一致）。"""

    def _safe_json_load(value) -> list:
        if not value:
            return []
        try:
            return json.loads(value) if isinstance(value, str) else value
        except (json.JSONDecodeError, TypeError):
            return []

    return {
        "id": entity.id,
        "conversation_id": entity.conversation_id,
        "job_role": entity.job_role,
        "total_score": entity.total_score,
        "dimensions": {
            "skill_match": entity.skill_match_score,
            "project_relevance": entity.project_relevance_score,
            "expression_quality": entity.expression_quality_score,
            "job_fitness": entity.job_fitness_score,
        },
        "keywords": _safe_json_load(entity.keywords),
        "requirements": _safe_json_load(entity.requirements),
        "bonus_points": _safe_json_load(entity.bonus_points),
        "strengths": _safe_json_load(entity.strengths),
        "gaps": _safe_json_load(entity.gaps),
        "suggestions": _safe_json_load(entity.suggestions),
        "created_at": entity.created_at.isoformat() if entity.created_at else "",
    }


def build_analysis_response(jd_analysis: dict, scoring_result: dict) -> dict:
    """组装 /job/analyze 的响应 data（与旧版 JobHandler._build_response 一致）。"""
    return {
        "jd_analysis": jd_analysis,
        "scoring": {
            "total_score": scoring_result.get("total_score", 0),
            "dimensions": scoring_result.get("dimensions", {}),
            "strengths": scoring_result.get("strengths", []),
            "gaps": scoring_result.get("gaps", []),
            "suggestions": scoring_result.get("suggestions", []),
        },
    }
