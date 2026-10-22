"""自动简历生成：make-resume skill 规范 + 结构化抽取 + 固定模板渲染。

设计取舍（2026-10-18，详见 docs/2026-10-18-自动简历生成模块方案.md）：

1. **skill 只取规范，不实现它的流程。** SKILL.md 要求读 `assets/asu-resume/`、
   跑 `scripts/build-asu-resume.mjs`、调 `scripts/export-resume-pdf.mjs`，
   这些资产与脚本在本机与上游仓库都不存在（文档版本比资产新）。所以这里只按二级标题
   切出「输入清单 / 缺失字段补问规则 / 默认排版」等规范文本作为 prompt 来源，
   版式则由 resources/resume_templates/asu_resume.html.j2 固化。
   整篇 SKILL.md 不塞进 prompt：其中大半在讲 node 脚本与 18 套模板矩阵，是噪音，
   还会诱导模型输出"我将执行构建脚本"或反过来索要 assets。

2. **不让模型直出 HTML。** 抽取成结构化 JSON（约 1.5k token）再渲染，
   相比直出整份 HTML（15–25k token）截断风险低一个量级，且改字段可零 LLM 重渲染。

3. **不使用项目改写结果（project_rewrites）作为素材。** 2026-10-18 实测该服务的提示词
   会把「90%+」编成「78%→92%」、凭空加 Docling；把它喂进来等于把污染带进简历。
   素材只取会话内已解析的简历原文，JD 仅用于决定求职方向与详略顺序。

4. **审计用确定性规则而非再调一次模型。** 逐个数字回查原文，找不到出处就报警——
   这比让模型自查更可靠、零成本、可写单测。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.json_utils import parse_llm_json
from app.core.llm_factory import build_chat_llm
from app.core.model_scope import current_chat_model
from app.services.generated_file_service import register_generated_file
from app.repositories import (
    DatabaseManager,
    DocumentRepository,
    JobAnalysisRepository,
    ResumeGeneration,
    ResumeGenerationRepository,
)
from app.utils import ResourceUtils, skill_loader

logger = logging.getLogger(__name__)

SKILL_NAME = "make-resume"
TEMPLATE_SUBDIR = "resume_templates"
TEMPLATE_NAME = "asu_resume.html.j2"
OUTPUT_SUBDIR = "generated_docs"

# 白名单小节：只有这些对"服务端确定性流水线"有意义。
# 刻意排除「模板与资源」「工作流程」「PDF 导出验收标准」「LaTeX 输出」——
# 它们讲的是 agent 在本机跑脚本的流程，后端没有也不该有这些能力。
_SKILL_SECTIONS = ("输入清单", "缺失字段补问规则", "默认排版")
_REFERENCE_SECTIONS = ("页面结构", "视觉参数", "内容规则")

# 素材上限：简历正文取 12000 字（够一份双页简历），JD 摘要取 3000 字
_MAX_RESUME_CHARS = 12_000
_MAX_JD_CHARS = 3_000
# "没有上传文档"时改用对话口述材料的最低字数：低于这个值只可能是闲聊，
# 拿它去抽取只会生成一份空简历。
_MIN_SPEECH_CHARS = 50

_YEAR_PATTERN = re.compile(r"^(19|20)\d{2}$")
_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_PENDING_PATTERN = re.compile(r"【待补[^】]*】")
# 学校名里不会出现这些学历词；出现了说明模型把身份行抄进了 school 字段
_DEGREE_WORD_PATTERN = re.compile(r"(本科|硕士|博士|专科|大专|研究生|毕业|应届|届)")
# JD 匹配缺口的关键词，用于把这类条目从 missing 里剔掉
_JD_NOISE_PATTERN = re.compile(r"(JD|岗位要求|硬性要求|是否(具备|掌握|熟悉)|年限)")
# 文件名只允许 ASCII：既挡路径穿越，也避免中文文件名在下载 URL 里要额外转义
_SAFE_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")

_MAX_WARNINGS = 12


def extract_sections(markdown: str, titles: tuple[str, ...]) -> str:
    """按二级标题从 SKILL.md / reference 中切出白名单小节。

    找不到任何小节时返回空串（调用方据此判断 skill 结构是否变了），
    不抛异常——skill 是外部文件，写法变动不该让整个接口 500。
    """
    if not markdown:
        return ""
    wanted = set(titles)
    chunks: list[str] = []
    current_title: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current_title in wanted and buffer:
            chunks.append(f"## {current_title}\n" + "\n".join(buffer).strip())

    for line in markdown.splitlines():
        if line.startswith("## "):
            flush()
            current_title = line[3:].strip()
            buffer = []
        elif line.startswith("# "):
            # 一级标题（技能名）只重置状态，本身不是可切分的小节
            flush()
            current_title = None
            buffer = []
        else:
            buffer.append(line)
    flush()
    return "\n\n".join(c for c in chunks if c.strip())


def build_skill_spec() -> str:
    """读取 make-resume 的白名单规范文本。

    每次调用都读文件（两个文件合计约 10KB），让 skill 更新后 prompt 自动跟随，
    避免用缓存导致"改了 skill 但行为没变"的假象。
    """
    try:
        guide = skill_loader.load_skill(SKILL_NAME)
    except (FileNotFoundError, ValueError):
        logger.warning("make-resume skill 缺失，简历生成将退化为无规范模式")
        return ""

    spec = extract_sections(guide, _SKILL_SECTIONS)

    try:
        reference = skill_loader.load_reference(
            SKILL_NAME, "references/default-resume-template.md"
        )
        spec += "\n\n" + extract_sections(reference, _REFERENCE_SECTIONS)
    except (FileNotFoundError, ValueError):
        logger.warning("make-resume 缺少 default-resume-template.md，只使用 SKILL.md 规范")

    return spec.strip()


_EXTRACT_SYSTEM_PROMPT = """你是一位严谨的中文技术简历信息抽取器。你的唯一任务是把素材整理成结构化 JSON，
供固定模板渲染成 A4 简历。你不负责"美化"，只负责"如实归档"。

