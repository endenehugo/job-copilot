"""防"假装生成"（2026-10-22）。

线上实测：用户给完信息要求生成简历，模型跳过工具直接回
「简历已按你提供的信息生成并落盘，下载链接见上方工具返回结果」——
那个"上方"什么都没有，generated_docs 里也没有任何新文件。
比慢更糟的是让用户以为拿到了东西。
"""
from app.services.conversation_chat_service import ConversationChatService

guard = ConversationChatService._guard_fake_artifact_claim

FAKE_CLAIMS = [
    "简历已按你提供的信息生成并落盘（HTML，A4 单栏排版），下载链接见上方工具返回结果。",
    "已经为你生成了一份简历文件，请查收。",
    "下载链接：/api/v1/conversation/file/zhangsan.html",
    "这份简历已保存为 HTML，点击下载即可。",
]

CLEAN_ANSWERS = [
    "1 + 1 等于 2。",
    "这份简历建议突出项目经历，并补上 GitHub 地址。",
    "你给的资料里没有邮箱，能补充一下吗？",
]


def test_fake_claim_gets_correction():
    for text in FAKE_CLAIMS:
        out = guard(text, [])
        assert out != text, f"应命中却没命中：{text}"
        assert "没有生成任何可下载的文件" in out
        assert text in out  # 原文保留，只在末尾追加说明


def test_real_artifact_is_never_touched():
    files = [{"name": "resume.html", "url": "/api/v1/conversation/file/resume.html"}]
    for text in FAKE_CLAIMS:
        assert guard(text, files) == text


def test_normal_answers_untouched():
    for text in CLEAN_ANSWERS:
        assert guard(text, []) == text


def test_empty_answer_untouched():
    assert guard("", []) == ""
