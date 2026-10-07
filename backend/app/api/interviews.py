"""模拟面试接口。

行为对齐旧版 InterviewHandler，并修复其事务缺陷：
旧版 answer() 的全部写库操作（用户回答/评价/下一题/轮次更新）都在
无提交事务内，session.close() 时被整体回滚，面试过程实际从未持久化。
新版拆为两个事务：读（快照会话与历史）→ LLM 评估（无事务）→ 写。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from app.core.deps import (
    get_conversation_store_service,
    get_interview_simulation_service,
)
from app.core.response import fail, ok
from app.repositories import (
    DatabaseManager,
    InterviewMessageRepository,
    InterviewSessionRepository,
    JobAnalysisRepository,
)
from app.schemas.interview import InterviewAnswerRequest, InterviewStartRequest

router = APIRouter(prefix="/api/v1")


def _read_latest_resume_text(store_service, conversation_id: str) -> str:
    """读取会话中最新上传简历的解析文本。"""
    import os

    documents = store_service.get_conversation_documents(conversation_id)
    if not documents:
        return ""
    latest_doc = documents[-1]
    parsed_text_path = latest_doc.get("parsed_text_path")
    if parsed_text_path and os.path.exists(parsed_text_path):
        with open(parsed_text_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""


@router.post("/interview/start")
def start_interview(payload: InterviewStartRequest) -> dict:
    conversation_id = payload.conversation_id.strip()
    direction = payload.direction.strip() or "general"
    jd_text = payload.jd_text.strip()

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    store_service = get_conversation_store_service()
    store_service.ensure_conversation_exists(conversation_id)

    resume_text = _read_latest_resume_text(store_service, conversation_id)
    if not resume_text:
        documents = store_service.get_conversation_documents(conversation_id)
        if not documents:
            return fail("当前会话未上传简历，请先上传简历", code=400)
        return fail("无法读取简历解析文本", code=500)

    # 如果没有传入 JD，尝试从最新分析中获取
    if not jd_text:
        session = DatabaseManager.get_session()
        try:
            latest_analysis = JobAnalysisRepository.get_latest_by_conversation_id(
                session, conversation_id
            )
            if latest_analysis:
                jd_text = latest_analysis.jd_text
        finally:
            DatabaseManager.remove_session()

    if not jd_text:
        return fail("请先分析 JD 或提供 JD 文本", code=400)

    result = get_interview_simulation_service().start_interview(jd_text, resume_text)
    questions = result.get("questions", [])
    if not questions:
        return fail("面试题生成失败", code=500)

    now = datetime.now()
    session_id = f"iv_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    session = DatabaseManager.get_session()
    try:
        with session.begin():
            InterviewSessionRepository.create(
                session,
                session_id=session_id,
                conversation_id=conversation_id,
                job_role="",
                direction=direction,
                status="in_progress",
                round_count=0,
                initial_questions=json.dumps(questions, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )
            InterviewMessageRepository.create(
                session,
                message_id=f"imsg_{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                role="assistant",
                content=questions[0].get("question", ""),
                score=None,
                evaluation="",
                msg_type="question",
                created_at=now,
            )
    finally:
        DatabaseManager.remove_session()

    return ok(
        {
            "session_id": session_id,
            "questions": questions,
            "current_question": questions[0],
            "question_index": 0,
            "total_questions": len(questions),
        },
        message="面试已开始",
    )


@router.post("/interview/answer")
def answer_interview(payload: InterviewAnswerRequest) -> dict:
    session_id = payload.session_id.strip()
    answer = payload.answer.strip()

    if not session_id:
        return fail("session_id 参数不能为空", code=400)
    if not answer:
        return fail("answer 参数不能为空", code=400)

    # 事务一：读取会话快照与面试历史（读完即释放）
    session = DatabaseManager.get_session()
    try:
        interview_session = InterviewSessionRepository.get_by_session_id(session, session_id)
        if interview_session is None:
            return fail("面试会话不存在", code=404)
        if interview_session.status == "completed":
            return fail("面试已结束", code=400)

        conversation_id = interview_session.conversation_id
        current_round = interview_session.round_count

        messages = InterviewMessageRepository.list_by_session_id(session, session_id)
        current_question = ""
        for msg in reversed(messages):
            if msg.msg_type == "question" and msg.role == "assistant":
                current_question = msg.content
                break
        history = [{"role": msg.role, "content": msg.content} for msg in messages]

        latest_analysis = JobAnalysisRepository.get_latest_by_conversation_id(
            session, conversation_id
        )
        jd_text = latest_analysis.jd_text if latest_analysis else ""
    finally:
        DatabaseManager.remove_session()

    if not current_question:
        return fail("无法找到当前题目", code=500)

    resume_text = _read_latest_resume_text(get_conversation_store_service(), conversation_id)
    round_number = current_round + 1

    # LLM 评估（不占用数据库事务）
    eval_result = get_interview_simulation_service().evaluate_answer(
        jd_text=jd_text,
        resume_text=resume_text or "",
        history=history,
        current_question=current_question,
        user_answer=answer,
        round_number=round_number,
    )

    score = eval_result.get("score", 0)
    evaluation = eval_result.get("evaluation", "")
    next_action = eval_result.get("next_action", "continue")
    follow_up = eval_result.get("follow_up")
    overall_summary = eval_result.get("overall_summary")

    # 事务二：落库（用户回答 + 评价 + 下一题 + 会话状态）
    now = datetime.now()
    session = DatabaseManager.get_session()
    try:
        with session.begin():
            interview_session = InterviewSessionRepository.get_by_session_id(session, session_id)
            InterviewMessageRepository.create(
                session,
                message_id=f"imsg_{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                role="user",
                content=answer,
                score=None,
                evaluation="",
                msg_type="answer",
                created_at=now,
            )
            InterviewMessageRepository.create(
                session,
                message_id=f"imsg_{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                role="assistant",
                content=evaluation,
                score=score,
                evaluation=evaluation,
                msg_type="evaluation",
                created_at=now,
            )

            if next_action == "summary":
                InterviewSessionRepository.update(
                    session,
                    interview_session,
                    status="completed",
                    round_count=round_number,
                    total_score=score,
                    overall_summary=overall_summary or evaluation,
                    updated_at=now,
                )
            else:
                next_question = follow_up or ""
                if next_question:
                    InterviewMessageRepository.create(
                        session,
                        message_id=f"imsg_{uuid.uuid4().hex[:16]}",
                        session_id=session_id,
                        role="assistant",
                        content=next_question,
                        score=None,
                        evaluation="",
                        msg_type="question",
                        created_at=now,
                    )
                InterviewSessionRepository.update(
                    session,
                    interview_session,
                    round_count=round_number,
                    updated_at=now,
                )
    finally:
        DatabaseManager.remove_session()

    if next_action == "summary":
        return ok(
            {
                "session_id": session_id,
                "action": "summary",
                "evaluation": evaluation,
                "score": score,
                "overall_summary": overall_summary or evaluation,
                "question_index": payload.question_index,
                "total_rounds": round_number,
            },
            message="面试结束",
        )

    return ok(
        {
            "session_id": session_id,
            "action": "continue",
            "evaluation": evaluation,
            "score": score,
            "next_question": follow_up or "",
            "question_index": payload.question_index + 1,
            "total_rounds": round_number,
        },
        message="回答已记录",
    )


@router.get("/interview/list")
def list_interviews(conversation_id: str = Query(default="")) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        sessions = InterviewSessionRepository.list_by_conversation_id(session, conversation_id)
        return ok({
            "sessions": [
                {
                    "session_id": s.session_id,
                    "job_role": s.job_role,
                    "direction": s.direction,
                    "status": s.status,
                    "round_count": s.round_count,
                    "total_score": s.total_score,
                    "created_at": s.created_at.isoformat() if s.created_at else "",
                    "updated_at": s.updated_at.isoformat() if s.updated_at else "",
                }
                for s in sessions
            ],
        })
    finally:
        DatabaseManager.remove_session()


@router.get("/interview/detail")
def interview_detail(session_id: str = Query(default="")) -> dict:
    session_id = session_id.strip()
    if not session_id:
        return fail("session_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        interview_session = InterviewSessionRepository.get_by_session_id(session, session_id)
        if interview_session is None:
            return fail("面试会话不存在", code=404)

        messages = InterviewMessageRepository.list_by_session_id(session, session_id)
        return ok({
            "session": {
                "session_id": interview_session.session_id,
                "conversation_id": interview_session.conversation_id,
                "job_role": interview_session.job_role,
                "direction": interview_session.direction,
                "status": interview_session.status,
                "round_count": interview_session.round_count,
                "total_score": interview_session.total_score,
                "overall_summary": interview_session.overall_summary,
                "initial_questions": (
                    json.loads(interview_session.initial_questions)
                    if interview_session.initial_questions
                    else []
                ),
                "created_at": interview_session.created_at.isoformat() if interview_session.created_at else "",
                "updated_at": interview_session.updated_at.isoformat() if interview_session.updated_at else "",
            },
            "messages": [
                {
                    "message_id": msg.message_id,
                    "role": msg.role,
                    "content": msg.content,
                    "score": msg.score,
                    "evaluation": msg.evaluation,
                    "msg_type": msg.msg_type,
                    "created_at": msg.created_at.isoformat() if msg.created_at else "",
                }
                for msg in messages
            ],
        })
    finally:
        DatabaseManager.remove_session()
