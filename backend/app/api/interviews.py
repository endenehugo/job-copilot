"""模拟面试接口。

行为对齐旧版 InterviewHandler，并修复其事务缺陷：
旧版 answer() 的全部写库操作（用户回答/评价/下一题/轮次更新）都在
无提交事务内，session.close() 时被整体回滚，面试过程实际从未持久化。
新版拆为两个事务：读（快照会话与历史）→ LLM 评估（无事务）→ 写。
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core.deps import (
    get_conversation_store_service,
    get_interview_simulation_service,
)
from app.core.model_scope import request_model_scope
from app.core.ownership import (
    assert_child_owner,
    assert_conversation_owner,
    check_owner,
    get_owner_key,
    require_writable,
)
from app.core.response import fail, ok
from app.core.security import get_current_role
from app.repositories import (
    DatabaseManager,
    InterviewMessage,
    InterviewMessageRepository,
    InterviewSessionRepository,
    JobAnalysisRepository,
)
from app.schemas.interview import (
    InterviewAnswerRequest,
    InterviewDeleteRequest,
    InterviewStartRequest,
)
from app.services.interview_simulation_service import (
    MAX_INTERVIEW_ROUNDS,
    normalize_interview_evaluation,
)
from app.services.question_bank import (
    NextQuestion,
    QuestionBank,
    bank_lock,
    dump_bank,
    parse_bank,
    plan_next_question,
)

router = APIRouter(prefix="/api/v1")

logger = logging.getLogger(__name__)

# 开场只生成 1 题（用户等着看题），剩下的在后台补进题库。
# 3 题约 5.8s，正好落在"用户读完第 1 题并开始打字"的窗口里，前台完全感知不到。
PREFETCH_QUESTIONS = 3


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
def start_interview(
    payload: InterviewStartRequest,
    background_tasks: BackgroundTasks,
    role: str = Depends(get_current_role),
    owner_key: str = Depends(get_owner_key),
) -> dict:
    conversation_id = payload.conversation_id.strip()
    direction = payload.direction.strip() or "general"
    jd_text = payload.jd_text.strip()

    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    store_service = get_conversation_store_service()
    store_service.ensure_conversation_writable(conversation_id, owner_key=owner_key)

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
        # 报错要能直接照着做：说清楚 JD 从哪来（上传的文档一律按简历处理，
        # 每个上传都会生成一条简历版本，因此 JD 只能以文本形式提供）
        documents = store_service.get_conversation_documents(conversation_id)
        hint = "请先在「JD 分析」页粘贴岗位描述并开始分析"
        if len(documents) >= 2:
            hint += "（上传的 PDF/Word 都按简历处理，JD 需要用文本粘贴）"
        return fail(f"当前会话还没有可用的 JD：{hint}，或在发起面试时直接提供 JD 文本", code=400)

    try:
        with request_model_scope(payload.model, role):
            result = get_interview_simulation_service().start_interview(jd_text, resume_text)
    except ValueError as exc:
        return fail(str(exc), code=400)
    questions = result.get("questions", [])
    if not questions:
        return fail("面试题生成失败", code=500)

    now = datetime.now()
    session_id = f"iv_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # 开场这 1 题立刻发出，其余题目放进题库由后台补齐（见 _prefetch_question_bank）。
    # cursor=1 表示"已经出过 1 题"，下一轮从 questions[1] 取。
    bank = QuestionBank(questions=questions, cursor=1, state="pending")

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
                initial_questions=dump_bank(bank),
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

    # 后台补题：不阻塞本次响应。失败只是题库停留在 pending/failed，
    # 作答时自动退回"现场生成追问"，面试不会卡住。
    background_tasks.add_task(
        _prefetch_question_bank,
        session_id,
        jd_text,
        resume_text,
        payload.model,
        role,
        [str(q.get("question") or "") for q in questions],
    )

    return ok(
        {
            "session_id": session_id,
            "questions": questions,
            "current_question": questions[0],
            "question_index": 0,
            "total_questions": len(questions),
            "bank_state": bank.state,
        },
        message="面试已开始",
    )


def _update_bank(session_id: str, mutate) -> bool:
    """在锁内"读整列 → 改 → 写回"，供后台补题与游标推进共用。

    读必须发生在写事务里：后台补题与作答落库都会重写 initial_questions，
    用请求早先读到的快照写回去会把对方刚写的内容抹掉（丢题或游标回退）。
    """
    with bank_lock:
        session = DatabaseManager.get_session()
        try:
            with session.begin():
                entity = InterviewSessionRepository.get_by_session_id(session, session_id)
                if entity is None:
                    return False
                bank = parse_bank(entity.initial_questions)
                mutate(bank)
                InterviewSessionRepository.update(
                    session, entity, initial_questions=dump_bank(bank)
                )
                return True
        finally:
            DatabaseManager.remove_session()


def _append_bank_questions(session_id: str, extra: list[dict]) -> bool:
    def mutate(bank: QuestionBank) -> None:
        seen = {str(q.get("question") or "").strip() for q in bank.questions}
        for item in extra:
            text = str(item.get("question") or "").strip()
            if text and text not in seen:
                bank.questions.append(item)
                seen.add(text)
        bank.state = "ready"

    return _update_bank(session_id, mutate)


def _mark_bank_state(session_id: str, state: str) -> bool:
    def mutate(bank: QuestionBank) -> None:
        # 已经补齐过就别再改回 failed：补题失败只影响"还能不能再问一道"，
        # 把 ready 改成 failed 会让剩余题目白丢。
        if bank.state != "ready":
            bank.state = state

    return _update_bank(session_id, mutate)


def _prefetch_question_bank(
    session_id: str,
    jd_text: str,
    resume_text: str,
    model: str,
    role: str,
    asked: list[str],
) -> None:
    """后台补齐题库：开场只出 1 题，剩下的 PREFETCH_QUESTIONS 道在这里补。

    BackgroundTasks 在响应发出之后才执行（同步函数进线程池），所以这段耗时用户
    完全看不到。三种需要兜住的情况：

    - 补题失败（额度/超时/JSON 坏）：把状态标成 failed，作答时退回现场追问；
    - 会话已被删除或已完成：`_update_bank` 找不到行，直接放弃；
    - 用户手快，第一题期间就作答推进了游标：`_update_bank` 在锁内重读，
      只追加题目、不动 cursor，所以不会把游标写回去。
    """
    try:
        with request_model_scope(model, role):
            extra = get_interview_simulation_service().generate_questions(
                jd_text, resume_text, count=PREFETCH_QUESTIONS, exclude=asked
            )
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给已结束的响应
        logger.warning("后台补题失败，本场将改用现场追问: %s", exc)
        _mark_bank_state(session_id, "failed")
        return

    if not extra:
        logger.warning("后台补题返回空题单，本场将改用现场追问")
        _mark_bank_state(session_id, "failed")
        return

    _append_bank_questions(session_id, extra)


class _InterviewFlowError(Exception):
    """接口内的业务错误：由端点捕获后转成统一信封（流式端点则转成 error 事件）。"""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


def _load_answer_context(session_id: str, owner_key: str) -> dict:
    """事务一：读会话快照与面试历史，读完即释放（LLM 调用不能占着连接）。

    抽成独立函数是为了让流式端点复用同一套校验：会话是否存在、是否已结束、
    当前题目是什么，两边必须完全一致，否则会出现"非流式能答、流式报错"的分裂。
    """
    session = DatabaseManager.get_session()
    try:
        interview_session = InterviewSessionRepository.get_by_session_id(session, session_id)
        if interview_session is None:
            raise _InterviewFlowError("面试会话不存在", 404)
        if interview_session.status == "completed":
            raise _InterviewFlowError("面试已结束", 400)

        conversation_id = interview_session.conversation_id
        # 第三层授权：session_id 直取子表，必须回查父会话。
        # 作答是写操作（要落消息与轮次），所以要求可写
        require_writable(
            assert_child_owner(session, conversation_id, owner_key, "面试会话不存在"),
            owner_key,
        )
        current_round = interview_session.round_count
        # 题库快照：只用于"这一轮该问什么"的决策。真正的游标推进在落库事务里
        # 用锁内重读的值，避免和后台补题互相覆盖。
        bank = parse_bank(interview_session.initial_questions)

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
        raise _InterviewFlowError("无法找到当前题目", 500)

    return {
        "conversation_id": conversation_id,
        "current_round": current_round,
        "current_question": current_question,
        "history": history,
        "jd_text": jd_text,
        "bank": bank,
        "resume_text": _read_latest_resume_text(get_conversation_store_service(), conversation_id),
    }


def _persist_answer(
    *,
    session_id: str,
    answer: str,
    question_index: int,
    round_number: int,
    eval_result: dict,
    eval_warnings: list[str],
    next_question: str = "",
    bank_index: int | None = None,
) -> dict:
    """事务二：落库（用户回答 + 评价 + 下一题 + 题库游标 + 会话状态）并构造响应 data。

    同样被两个端点共用：轮次上限兜底、status 流转、question_index 推进
    只有一处实现，就不会出现"流式落库少了 next_question"这类非对称 bug。
    """
    score = eval_result.get("score")
    evaluation = eval_result.get("evaluation", "")
    next_action = eval_result.get("next_action", "continue")
    overall_summary = eval_result.get("overall_summary")

    # 下一题由调用方（题库/追问）决定；调用方没给时退回模型在评价里带的 follow_up，
    # 这样 P1 及更早的老场次行为不变。
    next_question = (next_question or "").strip() or str(eval_result.get("follow_up") or "").strip()

    # 硬性轮次上限：模型未按约定收尾时兜底结束，避免无限追问
    if next_action != "summary" and round_number >= MAX_INTERVIEW_ROUNDS:
        next_action = "summary"
        eval_warnings.append(f"已达最大轮数（{MAX_INTERVIEW_ROUNDS}），面试自动结束")
    elif next_action != "summary" and not next_question:
        # 题库用尽 + 追问没生成出来：继续只会给前端一个空题面，直接正常收尾
        next_action = "summary"
        eval_warnings.append("没有可用的下一题（题库已用尽且追问未生成），面试提前结束")

    now = datetime.now()
    # 锁包住整个写事务：题库游标与后台补题都会重写 initial_questions
    with bank_lock:
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
                    update_fields = {"round_count": round_number, "updated_at": now}
                    if bank_index is not None:
                        # 题目取自题库：把游标推到它后面。这里必须用锁内重读的题库
                        # （上面刚读过这一行），否则会把后台补题的追加写回去。
                        fresh = parse_bank(interview_session.initial_questions)
                        if bank_index >= fresh.cursor:
                            fresh.cursor = bank_index + 1
                            update_fields["initial_questions"] = dump_bank(fresh)
                    InterviewSessionRepository.update(session, interview_session, **update_fields)
        finally:
            DatabaseManager.remove_session()

    if next_action == "summary":
        return {
            "session_id": session_id,
            "action": "summary",
            "evaluation": evaluation,
            "score": score,
            "overall_summary": overall_summary or evaluation,
            "question_index": question_index,
            "total_rounds": round_number,
        }

    return {
        "session_id": session_id,
        "action": "continue",
        "evaluation": evaluation,
        "score": score,
        "next_question": next_question,
        "question_index": question_index + 1,
        "total_rounds": round_number,
    }


def _plan_next(
    answer_ctx: dict,
    *,
    eval_result: dict,
    answer: str,
    round_number: int,
    model: str,
    role: str,
) -> NextQuestion:
    """决定本轮之后问什么：低分追问 → 题库顺序 → 现场生成 → 老逻辑的 follow_up。

    追问是一次额外的 LLM 调用（约 1–2s），只在这个人答崩了的时候才花；
    正常轮次走题库，几乎是零成本——这正是 P2 想要的形态。
    """
    if round_number >= MAX_INTERVIEW_ROUNDS:
        # 这一轮之后就收尾，没必要为"不会用到的下一题"再花一次模型调用
        return NextQuestion()

    def _follow_up() -> str:
        with request_model_scope(model, role):
            return get_interview_simulation_service().generate_follow_up(
                jd_text=answer_ctx["jd_text"],
                resume_text=answer_ctx["resume_text"] or "",
                history=answer_ctx["history"],
                current_question=answer_ctx["current_question"],
                user_answer=answer,
                round_number=round_number,
            )

    return plan_next_question(
        answer_ctx["bank"],
        score=eval_result.get("score"),
        answer=answer,
        eval_follow_up=str(eval_result.get("follow_up") or ""),
        follow_up_factory=_follow_up,
    )


@router.post("/interview/answer")
def answer_interview(
    payload: InterviewAnswerRequest,
    role: str = Depends(get_current_role),
    owner_key: str = Depends(get_owner_key),
) -> dict:
    session_id = payload.session_id.strip()
    answer = payload.answer.strip()

    if not session_id:
        return fail("session_id 参数不能为空", code=400)
    if not answer:
        return fail("answer 参数不能为空", code=400)

    try:
        ctx = _load_answer_context(session_id, owner_key)
    except _InterviewFlowError as exc:
        return fail(exc.message, code=exc.code)

    round_number = ctx["current_round"] + 1

    # LLM 评估（不占用数据库事务）
    try:
        with request_model_scope(payload.model, role):
            eval_result = get_interview_simulation_service().evaluate_answer(
                jd_text=ctx["jd_text"],
                resume_text=ctx["resume_text"] or "",
                history=ctx["history"],
                current_question=ctx["current_question"],
                user_answer=answer,
                round_number=round_number,
            )
            eval_result, eval_warnings = normalize_interview_evaluation(eval_result)
            plan = _plan_next(
                ctx,
                eval_result=eval_result,
                answer=answer,
                round_number=round_number,
                model=payload.model,
                role=role,
            )
    except ValueError as exc:
        return fail(str(exc), code=400)
    eval_warnings.extend(plan.warnings)

    data = _persist_answer(
        session_id=session_id,
        answer=answer,
        question_index=payload.question_index,
        round_number=round_number,
        eval_result=eval_result,
        eval_warnings=eval_warnings,
        next_question=plan.question,
        bank_index=plan.bank_index,
    )
    data["question_source"] = plan.source
    is_summary = data["action"] == "summary"
    return ok(
        data,
        message="面试结束" if is_summary else "回答已记录",
        warnings=eval_warnings or None,
    )



def _next_or_none(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None


@router.post("/interview/answer/stream")
async def answer_interview_stream(
    payload: InterviewAnswerRequest,
    role: str = Depends(get_current_role),
    owner_key: str = Depends(get_owner_key),
):
    """流式作答：评价边生成边下发，题目生成完再补一条。

    事件序列：`evaluation`（覆盖式全文，可多次）→ `score` → `question`（可选）→ `done`。

    与非流式端点共用 `_load_answer_context` / `_persist_answer`，
    所以校验、轮次上限、落库行为完全一致，区别只在"什么时候让用户看到评价"。
    参数校验失败发生在流开始**之前**，返回的仍是普通 JSON 信封，前端能直接弹文案。
    """
    session_id = payload.session_id.strip()
    answer = payload.answer.strip()

    if not session_id:
        return fail("session_id 参数不能为空", code=400)
    if not answer:
        return fail("answer 参数不能为空", code=400)

    try:
        answer_ctx = _load_answer_context(session_id, owner_key)
    except _InterviewFlowError as exc:
        return fail(exc.message, code=exc.code)

    round_number = answer_ctx["current_round"] + 1

    def sse(event: str, data) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def generate():
        # 模型作用域必须开在生成器内部：端点在返回 StreamingResponse 后就退出了，
        # 在外面 with 一下等于没生效。run_in_threadpool 会拷贝当前 context，
        # 所以每次 LLM 迭代都能读到本次选型。
        try:
            with request_model_scope(payload.model, role):
                iterator = get_interview_simulation_service().stream_evaluate_answer(
                    jd_text=answer_ctx["jd_text"],
                    resume_text=answer_ctx["resume_text"] or "",
                    history=answer_ctx["history"],
                    current_question=answer_ctx["current_question"],
                    user_answer=answer,
                    round_number=round_number,
                )
                # 同步生成器必须固定在同一个 context 里驱动：
                # starlette 的 iterate_in_threadpool 每次 __next__ 都拷贝新 context，
                # ContextVar 的 token 无法在后续迭代里复位（见 core/model_scope.py）。
                iter_ctx = contextvars.copy_context()
                raw_result: dict | None = None
                while True:
                    item = await run_in_threadpool(iter_ctx.run, _next_or_none, iterator)
                    if item is None:
                        break
                    kind, data = item
                    if kind == "delta":
                        yield sse("evaluation", {"text": data})
                    else:
                        raw_result = data

                if raw_result is None:
                    # 模型一个字都没吐出来（连 error 都没抛），别让前端拿到空评价
                    yield sse("error", {"message": "评价生成失败，请重试"})
                    return

                eval_result, eval_warnings = normalize_interview_evaluation(raw_result)
                yield sse(
                    "score",
                    {
                        "score": eval_result.get("score"),
                        "action": eval_result.get("next_action", "continue"),
                    },
                )

                # 下一题：正常轮次直接命中题库（毫秒级），只有答崩了才现场追问（1–2s）。
                # 放在 score 之后，所以用户先看到评价和分数，再等题目。
                plan = await run_in_threadpool(
                    _plan_next,
                    answer_ctx,
                    eval_result=eval_result,
                    answer=answer,
                    round_number=round_number,
                    model=payload.model,
                    role=role,
                )
                eval_warnings.extend(plan.warnings)

                result = await run_in_threadpool(
                    _persist_answer,
                    session_id=session_id,
                    answer=answer,
                    question_index=payload.question_index,
                    round_number=round_number,
                    eval_result=eval_result,
                    eval_warnings=eval_warnings,
                    next_question=plan.question,
                    bank_index=plan.bank_index,
                )
                result["question_source"] = plan.source
                if result["action"] == "continue" and result.get("next_question"):
                    yield sse(
                        "question",
                        {"question": result["next_question"], "source": plan.source},
                    )
                # warnings 要在落库之后再取：_persist_answer 可能追加"已达最大轮数"
                yield sse("done", {**result, "warnings": eval_warnings or None})
        except ValueError as exc:
            # 解析失败时本轮不落库：用户看到的评价只是预览，重试一次即可，
            # 比把半截 JSON 猜成评价写进面试记录更诚实。
            yield sse("error", {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 流内异常只能靠事件回传
            logger.exception("流式面试作答失败")
            yield sse("error", {"message": f"服务器内部错误: {exc}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/interview/delete")
def delete_interview(
    payload: InterviewDeleteRequest, owner_key: str = Depends(get_owner_key)
) -> dict:
    """删除单场面试（场次 + 全部问答消息），供会话树子节点操作。"""
    from sqlalchemy import delete as sql_delete

    session_id = payload.session_id.strip()
    if not session_id:
        return fail("session_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        with session.begin():
            interview_session = InterviewSessionRepository.get_by_session_id(session, session_id)
            if interview_session is None:
                return fail("面试会话不存在", code=404)
            # 第三层：按 session_id 直取，回查父会话（事务内，用 check_owner）
            require_writable(
                assert_child_owner(
                    session, interview_session.conversation_id, owner_key, "面试会话不存在"
                ),
                owner_key,
            )
            session.execute(
                sql_delete(InterviewMessage).where(InterviewMessage.session_id == session_id)
            )
            session.delete(interview_session)
    finally:
        DatabaseManager.remove_session()
    return ok({"session_id": session_id}, message="面试记录已删除")


@router.get("/interview/list")
def list_interviews(
    conversation_id: str = Query(default=""), owner_key: str = Depends(get_owner_key)
) -> dict:
    conversation_id = conversation_id.strip()
    if not conversation_id:
        return fail("conversation_id 参数不能为空", code=400)

    assert_conversation_owner(conversation_id, owner_key)

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
def interview_detail(
    session_id: str = Query(default=""), owner_key: str = Depends(get_owner_key)
) -> dict:
    session_id = session_id.strip()
    if not session_id:
        return fail("session_id 参数不能为空", code=400)

    session = DatabaseManager.get_session()
    try:
        interview_session = InterviewSessionRepository.get_by_session_id(session, session_id)
        if interview_session is None:
            return fail("面试会话不存在", code=404)
        # 第三层：session_id 直取，回查父会话（只读场景，不要求可写）。
        # 越权也回「面试会话不存在」，与上面 entity is None 的分支保持一致
        assert_child_owner(session, interview_session.conversation_id, owner_key, "面试会话不存在")

        messages = InterviewMessageRepository.list_by_session_id(session, session_id)
        bank = parse_bank(interview_session.initial_questions)
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
                # 题单：老场次是纯 list，P2 起是题库包装里的 questions，这里统一成 list
                "initial_questions": bank.questions,
                # 题库状态：pending 表示后台还在补题（此时作答会退回现场追问）
                "question_bank": {
                    "size": len(bank.questions),
                    "cursor": bank.cursor,
                    "state": bank.state,
                },
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
