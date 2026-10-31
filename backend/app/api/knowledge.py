"""内置面试知识库接口。

动态更新链路：提交内容 / AI 自主扩充 → AI 审核（是否面试相关 + 查重）→
通过才入库并增量更新向量索引，拒绝结果留审计；全部 fail-closed。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_builtin_knowledge_service
from app.core.response import fail, ok
from app.core.security import require_admin
from app.schemas.knowledge import KnowledgeSubmitRequest, SelfExpandRequest
from app.utils import ResourceUtils

router = APIRouter(prefix="/api/v1")


@router.post("/knowledge/rebuild")
def rebuild_knowledge(_role: str = Depends(require_admin)) -> dict:
    """重建全量向量索引。

    管理员专属：FAISS 重建要读全量语料并重算 embedding，单核机器上
    几百毫秒到数秒，公网可触发等于给了一个免费的 CPU 打满按钮。
    """
    result = get_builtin_knowledge_service().rebuild_index()
    return ok(result, message=f"知识库索引重建完成，共 {result['chunk_count']} 个文本块")


@router.post("/knowledge/entries")
def submit_knowledge(payload: KnowledgeSubmitRequest) -> dict:
    """提交内容 → AI 审核 → 通过自动入库。"""
    result = get_builtin_knowledge_service().add_entry(payload.content, source="user")
    message = (
        "审核通过，已加入知识库"
        if result["approved"]
        else f"未通过审核：{result['review']['reason'] or '仅收录面试相关内容'}"
    )
    return ok(result, message=message)


@router.get("/knowledge/entries")
def list_knowledge_entries(
    status: str = Query(default="approved"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    try:
        entries = get_builtin_knowledge_service().list_entries(status, limit)
    except ValueError as exc:
        return fail(str(exc), code=400)
    return ok({"entries": entries, "total": len(entries)})


@router.delete("/knowledge/entries/{entry_id}")
def delete_knowledge_entry(entry_id: str, _role: str = Depends(require_admin)) -> dict:
    """删除条目。

    管理员专属：删除会连带重建索引，是破坏性且不可撤销的操作，
    之前挂在公网上等于任何人都能清空知识库。前端也没有暴露入口。
    """
    try:
        get_builtin_knowledge_service().remove_entry(entry_id)
    except ValueError as exc:
        return fail(str(exc), code=404 if "不存在" in str(exc) else 400)
    return ok({"entry_id": entry_id}, message="已删除并重建索引")


@router.post("/knowledge/self-expand")
def self_expand_knowledge(
    payload: SelfExpandRequest, _role: str = Depends(require_admin)
) -> dict:
    """AI 自主扩充：生成候选并自审，通过才入库。

    管理员专属：一次要连烧多条生成 + 审核的模型调用，访客可触发的话
    单站 token 预算会被这一个按钮吃掉大半。
    """
    result = get_builtin_knowledge_service().self_expand(payload.count)
    return ok(
        result,
        message=f"AI 扩充完成：新增 {len(result['added'])} 条，驳回 {len(result['rejected'])} 条",
    )


@router.get("/knowledge/query")
def query_knowledge(
    query: str = Query(default=""),
    k: int = Query(default=3, ge=1, le=20),
    category: str = Query(default=""),
) -> dict:
    query = query.strip()
    if not query:
        return fail("query 参数不能为空", code=400)

    results = get_builtin_knowledge_service().retrieve(
        query,
        k=k,
        category=category.strip() or None,
        # 用户主动搜知识库：放宽门槛（对话注入用 settings 里更严的默认值，
        # 否则弱相关条目会被当作"用户经历"混进简历）
        score_threshold=0.3,
    )
    return ok({"results": results, "total": len(results)})


@router.get("/knowledge/categories")
def knowledge_categories() -> dict:
    return ok({"categories": get_builtin_knowledge_service().list_categories()})


@router.get("/knowledge/status")
def knowledge_status() -> dict:
    index_dir = ResourceUtils.get_resource_path("faiss_index_knowledge")
    exists = os.path.isdir(index_dir)
    has_files = False
    if exists:
        has_files = any(name.endswith(".faiss") for name in os.listdir(index_dir))

    stats = get_builtin_knowledge_service().corpus_stats()
    return ok({
        "index_exists": exists and has_files,
        "total_documents": stats["total"],
        "categories": stats["categories"],
    })
