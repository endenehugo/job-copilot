"""面试题库（P2）：本场题单的存取、游标推进，以及"下一题问什么"的决策。

题目存在 ``interview_sessions.initial_questions``（这一列本来就是"本场题单"），
P2 起改成带版本的包装结构，同时兼容 P1 及更早写入的纯 list：

    {"v": 2, "state": "pending|ready|failed", "cursor": 1, "questions": [...]}

``cursor`` 是"已经出过的题数"，``questions[cursor]`` 就是下一题。追加题目不动
cursor，所以"后台补题"和"作答推进游标"看起来互不冲突；但两者都会读改写**同一列**，
并发时会互相覆盖（丢题或游标回退），因此所有读写都要过 ``bank_lock``。

当前部署是单进程 uvicorn，进程内锁就够。将来上多 worker 时要换成数据库行锁
或独立的题目表——那时候这层锁会静默失效（不会报错，只会偶发丢题）。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Callable

BANK_VERSION = 2

# 评分低于该阈值时，跳过题库、针对这次回答追问。题库题是按 JD/简历预生成的，
# 不知道候选人答得怎么样；答崩了还换新题等于放过这个薄弱点，追问才有价值。
FOLLOW_UP_SCORE_THRESHOLD = 60

# 回答过短（敷衍/没说清）同样触发追问，避免"字数不够但关键词踩中"骗过阈值
SHORT_ANSWER_CHARS = 20

# 读改写 initial_questions 的互斥锁。见模块 docstring 里对单进程前提的说明。
bank_lock = threading.RLock()


@dataclass
class QuestionBank:
    questions: list[dict] = field(default_factory=list)
    cursor: int = 0
    # pending：后台正在补题；ready：题单已完整；failed：补题失败（不会再补）
    state: str = "ready"

    @property
    def next_question(self) -> dict | None:
        """下一道未出的题；题库用尽或还没补出来时返回 None。"""
        if 0 <= self.cursor < len(self.questions):
            return self.questions[self.cursor]
        return None

    @property
    def next_question_text(self) -> str:
        question = self.next_question or {}
        return str(question.get("question") or "").strip()

    @property
    def remaining(self) -> int:
        return max(0, len(self.questions) - self.cursor)


def parse_bank(raw: str | None) -> QuestionBank:
    """把列里的 JSON 解析成 QuestionBank；任何异常都退化成空题库。

    兼容三种历史形态：空值、P1 及更早的纯题单（list）、P2 的包装对象（dict）。
    纯题单的 cursor 取 len（视为"全部已出过"），这样老场次继续走追问兜底，
    行为与升级前一致，不会突然重问一遍老题。
    """
    if not raw:
        return QuestionBank([], 0, "ready")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return QuestionBank([], 0, "ready")

    if isinstance(data, list):
        questions = [q for q in data if isinstance(q, dict)]
        return QuestionBank(questions, len(questions), "ready")
    if not isinstance(data, dict):
        return QuestionBank([], 0, "ready")

    questions = [q for q in (data.get("questions") or []) if isinstance(q, dict)]
    cursor = data.get("cursor")
    if not isinstance(cursor, int) or isinstance(cursor, bool) or not 0 <= cursor <= len(questions):
        # 游标坏了比丢题更危险：越界会静默重问已经出过的题，宁可当作"已全部出过"
        cursor = len(questions)
    state = data.get("state")
    if state not in ("pending", "ready", "failed"):
        state = "ready"
    return QuestionBank(questions, cursor, state)


def dump_bank(bank: QuestionBank) -> str:
    return json.dumps(
        {
            "v": BANK_VERSION,
            "state": bank.state,
            "cursor": bank.cursor,
            "questions": bank.questions,
        },
        ensure_ascii=False,
    )


@dataclass
class NextQuestion:
    """本轮之后问什么。question 为空表示这一轮就是最后一轮。"""

    question: str = ""
    # bank：按题库顺序出题；follow_up：针对本次回答追问；legacy：模型在评价里自带的下一题
    source: str = "none"
    # 题目取自题库时的下标；追问不推进游标，所以为 None
    bank_index: int | None = None
    warnings: list[str] = field(default_factory=list)
    # 追问的触发原因（"评分 55 低于 60"/"回答过短"），只做诊断用，不进用户告警
    reason: str = ""


def needs_follow_up(score: float | None, answer: str) -> tuple[bool, str]:
    """判断是否该跳过题库、针对这次回答追问。返回 (是否追问, 原因)。"""
    if score is None:
        # 未评分说明模型没法判断这次回答，追问一次比换题更有信息量
        return True, "本轮未评分"
    if score < FOLLOW_UP_SCORE_THRESHOLD:
        return True, f"评分 {score:g} 低于 {FOLLOW_UP_SCORE_THRESHOLD}"
    if len((answer or "").strip()) < SHORT_ANSWER_CHARS:
        return True, "回答过短"
    return False, ""


def _try_follow_up(factory: Callable[[], str] | None, warnings: list[str]) -> str:
    if factory is None:
        return ""
    try:
        return (factory() or "").strip()
    except Exception as exc:  # noqa: BLE001 - 追问是可选的增强，失败必须能降级
        # 追问失败不能连累整轮：题库里还有题就换题，没有就退回模型给的下一题
        warnings.append(f"追问生成失败（{exc}），已改用题库题目")
        return ""


def plan_next_question(
    bank: QuestionBank,
    *,
    score: float | None,
    answer: str,
    eval_follow_up: str = "",
    follow_up_factory: Callable[[], str] | None = None,
) -> NextQuestion:
    """决定本轮之后的下一题：低分追问 → 题库顺序 → 现场生成 → 模型自带的下一题。

    这个顺序就是 P2 的产品定义：题库保证"题面和难度可预期"，追问保证"答崩了会被
    追根问底"。两者都没有时（题库用尽、追问生成也失败）才退到模型在评价里顺手
    给的 follow_up——P1 及更早的场次走的就是这条路，所以老场次行为不变。
    """
    warnings: list[str] = []
    escalate, reason = needs_follow_up(score, answer)
    tried = False

    if escalate and follow_up_factory is not None:
        tried = True
        question = _try_follow_up(follow_up_factory, warnings)
        if question:
            # 追问不推进题库游标：这一题是"绕道"，之后仍回到题库的正常顺序
            return NextQuestion(question, "follow_up", None, warnings, reason)

    bank_question = bank.next_question_text
    if bank_question:
        return NextQuestion(bank_question, "bank", bank.cursor, warnings)

    if not tried and follow_up_factory is not None:
        question = _try_follow_up(follow_up_factory, warnings)
        if question:
            return NextQuestion(question, "follow_up", None, warnings)

    legacy = (eval_follow_up or "").strip()
    if legacy:
        return NextQuestion(legacy, "legacy", None, warnings)

    return NextQuestion("", "none", None, warnings)
