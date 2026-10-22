"""压测前置探路：线上单请求 sanity check。

确认四件事，否则阶梯测试的结果没法解读：
1. 能否连通（本机有 http_proxy，requests 默认会走它，这里 trust_env=False）
2. 信封 code 是否 success，且**回答不是兜底文案**（模型没作答时接口照样回 success）
3. agent 模式下有没有真的触发工具（files 非空就会累积垃圾产物）
4. 单请求耗时落在什么量级 —— 顺便按样本量要求反推 LT_STAGES 该设多少

只发 N 次 create + N 次 chat，线上库会多 N 个会话（默认 N=3，取中位数）。

用法：
    D:/dshcache/loadtest-venv/Scripts/python.exe tools/loadtest/sanity.py
    ... https://displaydemo.top "用一句话回答：GIL 是什么" memory
    ... --stream            # 改压 /conversation/chat/stream，额外量首字节
    ... --runs 5 --mode agent

退出码：0 正常；1 有失败（连不上 / code 非 success / 命中兜底 / 有文件产物）
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

import requests

FALLBACK_MARKERS = ("这次模型没有返回内容", "这次没能给出完整回答")
CREATE_TITLE = "lt-session"  # 与 locustfile 一致，方便 cleanup_loadtest.py 兜底


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def _json_or_none(resp):
    try:
        return resp.json()
    except Exception:
        return None


def _post_json(session, url, payload, timeout, label):
    """POST 并返回 (body, 耗时)；连不上/非 JSON 都不抛，只返回 None。"""
    t0 = time.time()
    try:
        resp = session.post(url, json=payload, timeout=timeout)
    except Exception as exc:
        _fail(f"{label} 请求异常：{type(exc).__name__}: {exc}")
        return None, time.time() - t0
    elapsed = time.time() - t0
    body = _json_or_none(resp)
    if body is None:
        _fail(f"{label} 返回非 JSON（HTTP {resp.status_code}），前 120 字节：{resp.text[:120]!r}")
    return body, elapsed


def _judge_chat(body) -> list[str]:
    problems = []
    if body is None:
        return ["响应不是 JSON"]
    if body.get("code") != "success":
        problems.append(f"code={body.get('code')} message={str(body.get('message'))[:80]}")
        return problems
    data = body.get("data") or {}
    answer = str(data.get("answer") or "").strip()
    if not answer:
        problems.append("answer 为空")
    for marker in FALLBACK_MARKERS:
        if marker in answer:
            problems.append(f"命中兜底文案（模型没作答，但接口回 success）：{marker}")
    if body.get("degraded"):
        problems.append(f"degraded=True warnings={body.get('warnings')}")
    return problems


def _run_stream(session, base, cid, query, mode, timeout, extra: dict | None = None) -> tuple[dict, list[str]]:
    """压 /chat/stream，量首字节（首个 delta）与 done 时刻。

    TTFF 才是用户感知的"多久看到第一个字"；整条流的关闭时间还要等 done 之后的
    上下文校验，两者能差 30 秒，混在一起看会误判性能回退。
    """
    t0 = time.time()
    t_first_delta = None
    t_done = None
    answer_chars = 0
    events: list[str] = []
    try:
        with session.post(
            f"{base}/api/v1/conversation/chat/stream",
            json={"conversation_id": cid, "query": query, "mode": mode, **(extra or {})},
            timeout=timeout,
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}"}, [f"HTTP {resp.status_code}"]
            event_name = ""
            for raw in resp.iter_lines(decode_unicode=False):
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace")
                if line.startswith("event: "):
                    event_name = line[7:].strip()
                    events.append(event_name)
                    if event_name == "delta" and t_first_delta is None:
                        t_first_delta = time.time() - t0
                    elif event_name == "done" and t_done is None:
                        t_done = time.time() - t0
                elif line.startswith("data: "):
                    try:
                        payload = json.loads(line[6:])
                    except Exception:
                        continue
                    if event_name == "delta":
                        answer_chars += len(str(payload.get("text") or ""))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}, [f"流式请求异常：{exc}"]

    total = time.time() - t0
    result = {
        "ttff": t_first_delta,
        "done": t_done,
        "total": total,
        "answer_chars": answer_chars,
        "events": events,
    }
    problems = []
    if "error" in events:
        problems.append("流里出现 error 事件")
    if t_done is None:
        problems.append("没收到 done 事件（流被截断）")
    if answer_chars == 0:
        problems.append("delta 累计 0 字符")
    return result, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="压测前置 sanity check")
    parser.add_argument("base", nargs="?", default="https://displaydemo.top")
    parser.add_argument("query", nargs="?", default="用一句话回答：Python 的 GIL 是什么？")
    parser.add_argument(
        "--mode",
        default="memory",
        choices=["memory", "rag", "agent"],
        # 必须和 locustfile.py 的默认值一致：探路测 A 链路、压测压 B 链路，
        # 拿 A 的耗时去定 B 的阶梯时长，误差能有 3 倍。
        help="默认 memory，与 locustfile.py 对齐；要看前端主链路用 agent",
    )
    parser.add_argument("--runs", type=int, default=3, help="重复次数，取中位数（单次可能是 P95）")
    parser.add_argument("--timeout", type=float, default=200)
    parser.add_argument("--stream", action="store_true", help="改压 /chat/stream 并量首字节")
    parser.add_argument(
        "--token",
        default="",
        help="管理员 token（localStorage 的 jc_admin_token）。访客白名单只有 qwen-flash/qwen-plus，"
             "压 qwen3.8-flash 这类模型必须带",
    )
    parser.add_argument("--model", default="", help="指定模型；需要 --token 才放行非访客模型")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    session = requests.Session()
    session.trust_env = False  # 不走系统代理
    if args.token:
        session.headers.update({"X-Admin-Token": args.token})
    elif args.model and args.model not in ("qwen3.8-flash", "qwen3.8-omni-flash", "qwen-plus"):
        print(f"⚠ --model={args.model} 不在访客白名单内，服务端会回 400。请同时给 --token。")
    model_field = {"model": args.model} if args.model else {}

    t0 = time.time()
    try:
        health = session.get(f"{base}/api/v1/system/health", timeout=10).json()
    except Exception as exc:
        print(f"[health] 连不上：{type(exc).__name__}: {exc}")
        return 1
    if health.get("code") != "success":
        print(f"[health] 非 success：{health}")
        return 1
    data = health.get("data") or {}
    print(f"[health] ok env={data.get('env')} keys_ok={data.get('keys_ok')} ({time.time() - t0:.2f}s)")
    print(f"[cookie] {list(session.cookies.keys())}  mode={args.mode}  runs={args.runs}")

    problems: list[str] = []
    elapsed_list: list[float] = []
    ttff_list: list[float] = []
    done_list: list[float] = []
    has_files = False
    answer_chars = 0

    for i in range(1, max(1, args.runs) + 1):
        body, cost = _post_json(
            session,
            f"{base}/api/v1/conversation/create",
            {"title": CREATE_TITLE, "mode": args.mode},
            (5, 30),
            f"run{i} create",
        )
        if body is None or body.get("code") != "success":
            problems.append(f"run{i} 建会话失败")
            continue
        cid = (body.get("data") or {}).get("conversation_id") or ""
        print(f"\n[run{i}] cid={cid} create {cost:.2f}s")
        if not cid:
            problems.append(f"run{i} 没拿到 conversation_id")
            continue

        if args.stream:
            result, run_problems = _run_stream(
                session, base, cid, args.query, args.mode, args.timeout, model_field
            )
            problems.extend(f"run{i}: {p}" for p in run_problems)
            if "error" in result:
                print(f"  ✗ {result['error']}")
                continue
            ttff = result["ttff"]
            done = result["done"]
            print(
                f"  TTFF {ttff:.2f}s | done {done if done is None else f'{done:.2f}s'}"
                f" | 流关闭 {result['total']:.2f}s | delta {result['answer_chars']} 字"
            )
            print(f"  事件：{' → '.join(result['events'][:12])}"
                  + (" …" if len(result["events"]) > 12 else ""))
            if ttff is not None:
                ttff_list.append(ttff)
            if done is not None:
                done_list.append(done)
            continue

        body, elapsed = _post_json(
            session,
            f"{base}/api/v1/conversation/chat",
            {"conversation_id": cid, "query": args.query, "mode": args.mode, **model_field},
            (5, args.timeout),
            f"run{i} chat",
        )
        if body is None:
            problems.append(f"run{i} chat 没有 JSON 响应")
            continue

        run_problems = _judge_chat(body)
        problems.extend(f"run{i}: {p}" for p in run_problems)
        chat = (body.get("data") or {})
        answer = str(chat.get("answer") or "")
        files = chat.get("files") or []
        answer_chars = len(answer)
        has_files = has_files or bool(files)
        elapsed_list.append(elapsed)
        print(f"  code={body.get('code')} {elapsed:.2f}s answer={answer_chars} 字 "
              f"sources={len(chat.get('sources') or [])} files={len(files)}")
        print(f"  回答：{answer[:80]!r}")
        for p in run_problems:
            _fail(p)

    print("\n" + "=" * 56)
    if problems:
        print(f"失败 {len(problems)} 项：")
        for p in problems[:12]:
            print(f"  - {p}")
    else:
        print("全部通过：code=success，回答非兜底，无 degraded")

    if has_files:
        print("\n⚠ 触发了工具并产出文件，压测前必须换 query，否则会累积垃圾产物。")

    if ttff_list:
        print(f"\nTTFF 中位数 {statistics.median(ttff_list):.2f}s（{len(ttff_list)} 次）")
        print(f"done 中位数 {statistics.median(done_list):.2f}s" if done_list else "未收到 done")
    if elapsed_list:
        median = statistics.median(elapsed_list)
        print(f"单请求中位数 {median:.2f}s（{len(elapsed_list)} 次；"
              f"min {min(elapsed_list):.2f}s / max {max(elapsed_list):.2f}s）")
        # 每档要 100 个样本才能引用 P95：样本 ≈ 秒数 / 单请求耗时 × 并发
        per_stage = []
        for users in (1, 2, 4, 8):
            need = max(60, int(round(100 * median / users / 10) * 10))
            per_stage.append(f"{users}:{need}")
        print(f"建议阶梯（按 100 样本/档）：LT_STAGES={','.join(per_stage)}")
        print("  1 并发档即便 100 样本也只反映冷启动，P95 别单独引用。")

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