## 简历制作规范（来自内置 make-resume 技能）
{spec}

## 硬约束（违反即视为失败）

1. **身份字段只能逐字来自「简历原文」**：姓名、电话、邮箱、城市、学校、公司、职位、
   起止时间。取不到就填 null 并写进 `missing`，**绝不允许推测、补全或改写**。
2. **每个 bullet 必须给出处**：`basis` 取值 `resume`（原句改写）或 `user`（用户本次补充），
   `source_span` 填原文中支撑该条的原句片段（10–60 字，必须能在原文里原样找到）。
3. **不得新增原文没有的数字、指标、技术栈、职级、团队规模**。原文写"准确率 90%+"就保留
   "90%+"，不许改成"92%"或加"从 78% 提升至"；原文没写的技术不许出现在技术栈里。
   确实需要量化但原文没有数据的位置，写 `【待补：具体指标】`。
4. **JD 只用于排序和定位**：决定 `profile.target_role`、以及项目与技能的详略顺序。
   **绝不能把 JD 的要求写成候选人的经历**（JD 要求 C++ 不代表候选人会 C++）。
5. **项目技术栈只能收录该项目原文明确提到的技术名词**，不得用「这个候选人还会别的」来补全
   （例如项目段只写了 Milvus + 文心一言，就不要把工作经历里的 Go/FastAPI 塞进该项目的 stack）。
6. 公司、学校、项目正式名称不得美化、不得替换成更知名的同类，保持原文表述。
7. 内容允许压缩与合并（同一项目的多条零散描述可并成 2–4 条），但每一条的事实都要有出处。

## 输出格式（严格 JSON，不要 markdown 代码块，不要解释）

{{
  "profile": {{
    "name": "姓名或 null",
    "headline": "一句话身份，如 本科 · 2022 年 6 月毕业（原文字段拼接）",
    "phone": "原始写法或 null",
    "email": "或 null",
    "city": "或 null",
    "target_role": "求职方向，可用 JD 的岗位名",
    "links": [],
    "photo": null
  }},
  "education": [
    {{"school": "学校名称本身，如 XX 大学；原文没有就填 null，不要填身份行",
      "degree": "本科/硕士", "major": "或 null",
      "start": "2020.09 或 null", "end": "2022.06 或 null", "gpa": null, "awards": []}}
  ],
  "works": [
    {{"company": "原文公司名", "role": "岗位", "start": "2022.07", "end": "至今 或 null",
      "bullets": [{{"text": "一条描述", "basis": "resume", "source_span": "原文原句"}}]}}
  ],
  "projects": [
    {{"name": "项目正式名称", "role": "承担角色 或 null", "start": "2024.01", "end": "2026.01 或 null",
      "stack": ["Milvus", "文心一言"],
      "bullets": [{{"text": "一条描述", "basis": "resume", "source_span": "原文原句"}}]}}
  ],
  "skills": [
    {{"label": "语言", "items": ["Go", "Python"]}}
  ],
  "missing": ["素材中缺失、但简历必需的信息，如 学校名称"]
}}

补充要求：
- `works` 每条 3–5 个 bullet，`projects` 每个 2–5 个 bullet，总字数控制在一页 A4 能放下的密度；
- 原文的量化结果（准确率、并发、调用量、成本降幅）必须原样保留在对应 bullet 里；
- 每条 bullet 以强动词开头（主导/设计/开发/优化/搭建/落地），不要"负责了…相关的工作"这种空话；
- `skills` 按原文的技术分类组织，4–6 组，每组 3–8 项。

