"""内置面试知识库接口。行为对齐旧版 KnowledgeHandler。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Query

from app.core.deps import get_builtin_knowledge_service
from app.core.response import fail, ok
from app.services.builtin_knowledge_service import BUILTIN_KNOWLEDGE
from app.utils import ResourceUtils

router = APIRouter(prefix="/api/v1")


@router.post("/knowledge/rebuild")
def rebuild_knowledge() -> dict:
    result = get_builtin_knowledge_service().rebuild_index()
    return ok(result, message=f"知识库索引重建完成，共 {result['chunk_count']} 个文本块")


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
        query, k=k, category=category.strip() or None
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

    categories: dict = {}
    for item in BUILTIN_KNOWLEDGE:
        cat = item.get("category", "other")
        categories[cat] = categories.get(cat, 0) + 1

    return ok({
        "index_exists": exists and has_files,
        "total_documents": len(BUILTIN_KNOWLEDGE),
        "categories": categories,
    })
