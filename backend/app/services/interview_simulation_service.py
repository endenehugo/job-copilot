from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

from langchain_community.chat_models import ChatTongyi
from app.core.llm_factory import build_chat_llm
from app.core.model_scope import current_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

# 与 system prompt 中的约定一致：整场面试最多 8 轮（含初始问题）
MAX_INTERVIEW_ROUNDS = 8

_VALID_ACTIONS = ("continue", "summary")

# JSON 字符串里的反斜杠转义表（\uXXXX 单独处理）
_JSON_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    '"': '"',
    "\\": "\\",
    "/": "/",
}


def extract_partial_string(text: str, key: str) -> str:
    """从**尚不完整**的 JSON 文本里取出某个字符串字段目前已生成的值。

    流式评价要靠这个把 ``{"evaluation": "回答结构清晰……` 这类半截 JSON 变成
    可显示的文字。三个容易踩的点：

    1. 值还没开始时（只到 ``"evaluation":``）返回空串，不要当成"生成了空评价"；
    2. 转义序列可能被切在两个 chunk 之间（末尾正好是 ``\\`` 或 ``\\u12``）——
       这一帧先停在这里，等下一帧补齐，否则会把 ``\\n`` 显示成 ``n``；
    3. 单个 chunk 的末尾通常是半个汉字，所以返回值只用于覆盖式渲染，
       不参与任何"长度差 = 新增片段"的计算。

    找不到该字段、或值不是字符串时返回空串（模型把 evaluation 写成 null 的场景）。
    """

    marker = f'"{key}"'
    found = text.find(marker)
    if found < 0:
        return ""
    colon = text.find(":", found + len(marker))
    if colon < 0:
        return ""

    i = colon + 1
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text) or text[i] != '"':
        return ""
    i += 1

    out: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            if i + 1 >= len(text):
                break  # 半个转义序列，等下一帧
            nxt = text[i + 1]
            if nxt == "u":
                if i + 6 > len(text):
                    break  # \uXXXX 还没收全
                try:
                    out.append(chr(int(text[i + 2 : i + 6], 16)))
                except ValueError:
                    pass  # 非法码点（模型偶发乱码）直接丢弃，别让整段评价消失
                i += 6
                continue
            out.append(_JSON_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            break  # 字段值结束
        out.append(ch)
        i += 1
    return "".join(out)


def _chunk_text(chunk) -> str:
    """取流式 chunk 的文本；多模态块（list content）只拼文本片段。"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""

# 只出第 1 题，不是"只面试一轮"：后续题目由每轮评价时模型根据回答自适应生成
# （follow_up），所以开场造一份 3–6 题的题单从来用不上。
# 实测（关键词在 docs/2026-10-18-模拟面试提速方案.md）：出 3 题带要点 28.5s / 1256 tokens，
# 出 3 题无要点 5.8s，只出 1 题 2.6s。开场等待直接决定"点开始面试后多久看到题目"。
INTERVIEW_START_SYSTEM_PROMPT = """你是一位资深的面试官，擅长根据职位描述（JD）和候选人简历，出针对性的面试题。
请严格分析提供的 JD 和简历，出**第 1 道**面试题，返回 **严格的 JSON**：

- `questions` (list[object]): 面试题列表，**只包含 1 个元素**，每题包含：
  - `question` (str): 面试题目
  - `difficulty` (str): 难度级别，可选 "easy" / "medium" / "hard"
  - `direction` (str): 题目方向，可选 "python_backend" / "agent_ai" / "general"

出题原则：
1. 只有第 1 题：优先问简历里最值得深挖的那个项目，从具体技术决策切入，不要问泛泛的自我评价
2. 题目应落在 JD 的核心技术栈上，与简历项目经验交叉
3. 优先 medium 或 hard，能一层层追问出细节
4. Python 后端方向侧重：Flask/FastAPI、MySQL、Redis、Docker、Linux、系统设计
5. Agent/AI 方向侧重：LLM 原理、RAG、Agent 编排、Prompt Engineering、工具调用
6. 通用方向侧重：项目架构、问题排查、工程化、团队协作

请确保：
1. 输出的内容必须是 **纯 JSON 对象**，不要包含 markdown 代码块标记（```json）、不要额外解释。
2. 题目要具体，不要过于宽泛。
3. 不要在 JSON 之外输出"接下来第 2 题"之类的内容。
"""

INTERVIEW_ANSWER_SYSTEM_PROMPT = """你是一位资深的面试官，正在对候选人进行面试。
你需要根据之前的面试对话、当前题目和候选人的回答，给出评价并继续提问。

请严格分析，返回 **严格的 JSON**，包含以下字段（**请严格按此顺序输出**）：

- `evaluation` (str): 对候选人当前回答的评价（50-150 字），指出优点和不足。
  **这是第一个字段**：前端会边生成边展示，不要在它前面输出其他字段
- `score` (int): 该回答的评分（满分 100 分）
- `next_action` (str): 下一步行动，可选 "continue" / "summary"
  - "continue": 继续提问，给出下一题或追问
  - "summary": 认为面试可以结束，给出整体总结
- `follow_up` (str | null): 如果 next_action 为 "continue"，这里是下一题或需要追问的内容；如果 next_action 为 "summary"，这里为 null
- `overall_summary` (str | null): 如果 next_action 为 "summary"，这里是整体面试总结（包括表现亮点、待加强点、综合建议）；否则为 null

评分标准：
1. 技术准确性（40 分）：概念理解是否正确，技术细节是否到位
2. 表达清晰度（20 分）：回答是否有条理、结构清晰
3. 深度与思考（40 分）：是否有深度思考，能否举一反三

请确保：
1. 输出的内容必须是 **纯 JSON 对象**，不要包含 markdown 代码块标记（```json）、不要额外解释。
2. 评分要客观公正，给建设性反馈。
3. 整场面试最多进行 8 轮（含初始问题），超过后 next_action 应设为 "summary"。
"""


@dataclass
class InterviewSimulationService:
    def start_interview(self, jd_text: str, resume_text: str) -> dict:
        if not jd_text or not jd_text.strip():
            raise ValueError("JD 文本不能为空")
        if not resume_text or not resume_text.strip():
            raise ValueError("简历文本不能为空")

        content = self._invoke_start_llm(jd_text.strip(), resume_text.strip())
        return self._parse_response(content)

    def evaluate_answer(
        self,
        jd_text: str,
        resume_text: str,
        history: list[dict],
        current_question: str,
        user_answer: str,
        round_number: int,
    ) -> dict:
        if not user_answer or not user_answer.strip():
            raise ValueError("回答不能为空")

        content = self._invoke_answer_llm(
            jd_text, resume_text, history, current_question, user_answer, round_number
        )
        return self._parse_response(content)

    def _ensure_start_llm(self) -> ChatTongyi:
        # 模型随请求走（ContextVar 注入，chat Agent 工具链内同样生效）。
        # 出题是"给材料 → 出一道题"，不需要推理链。P0 关掉思考后仍是 20s，
        # 因为瓶颈在输出量：一份 3–6 题带要点的题单有 1300+ tokens，而实际只用到第 1 题。
        # P1 把 prompt 收成单题后约 94 tokens，上限 500 是四倍余量。
        return build_chat_llm(
            model=current_chat_model(),
            temperature=0.5,
            top_p=0.8,
            enable_thinking=False,
            max_tokens=500,
        )

    def _ensure_answer_llm(self, streaming: bool = False) -> ChatTongyi:
        # 单轮评价：关思考 + 封顶。实测 888 tokens/15.8–19.7s → 159 tokens/3.67s，
        # 评价长度与评分档位不变；上限 600 是正常输出（约 250 tokens）的两倍余量，
        # 留太紧会把 JSON 截断成解析失败。
        # streaming=True 时走流式超时档位（settings.llm_stream_timeout），
        # 否则长连接会被 60s 的普通超时掐断。
        return build_chat_llm(
            model=current_chat_model(),
            temperature=0.4,
            top_p=0.7,
            enable_thinking=False,
            max_tokens=600,
            streaming=streaming,
        )


    def _invoke_start_llm(self, jd_text: str, resume_text: str) -> str:
        llm = self._ensure_start_llm()
        user_prompt = (
            f"## JD 原文\n\n{jd_text}\n\n"
            f"## 简历原文\n\n{resume_text}\n\n"
            f"请根据以上 JD 和简历，出第 1 道面试题。"
        )
        messages = [
            SystemMessage(content=INTERVIEW_START_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
        response = llm.invoke(messages)
        return response.content or ""

    def _build_answer_messages(
        self,
        jd_text: str,
        resume_text: str,
        history: list[dict],
        current_question: str,
        user_answer: str,
        round_number: int,
    ) -> list:
        """构造单轮评价的 messages；流式与非流式共用，避免两处 prompt 漂移。"""
        history_text = ""
        for item in history:
            role = item.get("role", "")
            content = item.get("content", "")
            history_text += f"{'面试官' if role == 'assistant' else '候选人'}：{content}\n\n"

        user_prompt = (
            f"## JD 原文\n\n{jd_text}\n\n"
            f"## 简历原文\n\n{resume_text}\n\n"
            f"## 面试历史\n{history_text if history_text else '（无历史）'}\n"
            f"## 当前轮次\n第 {round_number} 轮\n\n"
            f"## 当前题目\n{current_question}\n\n"
            f"## 候选人回答\n{user_answer}\n"
        )
        return [
            SystemMessage(content=INTERVIEW_ANSWER_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

    def _invoke_answer_llm(
        self,
        jd_text: str,
        resume_text: str,
        history: list[dict],
        current_question: str,
        user_answer: str,
        round_number: int,
    ) -> str:
        llm = self._ensure_answer_llm()
        messages = self._build_answer_messages(
            jd_text, resume_text, history, current_question, user_answer, round_number
        )
        response = llm.invoke(messages)
        return response.content or ""

    def stream_evaluate_answer(
        self,
        jd_text: str,
        resume_text: str,
        history: list[dict],
        current_question: str,
        user_answer: str,
        round_number: int,
    ) -> Iterator[tuple[str, object]]:
        """流式评价：边生成边产出 evaluation 的当前全文，最后产出解析后的完整结果。

        产出形如 ``("delta", 文本)`` 与 ``("result", dict)``。delta 发的是
        **覆盖式全文**而不是增量片段：

        - 前端直接替换即可，重复帧、乱序帧都不会让文字错乱；
        - 丢一帧最多少显示几个字，下一帧自动补齐，不需要补偿逻辑；
        - 代价是每帧多几十字节，相对一次 LLM 调用可以忽略。

        为什么不是"评价和下一题分两次调用"：那会把单轮变成串行两次 LLM 往返
        （首字更快但拿到下一题更慢），而一次调用的 JSON 本来就能边出边解析。
        拆成两次留给"题库化"那一档（P2），届时下一题不再需要模型生成。
        """
        llm = self._ensure_answer_llm(streaming=True)
        messages = self._build_answer_messages(
            jd_text, resume_text, history, current_question, user_answer, round_number
        )

        raw_parts: list[str] = []
        shown = ""
        for chunk in llm.stream(messages):
            piece = _chunk_text(chunk)
            if not piece:
                continue
            raw_parts.append(piece)
            current = extract_partial_string("".join(raw_parts), "evaluation")
            # 只往前推进：模型偶发回撤（重复输出同一段）时界面不该倒着写字
            if len(current) > len(shown):
                shown = current
                yield ("delta", shown)

        # 解析失败会抛 ValueError：调用方转成 error 事件，本轮的"预览评价"不落库。
        # 宁可不落库让用户重试，也不要把半截 JSON 猜成评价写进面试记录。
        yield ("result", self._parse_response("".join(raw_parts)))


    def _parse_response(self, content: str) -> dict:
        logger.info("LLM interview raw response (first 500 chars): %s", content[:500])
        return parse_llm_json(content, "面试服务返回格式异常，无法提取结构化结果")


def normalize_interview_evaluation(result: dict) -> tuple[dict, list[str]]:
    """规范化 evaluate_answer 的 LLM 输出，返回 (规范化结果, 告警列表)。

    模型偶发漏字段/类型错乱，直接透传会造成两类静默失败：
    1. 缺 score 时被上层 ``.get("score", 0)`` 兜底成 0 分落库——把"模型没返回"
       说成"你得了 0 分"；这里改为 score=None 并告警。
    2. next_action="continue" 却没有 follow_up——前端拿到空下一题白屏卡死；
       这里转为 summary 让面试正常收尾。
    """
    warnings: list[str] = []

    # score：缺字段/类型错乱一律置 None，不伪造 0 分
    raw_score = result.get("score")
    score: float | None
    if isinstance(raw_score, bool) or raw_score is None:
        score = None
    elif isinstance(raw_score, (int, float)):
        score = float(raw_score)
    elif isinstance(raw_score, str):
        try:
            score = float(raw_score.strip())
            warnings.append("模型返回的评分格式异常，已自动修正")
        except ValueError:
            score = None
    else:
        score = None
    if score is not None:
        score = max(0.0, min(100.0, score))
    else:
        warnings.append("模型未返回有效评分，本轮标记为未评分")

    # next_action：非法值回退 continue（并在无下一题时由下方转为 summary）
    next_action = result.get("next_action")
    if next_action not in _VALID_ACTIONS:
        if next_action is not None:
            warnings.append(f"模型返回了未知的 next_action={next_action!r}，已按继续提问处理")
        next_action = "continue"

    follow_up_raw = result.get("follow_up")
    follow_up = str(follow_up_raw).strip() if follow_up_raw else ""

    if next_action == "continue" and not follow_up:
        next_action = "summary"
        warnings.append("模型未给出下一题，面试提前结束")

    overall_summary_raw = result.get("overall_summary")
    normalized = {
        "evaluation": str(result.get("evaluation") or ""),
        "score": score,
        "next_action": next_action,
        "follow_up": follow_up or None,
        "overall_summary": str(overall_summary_raw).strip() if overall_summary_raw else None,
    }
    return normalized, warnings