`missing` 只收录**简历字段本身的缺口**，也就是"填进简历表里的信息没给"：
姓名、学校、专业、起止时间、联系方式、公开链接、照片。
**禁止**把 JD 要求、技能缺口、经验年限判断写进 missing
（例如"C++ 是否掌握""是否具备 Linux 后台经验""是否具备企业微信经验"都不属于 missing，
这些是岗位匹配问题，简历不该出现）。
"""


@dataclass
class ResumeGenerationService:
    """会话内简历 → 可编辑 A4 简历 HTML。"""

    # 用于把生成结果写进主会话（预留注入点，便于单测打桩）
    conversation_store_service: object | None = None

    def _store(self):
        if self.conversation_store_service is None:
            # 延迟导入：conversation_store_service 会反过来 import 会话仓库，
            # 在模块层互相 import 容易形成环。
            from app.services.conversation_store_service import ConversationStoreService

            self.conversation_store_service = ConversationStoreService()
        return self.conversation_store_service

    # ── 对外主流程 ───────────────────────────────────────────────

    def generate(
        self,
        conversation_id: str,
        extra_fields: dict | None = None,
        announce: bool = True,
    ) -> dict:
        """生成简历并落库。返回结构化数据 + HTML 正文 + 审计告警 + 版本信息。

        Args:
            announce: 是否把结果作为一问一答写进会话。chat 链路的工具调用要传 False——
                那边的 chat_stream 自己会写消息，两边都写会出现重复的四个气泡。

        Raises:
            ValueError: 会话内既没有简历文档、对话里也没有足够口述材料，或抽取结果无法解析。
        """
        started = datetime.now()
        conversation_id = (conversation_id or "").strip()
        if not conversation_id:
            raise ValueError("conversation_id 不能为空")

        materials = self.collect_materials(conversation_id)
        spec_loaded = bool(build_skill_spec())

        data, llm_seconds = self.extract(materials, extra_fields)
        warnings = audit_resume_data(data, materials["resume_text"])

        html = self.render_html(data)
        filename, html_path = self.persist_html(html, conversation_id)

        stages = {
            "extract": round(llm_seconds, 2),
            "total": round((datetime.now() - started).total_seconds(), 2),
        }
        record = self.save_generation(
            conversation_id=conversation_id,
            data=data,
            warnings=warnings,
            materials=materials,
            filename=filename,
            html=html,
            stages=stages,
        )

        result = {
            "conversation_id": conversation_id,
            "document_id": materials.get("document_id", ""),
            "data": data,
            "warnings": warnings,
            "missing": data.get("missing", []),
            "html": html,
            "filename": filename,
            "html_url": f"/api/v1/conversation/file/{filename}",
            "html_path": html_path,
            "skill_spec_loaded": spec_loaded,
            "stages": stages,
            **record,
        }
        if announce:
            self.announce_in_conversation(result)
        return result

    # ── 素材收集 ─────────────────────────────────────────────────

    def collect_materials(self, conversation_id: str, allow_conversation_fallback: bool = True) -> dict:
        """取会话内最新一份已解析简历 + 最新一次 JD 分析。

        JD 只作为"投递方向"提供给抽取阶段，不写入简历正文（见模块 docstring 第 3 点）。

        会话内没有可解析文档时，退而使用**用户在对话里说过的话**作为简历原文。
        原先这里是直接 raise「请先上传简历」，导致"直接在对话里给信息、不上传文件"的用户
        完全无法生成简历（2026-10-22 HR 试用即卡死在这条路径）。
        门槛放在 _MIN_SPEECH_CHARS：只有"你好""帮我写份简历"这类闲聊不算素材，
        否则会拿一句问候去抽取，生成一份空简历。

        Args:
            allow_conversation_fallback: 是否允许用口述材料兜底。rerender 场景要传 False：
                它只该基于原文档重算审计，素材没了就沿用上一版告警，不能换成口述文本。
        """
        session = DatabaseManager.get_session()
        try:
            # 文档状态机：parsed（解析完成）→ indexed（建索引成功）→ failed（建索引失败）。
            # parsed 与 indexed 的正文都已落盘可用，failed 才要排除。
            documents = [
                d for d in DocumentRepository().list_by_conversation_id(session, conversation_id)
                if (d.status or "") in ("parsed", "indexed") and d.parsed_text_path
            ]

            jd = JobAnalysisRepository.get_latest_by_conversation_id(session, conversation_id)
            jd_brief = self._build_jd_brief(jd) if jd is not None else ""

            if not documents:
                if not allow_conversation_fallback:
                    raise ValueError("当前会话还没有解析成功的简历，请先上传简历")
                spoken = self.collect_user_speech(conversation_id)
                if len(spoken) < _MIN_SPEECH_CHARS:
                    raise ValueError(
                        "当前会话还没有可用的简历材料：请先上传简历，"
                        "或直接在对话里说明你的经历（学校、公司/实习、项目、技能等）"
                    )
                logger.info(
                    "会话内无可解析文档，改用对话口述材料：conversation_id=%s chars=%d",
                    conversation_id, len(spoken),
                )
                return {
                    "document_id": "",
                    "original_name": "对话口述材料",
                    "resume_text": spoken[:_MAX_RESUME_CHARS],
                    "jd_brief": jd_brief,
                    "from_conversation": True,
                }

            latest = documents[-1]
            resume_text = self._read_text(latest.parsed_text_path)
            if not resume_text:
                raise ValueError("简历解析结果为空，请重新上传")

            return {
                "document_id": latest.document_id,
                "original_name": latest.original_name,
                "resume_text": resume_text[:_MAX_RESUME_CHARS],
                "jd_brief": jd_brief,
            }
        finally:
            DatabaseManager.remove_session()

    def collect_user_speech(self, conversation_id: str, limit: int = 12) -> str:
        """把会话里用户说过的话拼成一段文本，作为"没有上传文档"时的简历素材。

        只取 user 角色：助手消息里全是方法建议、模板说明和"我需要知道你什么"，
        混进去会被抽取阶段当成候选人的经历。图片 markdown 也要剥掉，只留文字。
        """
        try:
            messages = self._store().get_conversation_messages(conversation_id)
        except Exception:
            logger.warning("读取会话消息失败：conversation_id=%s", conversation_id, exc_info=True)
            return ""

        texts: list[str] = []
        for item in messages:
            if (item.get("role") or "") != "user":
                continue
            content = (item.get("content") or "").strip()
            if not content:
                continue
            content = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", content).strip()
            if content:
                texts.append(content)
        return "\n\n".join(texts[-limit:])[:_MAX_RESUME_CHARS]

    @staticmethod
    def _read_text(path: str) -> str:
        if not path or not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()

    @staticmethod
    def _build_jd_brief(jd) -> str:
        """把 JD 分析压成一小段定向说明（只要岗位名、关键词、要求，不要评分与建议）。"""
        def _load(raw: str, limit: int) -> list[str]:
            try:
                value = json.loads(raw) if raw else []
            except (ValueError, TypeError):
                return []
            if not isinstance(value, list):
                return []
            return [str(v).strip() for v in value if str(v).strip()][:limit]

        parts = [f"目标岗位：{jd.job_role or '未识别'}"]
        keywords = _load(jd.keywords, 20)
        if keywords:
            parts.append("JD 关键词：" + "、".join(keywords))
        requirements = _load(jd.requirements, 8)
        if requirements:
            parts.append("硬性要求：\n" + "\n".join(f"- {r}" for r in requirements))
        bonus = _load(jd.bonus_points, 5)
        if bonus:
            parts.append("加分项：\n" + "\n".join(f"- {b}" for b in bonus))
        return "\n".join(parts)[:_MAX_JD_CHARS]

    # ── 抽取 ─────────────────────────────────────────────────────

    def extract(self, materials: dict, extra_fields: dict | None = None) -> tuple[dict, float]:
        """一次 LLM 调用把素材抽成 ResumeData，返回 (data, 耗时秒)。

        关思考：结构化归档属"给材料做判断"，2026-10-18 实测同类任务
        15.8–19.7s → 3.67s，token 888 → 159，方差同时收敛。
        """
        spec = build_skill_spec()
        system_prompt = _EXTRACT_SYSTEM_PROMPT.format(spec=spec or "（技能规范未加载，按通用中文技术简历规范执行）")

        user_parts = ["## 简历原文\n" + materials["resume_text"]]
        if materials.get("jd_brief"):
            user_parts.append(
                "## 目标 JD（只用于确定求职方向与详略顺序，不得写成候选人的经历）\n"
                + materials["jd_brief"]
            )
        if extra_fields:
            extra_lines = "\n".join(f"- {k}：{v}" for k, v in extra_fields.items() if v)
            if extra_lines:
                user_parts.append("## 用户本次补充的信息（basis 记 user）\n" + extra_lines)
        user_parts.append("请输出结构化 JSON。")

        llm = build_chat_llm(
            model=current_chat_model(),
            temperature=0.2,
            enable_thinking=False,
            max_tokens=6000,  # 一份双页简历 JSON 约 2500–3500 token，留一倍余量防截断
        )
        started = datetime.now()
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content="\n\n".join(user_parts)),
        ])
        elapsed = (datetime.now() - started).total_seconds()

        raw = response.content or ""
        logger.info("简历抽取原始输出前 500 字：%s", raw[:500])
        data = normalize_resume_data(parse_llm_json(raw, "简历生成返回格式异常，无法解析为结构化数据"))
        return data, elapsed

    # ── 渲染与落盘 ───────────────────────────────────────────────

    def render_html(self, data: dict) -> str:
        """用固定模板渲染自包含 HTML（无外部 CSS/JS/图片依赖）。"""
        template_dir = ResourceUtils.get_resource_path(TEMPLATE_SUBDIR)
        template_path = os.path.join(template_dir, TEMPLATE_NAME)
        if not os.path.isfile(template_path):
            raise ValueError(f"简历模板缺失：{TEMPLATE_SUBDIR}/{TEMPLATE_NAME}")

        env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(enabled_extensions=("html", "j2"), default_for_string=False),
            trim_blocks=False,
            lstrip_blocks=False,
        )
        view = build_view_model(data)
        return env.get_template(TEMPLATE_NAME).render(**view)

    def persist_html(self, html: str, conversation_id: str) -> tuple[str, str]:
        """落盘到 generated_docs/，返回 (文件名, 绝对路径)。

        复用 /api/v1/conversation/file/<name> 下载路由，不新增静态目录。
        """
        output_dir = ResourceUtils.ensure_resource_dir(OUTPUT_SUBDIR)
        stem = f"resume_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        if not _SAFE_STEM.match(stem):  # 理论上不会触发，留着防生成规则被改动
            raise ValueError("简历文件名生成异常")
        filename = f"{stem}.html"
        path = os.path.join(output_dir, filename)
        Path(path).write_text(html, encoding="utf-8")
        # 登记归属：下载路由只看文件名，不登记的话这个文件就只有管理员能取
        register_generated_file(filename, conversation_id)
        logger.info("简历已生成：%s（%d 字符，会话 %s）", filename, len(html), conversation_id)
        return filename, path

    # ── 持久化与读取 ─────────────────────────────────────────────

    def save_generation(
        self,
        *,
        conversation_id: str,
        data: dict,
        warnings: list[str],
        materials: dict,
        filename: str,
        html: str,
        stages: dict,
    ) -> dict:
        """落库一版简历，返回 generation_id / version / created_at。

        `data_json` 是唯一真相，HTML 只是派生物：文件被清理后能重渲染恢复（见 load_generation）。
        """
        generation_id = f"rg_{uuid.uuid4().hex[:16]}"
        now = datetime.now()
        session = DatabaseManager.get_session()
        try:
            with session.begin():
                # 放在写事务内取版本号，避免并发两次生成拿到同一个 version
                version = ResumeGenerationRepository.next_version(session, conversation_id)
                ResumeGenerationRepository.create(
                    session,
                    generation_id=generation_id,
                    conversation_id=conversation_id,
                    status="ready",
                    target_role=_str((data.get("profile") or {}).get("target_role"))[:128],
                    data_json=json.dumps(data, ensure_ascii=False),
                    source_json=json.dumps(self._source_fingerprint(materials), ensure_ascii=False),
                    warnings_json=json.dumps(list(warnings), ensure_ascii=False),
                    missing_json=json.dumps(data.get("missing") or [], ensure_ascii=False),
                    stages_json=json.dumps(stages, ensure_ascii=False),
                    model=current_chat_model() or "",
                    html_filename=filename,
                    html_chars=len(html),
                    version=version,
                    created_at=now,
                    updated_at=now,
                )
        finally:
            session.close()
        return {"generation_id": generation_id, "version": version, "created_at": now.isoformat()}

    def load_generation(self, generation_id: str) -> dict:
        """按 generation_id 取详情（含 HTML 正文）。

        HTML 文件不存在时用 data_json 原地重渲染并回写文件名，
        这样「消息里的卡片点开 404」在服务端就地消化。
        """
        generation_id = (generation_id or "").strip()
        if not generation_id:
            raise ValueError("generation_id 不能为空")

        session = DatabaseManager.get_session()
        try:
            entity = ResumeGenerationRepository.get_by_generation_id(session, generation_id)
            if entity is None:
                raise ValueError("简历记录不存在或已删除")

            html, rebuilt = self._read_or_rebuild_html(entity)
            if rebuilt:
                entity.html_chars = len(html)
                entity.updated_at = datetime.now()
                session.commit()
            return self._generation_to_dict(entity, html=html, html_regenerated=rebuilt)
        finally:
            session.close()

    def list_generations(self, conversation_id: str, limit: int = 50) -> list[dict]:
        """会话内的简历版本列表（不含正文，供侧栏树与历史切换）。"""
        conversation_id = (conversation_id or "").strip()
        if not conversation_id:
            return []
        session = DatabaseManager.get_session()
        try:
            rows = ResumeGenerationRepository.list_by_conversation_id(session, conversation_id, limit)
            return [
                {
                    "generation_id": row.generation_id,
                    "version": row.version,
                    "target_role": row.target_role,
                    "warnings_count": len(_loads(row.warnings_json, [])),
                    "missing_count": len(_loads(row.missing_json, [])),
                    "html_chars": row.html_chars,
                    "status": row.status,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
        finally:
            session.close()

    def rerender(self, generation_id: str, extra_fields: dict | None = None) -> dict:
        """基于已存 data 补字段后重渲染，产出新一版（0 次 LLM，约 20ms）。

        用于「把缺的学校名/联系方式补上再出一版」。素材还在就重算审计，
        素材被删则沿用上一版的告警，不阻断。
        """
        started = datetime.now()
        generation_id = (generation_id or "").strip()
        if not generation_id:
            raise ValueError("generation_id 不能为空")

        session = DatabaseManager.get_session()
        try:
            entity = ResumeGenerationRepository.get_by_generation_id(session, generation_id)
            if entity is None:
                raise ValueError("简历记录不存在或已删除")
            conversation_id = entity.conversation_id
            data = _loads(entity.data_json, {})
            warnings = _loads(entity.warnings_json, [])
        finally:
            session.close()

        applied, ignored = apply_extra_fields(data, extra_fields or {})
        materials: dict = {}
        try:
            materials = self.collect_materials(conversation_id, allow_conversation_fallback=False)
            warnings = audit_resume_data(data, materials["resume_text"])
        except ValueError:
            # 文档被删/解析结果丢了：沿用上一版审计结果，用户仍能拿到新一版
            logger.warning("重渲染时素材不可用，沿用上一版审计：%s", generation_id)

        data = normalize_resume_data(data)
        html = self.render_html(data)
        filename, _ = self.persist_html(html, conversation_id)
        stages = {"total": round((datetime.now() - started).total_seconds(), 2)}
        record = self.save_generation(
            conversation_id=conversation_id,
            data=data,
            warnings=warnings,
            materials=materials,
            filename=filename,
            html=html,
            stages=stages,
        )
        result = {
            "conversation_id": conversation_id,
            "data": data,
            "warnings": warnings,
            "missing": data.get("missing", []),
            "html": html,
            "filename": filename,
            "html_url": f"/api/v1/conversation/file/{filename}",
            "applied_fields": applied,
            "ignored_fields": ignored,
            "skill_spec_loaded": True,
            "stages": stages,
            **record,
        }
        self.announce_in_conversation(result)
        return result

    # ── 写回主会话 ───────────────────────────────────────────────

    def announce_in_conversation(self, result: dict) -> None:
        """把生成结果作为一问一答写进会话（助手消息带卡片载荷）。

        写消息失败不能让接口失败：简历已经生成并落库，用户至少能拿到 HTML。
        """
        try:
            self._store().append_pair_with_meta(
                result["conversation_id"],
                build_user_line(result),
                build_assistant_line(result),
                assistant_meta=build_message_meta(result),
            )
        except Exception:
            logger.warning(
                "简历已生成但写入会话消息失败：conversation_id=%s generation_id=%s",
                result.get("conversation_id"),
                result.get("generation_id"),
                exc_info=True,
            )

    @staticmethod
    def _source_fingerprint(materials: dict) -> dict:
        """记录这一版是基于什么素材生成的，便于日后回看与对齐。"""
        return {
            "document_id": materials.get("document_id", ""),
            "original_name": materials.get("original_name", ""),
            "resume_chars": len(materials.get("resume_text") or ""),
            "has_jd": bool(materials.get("jd_brief")),
        }

    def _read_or_rebuild_html(self, entity) -> tuple[str, bool]:
        """读 HTML；文件丢了就从 data_json 重建并回写文件名。"""
        if entity.html_filename:
            path = os.path.join(
                ResourceUtils.get_resource_path(OUTPUT_SUBDIR), entity.html_filename
            )
            if os.path.isfile(path):
                return Path(path).read_text(encoding="utf-8"), False

        data = _loads(entity.data_json, {})
        html = self.render_html(data)
        filename, _ = self.persist_html(html, entity.conversation_id)
        entity.html_filename = filename
        logger.warning(
            "简历 HTML 丢失，已从 data_json 重建：%s → %s", entity.generation_id, filename
        )
        return html, True

    @staticmethod
    def _generation_to_dict(entity, *, html: str | None = None, html_regenerated: bool = False) -> dict:
        result = {
            "generation_id": entity.generation_id,
            "conversation_id": entity.conversation_id,
            "version": entity.version,
            "status": entity.status,
            "target_role": entity.target_role,
            "data": _loads(entity.data_json, {}),
            "warnings": _loads(entity.warnings_json, []),
            "missing": _loads(entity.missing_json, []),
            "stages": _loads(entity.stages_json, {}),
            "source": _loads(entity.source_json, {}),
            "model": entity.model,
            "html_chars": entity.html_chars,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }
        if html is not None:
            result["html"] = html
            result["filename"] = entity.html_filename
            result["html_url"] = f"/api/v1/conversation/file/{entity.html_filename}"
            result["html_regenerated"] = html_regenerated
        return result


# ── 主会话文案与卡片载荷 ─────────────────────────────────────────


def build_user_line(result: dict) -> str:
    """程序化 user 消息：让对话流的时间线完整（点了什么、为哪个岗位）。"""
    role = _str(((result.get("data") or {}).get("profile") or {}).get("target_role"))
    version = result.get("version") or 1
    if version > 1:
        return f"生成简历（目标岗位：{role}，第 {version} 版）" if role else f"生成简历（第 {version} 版）"
    return f"生成简历（目标岗位：{role}）" if role else "生成简历"


def build_assistant_line(result: dict) -> str:
    data = result.get("data") or {}
    warnings = result.get("warnings") or []
    missing = result.get("missing") or []
    role = _str((data.get("profile") or {}).get("target_role"))
    chars = _visible_chars(data)
    version = result.get("version") or 1

    head = (
        f"已按「{role}」重排完成，A4 单栏，正文约 {chars:,} 字"
        + (f"（第 {version} 版）" if version > 1 else "")
        + "。"
    )
    if not warnings and not missing:
        return head + "未发现需要核对的内容，打开预览即可打印或下载。"

    problems: list[str] = []
    if warnings:
        problems.append(f"{len(warnings)} 处数字或技术栈在原文中找不到出处")
    if missing:
        preview = "、".join(missing[:3])
        problems.append(f"{len(missing)} 个字段缺值（{preview}）")
    return head + "；".join(problems) + "。已在下方卡片标出，核对后可补字段重新出一版。"


def build_message_meta(result: dict) -> dict:
    """助手消息的卡片载荷。只存可展示的摘要，不存 HTML 链接（避免文件换了变死链）。"""
    data = result.get("data") or {}
    warnings = result.get("warnings") or []
    missing = result.get("missing") or []
    role = _str((data.get("profile") or {}).get("target_role")) or "简历"
    version = result.get("version") or 1

    bits = ["A4 单栏", f"{_visible_chars(data):,} 字"]
    pending = len(warnings) + len(missing)
    if pending:
        bits.append(f"{pending} 处待核对")

    return {
        "kind": "resume",
        "generation_id": result.get("generation_id", ""),
        "title": f"{role} · 第 {version} 版",
        "meta_line": " · ".join(bits),
        "warnings_count": len(warnings),
        "missing_count": len(missing),
        "version": version,
        "created_at": result.get("created_at", ""),
    }


def _visible_chars(data: dict) -> int:
    """简历正文字数（用于卡片与消息里的"约 N 字"），不算 HTML 标签。"""
    total = 0
    profile = data.get("profile") or {}
    for key in ("name", "headline", "phone", "email", "city", "target_role"):
        total += len(str(profile.get(key) or ""))
    for section in ("education", "works", "projects"):
        for item in data.get(section) or []:
            for key, value in item.items():
                if key not in ("bullets", "stack", "awards"):
                    total += len(str(value or ""))
            for bullet in item.get("bullets") or []:
                total += len(str((bullet or {}).get("text") or ""))
            total += sum(len(str(s)) for s in item.get("stack") or [])
    for group in data.get("skills") or []:
        total += len(str(group.get("label") or ""))
        total += sum(len(str(tag)) for tag in group.get("tags") or [])
    return total


# 补充字段 → ResumeData 路径。只覆盖"占位符级"的简单字段，
# 复杂改写仍走重新生成（需要 LLM 重新组织内容）。
_EXTRA_FIELD_TARGETS: dict[str, tuple[str, str]] = {
    "姓名": ("profile", "name"),
    "名字": ("profile", "name"),
    "手机": ("profile", "phone"),
    "手机号": ("profile", "phone"),
    "电话": ("profile", "phone"),
    "邮箱": ("profile", "email"),
    "邮件": ("profile", "email"),
    "城市": ("profile", "city"),
    "现居": ("profile", "city"),
    "目标岗位": ("profile", "target_role"),
    "求职方向": ("profile", "target_role"),
    "学校": ("education", "school"),
    "学校名称": ("education", "school"),
    "毕业院校": ("education", "school"),
    "专业": ("education", "major"),
    "专业名称": ("education", "major"),
    "学历": ("education", "degree"),
    "毕业时间": ("education", "end"),
}

# missing 项的关键词归属：补上字段后要把对应的缺口从 missing 里摘掉
_MISSING_KEYWORDS: dict[str, tuple[str, ...]] = {
    "name": ("姓名",),
    "phone": ("手机", "电话", "联系方式"),
    "email": ("邮箱", "邮件"),
    "city": ("城市", "现居", "地点"),
    "school": ("学校", "院校"),
    "major": ("专业",),
    "degree": ("学历",),
    "end": ("毕业", "起止"),
}


def apply_extra_fields(data: dict, extra_fields: dict) -> tuple[list[str], list[str]]:
    """把用户补充的字段写进 ResumeData，返回 (已应用, 被忽略)。

    0 次 LLM：只做字段赋值与 missing 清理。认不出的键不猜，直接返回给前端提示。
    """
    applied: list[str] = []
    ignored: list[str] = []
    profile = data.get("profile")
    if not isinstance(profile, dict):
        profile = {}
        data["profile"] = profile

    for raw_key, raw_value in (extra_fields or {}).items():
        key = _str(raw_key)
        value = _str(raw_value)
        target = _EXTRA_FIELD_TARGETS.get(key)
        if not target or not value:
            ignored.append(key)
            continue

        section, field = target
        if section == "profile":
            profile[field] = value
        else:
            education = data.get("education") or []
            if not education:
                education = [{}]
                data["education"] = education
            education[0][field] = value
        applied.append(key)

    if applied:
        satisfied = set()
        for key in applied:
            field = _EXTRA_FIELD_TARGETS[key][1]
            satisfied.update(_MISSING_KEYWORDS.get(field, ()))
        if satisfied:
            data["missing"] = [
                item
                for item in (data.get("missing") or [])
                if not any(word in str(item) for word in satisfied)
            ]
    return applied, ignored


def _loads(raw, default):
    """读库里的 JSON 字符串。脏数据返回 default，不让详情接口 500。"""
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


# ── 数据规范化 ───────────────────────────────────────────────────


def _str(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def normalize_resume_data(raw: dict) -> dict:
    """把模型输出收敛成模板可消费的稳定结构。

    模型偶尔会把 null 写成 "null" 字符串、把数组写成字符串、漏字段，
    这里统一兜住，避免模板渲染时抛 UndefinedError。
    """
    raw = raw if isinstance(raw, dict) else {}

    profile_raw = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    profile = {
        "name": _str(profile_raw.get("name")) or "【待补：姓名】",
        "headline": _str(profile_raw.get("headline")),
        "phone": _str(profile_raw.get("phone")),
        "email": _str(profile_raw.get("email")),
        "city": _str(profile_raw.get("city")),
        "target_role": _str(profile_raw.get("target_role")),
        "links": _str_list(profile_raw.get("links")),
        "photo": _str(profile_raw.get("photo")),
    }

    def _bullets(values) -> list[dict]:
        if not isinstance(values, list):
            return []
        out: list[dict] = []
        for item in values:
            if isinstance(item, str):
                text, basis, span = item.strip(), "resume", ""
            elif isinstance(item, dict):
                text = _str(item.get("text"))
                basis = _str(item.get("basis")) or "resume"
                span = _str(item.get("source_span"))
            else:
                continue
            if text:
                out.append({"text": text, "basis": basis, "source_span": span})
        return out

    def _items(section: str, keys: tuple[str, ...]) -> list[dict]:
        values = raw.get(section)
        if not isinstance(values, list):
            return []
        out: list[dict] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            entry = {key: _str(item.get(key)) for key in keys if key not in ("bullets", "stack", "awards")}
            if "stack" in keys:
                entry["stack"] = _str_list(item.get("stack"))
            if "awards" in keys:
                entry["awards"] = _str_list(item.get("awards"))
            if "bullets" in keys:
                entry["bullets"] = _bullets(item.get("bullets"))
            # 条目至少要有一个能显示的主体字段，否则渲染出空条
            if any(entry.get(k) for k in keys if k not in ("bullets", "stack", "awards")):
                out.append(entry)
        return out

    education = _items("education", ("school", "degree", "major", "start", "end", "gpa", "awards"))
    works = _items("works", ("company", "role", "start", "end", "bullets"))
    projects = _items("projects", ("name", "role", "start", "end", "stack", "bullets"))

    missing = _str_list(raw.get("missing"))
    # 实测模型会把 JD 的匹配缺口（"是否掌握 C++""是否具备 Linux 后台经验"）
    # 塞进 missing。missing 的语义是"简历字段缺口"，JD 缺口属于另一条链路，
    # 混进来会让用户以为简历少写了东西。prompt 已约束，这里再兜一层。
    missing = [m for m in missing if not _JD_NOISE_PATTERN.search(m)]

    # 学校名是投递必需项，取不到时给占位而不是留空单元格（skill 的缺失字段补问规则）
    for edu in education:
        school = (edu.get("school") or "").strip()
        if not school or _DEGREE_WORD_PATTERN.search(school):
            # 模型有时把"本科 · 2022 年 6 月毕业"这类身份行当成学校名填进来
            edu["school"] = "【待补：学校名称】"
            if "学校名称" not in missing:
                missing.append("学校名称")

    skills: list[dict] = []
    raw_skills = raw.get("skills")
    if isinstance(raw_skills, list):
        for group in raw_skills:
            if not isinstance(group, dict):
                continue
            label = _str(group.get("label"))
            tags = _str_list(group.get("items"))
            # 键名不能叫 items：Jinja 里 group.items 会解析成 dict.items 方法而不是字段
            if label and tags:
                skills.append({"label": label, "tags": tags})

    # 规范化过程中补出来的占位也要进 missing，用户才知道要填什么
    if profile["name"].startswith("【待补") and "姓名" not in missing:
        missing.insert(0, "姓名")

    return {
        "profile": profile,
        "education": education,
        "works": works,
        "projects": projects,
        "skills": skills,
        "missing": missing,
    }


def build_view_model(data: dict) -> dict:
    """准备模板变量：头部信息行合并、bullet 文本转安全 HTML、待补标记包 span。

    刻意不改动传入的 data：name_html 属于渲染期字段，混进 data 会被接口原样返回给前端，
    用户改完字段重新提交时又带着它回灌。
    """
    profile = dict(data.get("profile") or {})
    profile["name_html"] = mark_pending(escape(profile.get("name") or ""))
    data = {**data, "profile": profile}

    head_meta: list[str] = []
    # headline 是原始资料的一句话身份，常已包含城市（"现居深圳"），
    # 后面再拼一次 city 会出现"现居深圳 · 深圳"，所以做包含去重
    candidates = [profile.get("headline")]
    candidates += [profile.get(k) for k in ("city", "phone", "email")]
    candidates += list(profile.get("links") or [])
    for value in candidates:
        text = (value or "").strip()
        if not text or text in head_meta:
            continue
        if any(text in existing for existing in head_meta):
            continue
        head_meta.append(text)

    def _decorate(bullets: list[dict]) -> list[dict]:
        return [{**b, "html_text": mark_pending(escape(b["text"]))} for b in bullets]

    # 全部用新对象重建，避免把 html_text / name_html 这类渲染期字段写回 data
    data = {
        **data,
        "works": [{**item, "bullets": _decorate(item.get("bullets") or [])}
                  for item in data.get("works") or []],
        "projects": [{**item, "bullets": _decorate(item.get("bullets") or [])}
                     for item in data.get("projects") or []],
        "education": [{**edu, "awards": [mark_pending(escape(a)) for a in edu.get("awards") or []]}
                      for edu in data.get("education") or []],
    }

    return {"data": data, "head_meta": head_meta}


def mark_pending(escaped_text: str) -> str:
    """把【待补：xxx】包成 .pending（橙褐色），方便用户全局搜索替换。"""
    return _PENDING_PATTERN.sub(
        lambda m: f'<span class="pending">{m.group(0)}</span>', escaped_text
    )


# ── 事实一致性审计 ───────────────────────────────────────────────


def audit_resume_data(data: dict, source_text: str) -> list[str]:
    """回查每个数字、技术栈与出处片段是否真在原文出现，返回人工核对提示。

    这是本项目两个 P0（编造指标、编造技术栈）的落地点：不阻断生成，
    但把"无法溯源"的位置逐条指出来，前端以黄条呈现。
    """
    warnings: list[str] = []
    if not source_text:
        return warnings

    source_numbers = set(_NUMBER_PATTERN.findall(source_text))
    compact_source = _compact(source_text)
    source_lower = source_text.lower()

    def _add(message: str) -> None:
        if message not in warnings and len(warnings) < _MAX_WARNINGS:
            warnings.append(message)

    for item in data.get("works", []):
        owner = item.get("company") or "工作经历"
        for bullet in item.get("bullets", []):
            _audit_bullet(bullet, owner, source_numbers, compact_source, _add)

    for item in data.get("projects", []):
        owner = item.get("name") or "项目"
        for bullet in item.get("bullets", []):
            _audit_bullet(bullet, owner, source_numbers, compact_source, _add)
        for tech in item.get("stack") or []:
            name = (tech or "").strip()
            if name and name.lower() not in source_lower:
                _add(f"「{owner}」技术栈里的「{name}」在简历原文中找不到，确认是否真实掌握")

    if len(warnings) >= _MAX_WARNINGS:
        warnings.append(f"（告警已截断，仅显示前 {_MAX_WARNINGS} 条）")
    return warnings


def _audit_bullet(
    bullet: dict,
    owner: str,
    source_numbers: set[str],
    compact_source: str,
    add,
) -> None:
    text = bullet.get("text") or ""
    basis = bullet.get("basis") or ""
    span = bullet.get("source_span") or ""

    if basis not in ("resume", "user"):
        add(f"「{owner}」有一条内容没标出处类型（basis={basis or '空'}），需人工确认")
    elif basis == "resume":
        if not span:
            add(f"「{owner}」有一条内容未提供原文出处，需人工确认")
        elif len(_compact(span)) >= 6 and _compact(span) not in compact_source:
            # 出处片段连原文都找不到，说明这条是模型自造或改写幅度过大
            add(f"「{owner}」的出处片段在原文中查无对应：{span[:20]}…，疑为改写或编造")

    for number in _NUMBER_PATTERN.findall(text):
        if _YEAR_PATTERN.match(number):
            continue  # 年份/月份原文写作 2024.01，切出来的 2024 与之天然对不上
        if number not in source_numbers:
            add(f"「{owner}」中的数字 {number} 在简历原文里找不到出处，请核对是否被改写放大")


def _compact(text: str) -> str:
    """去掉空白与常见标点，用于宽松比对（模型抄写原句时常替换标点）。"""
    return re.sub(r"[\s，。、；：\u201c\u201d\u2018\u2019,.%·\-–—/()（）]+", "", text or "")

