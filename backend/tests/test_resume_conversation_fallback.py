"""会话口述兜底：没有上传简历也能生成（2026-10-22）。

原来 collect_materials 只认"已上传并解析的文档"，没有就 raise「请先上传简历」。
HR 试用时正是"直接在对话里说经历、不上传文件"，于是每次生成简历都失败
（线上表现是「抱歉，我暂时无法生成回答」）。
"""
from app.core.model_scope import current_conversation_id, request_conversation_scope
from app.services import resume_generation_service as service
from app.tools.resume_generate import ResumeGenerateTool

SPEECH = (
    "张三，电话 13800138000，求职意向前端开发工程师。本科学历，测试大学计算机科学与技术专业，"
    "2026 年 6 月毕业。项目经历：校园二手交易平台（Vue3 + Vite），首屏加载 3.2s 降到 1.1s；"
    "个人博客系统（Vue3 + Node.js）。"
)


class _FakeSession:
    def close(self):
        pass


def _stub_empty_conversation(monkeypatch, speech=""):
    """让会话里既没有文档、也没有 JD，只有（可选的）口述文本。"""
    monkeypatch.setattr(service.DatabaseManager, "get_session", staticmethod(lambda: _FakeSession()))
    monkeypatch.setattr(service.DatabaseManager, "remove_session", staticmethod(lambda: None))
    monkeypatch.setattr(
        service.DocumentRepository, "list_by_conversation_id", lambda self, session, cid: []
    )
    monkeypatch.setattr(
        service.JobAnalysisRepository,
        "get_latest_by_conversation_id",
        staticmethod(lambda session, cid: None),
    )
    svc = service.ResumeGenerationService()
    monkeypatch.setattr(svc, "collect_user_speech", lambda cid, limit=12: speech)
    return svc


def test_uses_speech_when_no_documents(monkeypatch):
    svc = _stub_empty_conversation(monkeypatch, speech=SPEECH)
    materials = svc.collect_materials("conv_1")

    assert materials["from_conversation"] is True
    assert "校园二手交易平台" in materials["resume_text"]
    assert materials["document_id"] == ""


def test_rejects_chitchat_as_material(monkeypatch):
    """只有"你好"这种话不算素材，否则会拿一句问候抽出一份空简历。"""
    svc = _stub_empty_conversation(monkeypatch, speech="你好")
    try:
        svc.collect_materials("conv_1")
    except ValueError as exc:
        assert "请先上传简历" in str(exc)
    else:
        raise AssertionError("闲聊内容不应被当作简历素材")


def test_rerender_path_forbids_speech_fallback(monkeypatch):
    """rerender 只该基于原文档重算审计，素材没了要沿用旧告警。"""
    svc = _stub_empty_conversation(monkeypatch, speech=SPEECH)
    try:
        svc.collect_materials("conv_1", allow_conversation_fallback=False)
    except ValueError as exc:
        assert "请先上传简历" in str(exc)
    else:
        raise AssertionError("rerender 路径不应使用口述兜底")


def test_scope_exposes_conversation_id():
    assert current_conversation_id() == ""
    with request_conversation_scope("conv_abc"):
        assert current_conversation_id() == "conv_abc"
    assert current_conversation_id() == ""


def test_tool_refuses_without_scope():
    """没有会话上下文时明确报错，不能让工具去猜一个 conversation_id。"""
    out = ResumeGenerateTool()._run("")
    assert "会话上下文" in out
