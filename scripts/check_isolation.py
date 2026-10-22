#!/usr/bin/env python3
"""会话隔离的交叉越权检查。

不靠人肉点页面：用两个独立的 cookie jar 模拟两个访客，拿 A 的资源 id 去打
B 的请求，断言全部被拒、且**错误信息与"资源不存在"完全一致**——如果越权回
"无权限"、不存在回"不存在"，这两句话的差异本身就是一次存在性探测。

用法：
    # 默认只跑不需要模型调用的端点（几秒，不烧额度）
    python scripts/check_isolation.py --base http://127.0.0.1:8018

    # 加上需要 LLM 的子资源（JD 分析 / 面试 / 改写 / 简历生成）
    python scripts/check_isolation.py --base https://displaydemo.top --with-llm

退出码 0 = 全部通过，1 = 有越权/泄漏。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar

# 每条： (方法, 路径, 是否写操作, 需要哪个 id 变量)
# id 变量占位：{cid} 会话、{analysis} / {session} / {rewrite} / {generation} / {document} / {version}
ENDPOINTS = [
    ("GET", "/api/v1/conversation/detail?conversation_id={cid}", False),
    ("POST", "/api/v1/conversation/chat", True),
    ("POST", "/api/v1/conversation/chat/stream", True),
    ("POST", "/api/v1/conversation/pin", True),
    ("POST", "/api/v1/conversation/delete", True),
    ("POST", "/api/v1/document/upload", True),
    ("POST", "/api/v1/document/delete", True),
    ("GET", "/api/v1/job/analysis/latest?conversation_id={cid}", False),
    ("GET", "/api/v1/job/analysis/list?conversation_id={cid}", False),
    ("GET", "/api/v1/job/analysis/detail?analysis_id={analysis}", False),
    ("POST", "/api/v1/job/analysis/delete", True),
    ("POST", "/api/v1/job/analyze", True),
    ("POST", "/api/v1/job/analyze/stream", True),
    ("GET", "/api/v1/interview/list?conversation_id={cid}", False),
    ("GET", "/api/v1/interview/detail?session_id={session}", False),
    ("POST", "/api/v1/interview/answer", True),
    ("POST", "/api/v1/interview/delete", True),
    ("GET", "/api/v1/resume/project/rewrites?conversation_id={cid}", False),
    ("GET", "/api/v1/resume/project/rewrite/detail?rewrite_id={rewrite}", False),
    ("POST", "/api/v1/resume/project/rewrite/delete", True),
    ("GET", "/api/v1/resume/versions/list?conversation_id={cid}", False),
    ("GET", "/api/v1/resume/versions/detail?version_id={version}", False),
    ("GET", "/api/v1/resume/versions/compare?conversation_id={cid}", False),
    ("GET", "/api/v1/resume/generations?conversation_id={cid}", False),
    ("GET", "/api/v1/resume/generations/{generation}", False),
    ("POST", "/api/v1/resume/generations/{generation}/regenerate", True),
    ("GET", "/api/v1/export/analysis?analysis_id={analysis}", False),
    ("GET", "/api/v1/export/interview?session_id={session}", False),
]

# 只有这些字段的端点能不带 LLM 就把子资源造出来
NEEDS_LLM = {"analysis", "session", "rewrite", "generation"}


class Visitor:
    def __init__(self, base: str, label: str):
        self.base = base.rstrip("/")
        self.label = label
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        url = self.base + path
        data = None
        hdrs = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=180) as resp:
                raw = resp.read()
                # SSE 端点无论如何都是 HTTP 200，只能从事件类型判断：
                # 被拦时发的是 event: error，没被拦才会流出 meta/delta
                if raw.lstrip().startswith(b"event:"):
                    text = raw.decode("utf-8", "replace")
                    if "event: meta" in text or "event: delta" in text:
                        return {"code": "success", "message": "stream", "data": {}}
                    return {"code": "denied", "message": "stream error", "data": {}}
                return json.loads(raw or b"{}")
        except urllib.error.HTTPError as exc:
            return {"code": exc.code, "message": f"HTTP {exc.code}", "data": {}}
        except Exception as exc:  # noqa: BLE001
            return {"code": "error", "message": str(exc), "data": {}}

    def post(self, path: str, body: dict):
        return self.request("POST", path, body)

    def get(self, path: str):
        return self.request("GET", path)

    def upload(self, conversation_id: str, text: str) -> dict:
        """multipart 手搓：不想为一个检查脚本引入 requests 依赖。"""
        boundary = "----jc" + uuid.uuid4().hex
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"conversation_id\"\r\n\r\n{conversation_id}\r\n",
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"probe.txt\"\r\n"
            f"Content-Type: text/plain\r\n\r\n{text}\r\n",
            f"--{boundary}--\r\n",
        ]
        data = "".join(parts).encode()
        req = urllib.request.Request(
            self.base + "/api/v1/document/upload",
            data=data,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=180) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            return {"code": "error", "message": str(exc), "data": {}}


def ok(resp: dict) -> bool:
    return resp.get("code") == "success"


# 只读列表类端点"看不到就返回空集合"是设计选择（不泄漏存在性），
# 这种情况没有数据流出，同样算拒绝
_LIST_KEYS = ("analyses", "sessions", "rewrites", "versions", "generations", "conversations")


def denied(resp: dict) -> bool:
    """被拒绝：不是 success（或是 success 但集合为空），且不是服务器内部错误。"""
    if ok(resp):
        data = resp.get("data") or {}
        return any(key in data and not data[key] for key in _LIST_KEYS)
    return resp.get("code") not in (500, "error")


def body_for(path: str, ids: dict) -> dict | None:
    """按端点拼请求体。参数顺序不重要，能触发授权检查即可。"""
    if path.endswith("/conversation/chat") or path.endswith("/conversation/chat/stream"):
        return {"conversation_id": ids["cid"], "query": "越权探测", "mode": "agent"}
    if path.endswith("/conversation/pin"):
        return {"conversation_id": ids["cid"], "pinned": True}
    if path.endswith("/conversation/delete"):
        return {"conversation_id": ids["cid"]}
    if path.endswith("/job/analyze") or path.endswith("/job/analyze/stream"):
        return {"conversation_id": ids["cid"], "jd_text": "越权探测 JD"}
    if path.endswith("/job/analysis/delete"):
        return {"analysis_id": ids["analysis"]}
    if path.endswith("/interview/answer"):
        return {"session_id": ids["session"], "answer": "越权探测回答"}
    if path.endswith("/interview/delete"):
        return {"session_id": ids["session"]}
    if path.endswith("/resume/project/rewrite"):
        return {"conversation_id": ids["cid"], "project_description": "越权探测项目"}
    if path.endswith("/resume/project/rewrite/delete"):
        return {"rewrite_id": ids["rewrite"]}
    if path.endswith("/conversation/image/upload"):
        return {"conversation_id": ids["cid"]}
    if path.endswith("/document/delete"):
        return {"document_id": ids["document"]}
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8018", help="服务地址")
    ap.add_argument("--with-llm", action="store_true", help="连同需要模型调用的子资源一起测")
    args = ap.parse_args()

    a = Visitor(args.base, "A")
    b = Visitor(args.base, "B")

    # 先让两边都拿到身份 cookie（首屏任意请求即可签发）
    a.get("/api/v1/conversation/list")
    b.get("/api/v1/conversation/list")

    cid_a = a.post("/api/v1/conversation/create", {"title": "隔离检查 A", "mode": "agent"})["data"]["conversation_id"]
    cid_b = b.post("/api/v1/conversation/create", {"title": "隔离检查 B", "mode": "agent"})["data"]["conversation_id"]

    # A 上传一份文本简历：document / version 两个 id 就能拿到，不需要模型
    probe = "张三，Python 后端 3 年。负责订单系统重构，QPS 从 120 提到 400。"
    up = a.upload(cid_a, probe)
    ids_a = {"cid": cid_a, "document": "", "version": "", "analysis": 0, "session": "", "rewrite": 0, "generation": ""}
    if ok(up):
        ids_a["document"] = (up.get("data", {}).get("document") or {}).get("document_id", "")
    versions = a.get(f"/api/v1/resume/versions/list?conversation_id={cid_a}")
    if ok(versions):
        items = versions.get("data", {}).get("versions") or []
        if items:
            ids_a["version"] = items[0]["version_id"]

    if args.with_llm:
        print("→ 生成子资源（会调模型，较慢）…")
        ana = a.post("/api/v1/job/analyze", {"conversation_id": cid_a, "jd_text": probe})
        if ok(ana):
            ids_a["analysis"] = ana["data"].get("analysis_id", 0)
        iv = a.post("/api/v1/interview/start", {"conversation_id": cid_a, "direction": "general"})
        if ok(iv):
            ids_a["session"] = iv["data"].get("session_id", "")
        rw = a.post(
            "/api/v1/resume/project/rewrite",
            {"conversation_id": cid_a, "project_description": probe},
        )
        if ok(rw):
            ids_a["rewrite"] = rw["data"].get("rewrite_id", 0)
        rg = a.post("/api/v1/resume/generate", {"conversation_id": cid_a})
        if ok(rg):
            ids_a["generation"] = rg["data"].get("generation_id", "")

    # 不存在 id 的基线文案：越权必须与之一致
    missing_ids = {k: v for k, v in ids_a.items()}
    missing_ids.update(
        cid="conv_19700101000000_000000",
        analysis=999999,
        session="iv_19700101000000_000000",
        rewrite=999999,
        generation="rg_19700101000000_000000",
        document="doc_19700101000000_000000",
        version="rv_19700101000000_000000",
    )

    failures: list[str] = []
    checked = 0

    for method, path_tpl, _writable in ENDPOINTS:
        needed = {k for k in ("analysis", "session", "rewrite", "generation") if "{" + k + "}" in path_tpl}
        if needed and not args.with_llm:
            continue
        # 子资源 id 没造出来（比如上传失败）就跳过，避免把"没准备好"误报成通过
        placeholders = [k for k in ids_a if "{" + k + "}" in path_tpl]
        if any(not ids_a.get(k) for k in placeholders):
            continue

        body = body_for(path_tpl, ids_a)
        # body 里的 id 没造出来（比如没开 --with-llm）：跳过，否则测的是"参数为空"而不是越权
        if body and any(
            body.get(k) in ("", 0, None)
            for k in ("conversation_id", "analysis_id", "session_id", "rewrite_id", "document_id")
            if k in body
        ):
            continue

        path = path_tpl.format(**{k: ids_a.get(k, "") for k in ids_a})
        miss_path = path_tpl.format(**{k: missing_ids.get(k, "") for k in missing_ids})
        miss_body = body_for(path_tpl, missing_ids) if body is not None else None
        hdrs = {"Content-Type": "application/json"} if body is not None else {}

        # 基线：不存在的资源被要求"不存在"
        baseline = b.request(method, miss_path, miss_body, hdrs)
        # 攻击：B 拿 A 的真实 id
        attack = b.request(method, path, body, hdrs)
        checked += 1

        if not denied(attack):
            failures.append(f"[越权放行] {method} {path_tpl} → {attack.get('code')} {attack.get('message')}")
            continue
        if attack.get("message") != baseline.get("message"):
            failures.append(
                f"[文案不一致·可探测存在性] {method} {path_tpl}\n"
                f"    越权: {attack.get('message')!r}\n    不存在: {baseline.get('message')!r}"
            )

    # 列表隔离
    list_a = a.get("/api/v1/conversation/list")
    list_b = b.get("/api/v1/conversation/list")
    ids_in_a = {c["conversation_id"] for c in (list_a.get("data", {}).get("conversations") or [])}
    ids_in_b = {c["conversation_id"] for c in (list_b.get("data", {}).get("conversations") or [])}
    if cid_b in ids_in_a or cid_a in ids_in_b:
        failures.append("[列表未隔离] 两个访客互相看得到对方的会话")

    # 正常访问不受影响（防"一刀切全拒"这种假通过）
    if not ok(a.get(f"/api/v1/conversation/detail?conversation_id={cid_a}")):
        failures.append("[正常访问被误伤] 属主读自己的会话失败")

    print(f"\n检查了 {checked} 个端点。")
    if failures:
        print(f"发现 {len(failures)} 个问题：\n")
        for item in failures:
            print("  ✗ " + item)
        return 1
    print("✓ 全部通过：越权一律被拒，且错误文案与「不存在」完全一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
