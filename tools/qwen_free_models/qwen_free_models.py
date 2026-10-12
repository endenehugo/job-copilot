#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录千问 / 阿里云账号，列出你在千问 AI 平台上「可以免费使用」的模型。

两种登录方式（默认自动择一，可用 --source 指定）：

1. oauth —— 千问 OAuth 设备码登录
   用阿里云账号（或 qwen.ai 账号）在浏览器里点一下确认即可，无需 API Key。
   官方免费额度：每天 2000 次调用，与百炼控制台 API Key 的额度体系相互独立。
   凭据写入 ~/.qwen/oauth_creds.json，与 Qwen Code CLI 共用 —— 已经登录过 Qwen Code
   的机器可以直接复用，不会重复弹登录。

2. bailian —— 阿里云百炼（Model Studio / DashScope）API Key
   新人免费额度：每个模型约 100 万 tokens，仅华北2（北京）地域，有效期 90 天，
   自开通百炼 / 模型发布之日起算（以较晚者为准）。

常用命令：
    python qwen_free_models.py login                  # 设备码登录（浏览器确认）
    python qwen_free_models.py list                   # 列出可免费使用的模型
    python qwen_free_models.py list --source bailian  # 用 .env 里的 DASHSCOPE_API_KEY
    python qwen_free_models.py verify --limit 3       # 真实调用一次，确认额度还能用
    python qwen_free_models.py list --json out.json --markdown out.md

只依赖 Python 标准库（3.8+），直接 python 运行，无需 pip install。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------------------

VERSION = "1.0.0"

# 千问 OAuth（与开源项目 Qwen Code CLI 使用同一套公开客户端配置，Apache-2.0）
OAUTH_BASE = "https://chat.qwen.ai"
OAUTH_DEVICE_CODE_URL = f"{OAUTH_BASE}/api/v1/oauth2/device/code"
OAUTH_TOKEN_URL = f"{OAUTH_BASE}/api/v1/oauth2/token"
OAUTH_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"
OAUTH_SCOPE = "openid profile email model.completion"
OAUTH_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# 百炼（DashScope）默认地域端点
BAILIAN_BASE = "https://dashscope.aliyuncs.com"
BAILIAN_INTL_BASE = "https://dashscope-intl.aliyuncs.com"

# 默认凭据文件（与 Qwen Code CLI 相同路径，可互相复用）
DEFAULT_CREDS_PATH = Path.home() / ".qwen" / "oauth_creds.json"

# 官方免费额度说明（帮助文档：新人免费额度）
BAILIAN_FREE_NOTE = "新人额度 约100万tokens/90天(北京)"
OAUTH_FREE_NOTE = "OAuth 免费额度 2000次/天"

CAPABILITY_LABELS = {
    "TG": "文本生成",
    "Reasoning": "深度思考",
    "VU": "视觉理解",
    "IG": "图片生成",
    "VG": "视频生成",
    "ASR": "语音识别",
    "TTS": "语音合成",
    "ME": "多模态向量",
    "TR": "文本向量",
    "Multimodal-Omni": "全模态",
    "Realtime-Omni": "实时全模态",
    "Realtime-Chatting": "实时语音对话",
    "Realtime-ASR": "实时语音识别",
    "Realtime-Text-to-Speech": "实时语音合成",
    "Realtime-Audio-Translate": "实时语音翻译",
    "3D-generation": "3D生成",
}

# 对话 / 文本类模型能力：默认只列这些，--all-modalities 可放开
TEXT_CAPABILITIES = {"TG", "Reasoning"}

# 控制台地址（给用户自己查剩余额度 / 建 Key）
URL_FREE_QUOTA = "https://bailian.console.aliyun.com/cn-beijing/costing-balance/free-quota"
URL_API_KEY = "https://bailian.console.aliyun.com/settings/api-key"
URL_MODEL_MARKET = "https://bailian.console.aliyun.com/model/market"


# --------------------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------------------


def die(msg: str, code: int = 1) -> "None":
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg: str) -> None:
    print(f"! {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def mask(secret: str | None, keep: int = 6) -> str:
    """脱敏：只保留前缀和长度，不回显尾部（避免成为离线爆破的校验位）。"""
    if not secret:
        return "(空)"
    if len(secret) <= keep + 4:
        return "*" * len(secret)
    return f"{secret[:keep]}…（共 {len(secret)} 位）"


def disp_width(text: str) -> int:
    """按终端显示宽度计算（中文算 2 列）。"""
    width = 0
    for ch in str(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    text = str(text)
    return text + " " * max(0, width - disp_width(text))


def print_table(headers: list[str], rows: list[list[Any]], indent: str = "  ") -> None:
    if not rows:
        print(f"{indent}(无数据)")
        return
    cols = len(headers)
    widths = [disp_width(h) for h in headers]
    normalized = []
    for row in rows:
        cells = ["" if c is None else str(c) for c in row][:cols]
        cells += [""] * (cols - len(cells))
        normalized.append(cells)
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], disp_width(cell))
    line = indent + "  ".join(pad(h, w) for h, w in zip(headers, widths))
    print(line.rstrip())
    print(indent + "  ".join("-" * w for w in widths))
    for cells in normalized:
        print((indent + "  ".join(pad(c, w) for c, w in zip(cells, widths))).rstrip())


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    """返回 (status_code, 解析后的 body)；网络异常返回 (0, {"error": ...})。"""
    data: bytes | None = None
    hdrs = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("Accept", "application/json")
    hdrs.setdefault("User-Agent", f"qwen-free-models/{VERSION}")

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, _parse_body(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return exc.code, _parse_body(raw)
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _parse_body(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:2000]}


def is_waf_block(body: Any) -> bool:
    """阿里云 WAF 拦截时返回的是 HTML 拦截页（常见 405/403），需要单独识别。"""
    if not isinstance(body, dict):
        return False
    raw = str(body.get("raw") or "").lower()
    return "has been blocked" in raw or "<!doctypehtml" in raw or "<!doctype html" in raw


class WafBlocked(RuntimeError):
    """请求被阿里云 WAF 拦截（通常是网络出口被风控，而非参数问题）。"""


def read_env_file(path: Path) -> dict[str, str]:
    """解析 .env（KEY=VALUE，忽略注释与引号），供百炼模式取 Key。"""
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def find_env_file(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if p.is_file() else None
    here = Path(__file__).resolve().parent
    for base in [Path.cwd(), *Path.cwd().parents, here, *here.parents]:
        candidate = base / ".env"
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------------------


@dataclass
class ModelRow:
    model: str
    name: str = ""
    caps: list[str] = field(default_factory=list)
    context: int | None = None
    price_in: str = "-"
    price_out: str = "-"
    free_kind: str = "-"          # 免费层分类
    free_note: str = ""           # 免费额度说明
    verify: str = ""              # 实测结果
    source: str = ""              # bailian / oauth
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def caps_text(self) -> str:
        return "/".join(CAPABILITY_LABELS.get(c, c) for c in self.caps) or "-"

    @property
    def context_text(self) -> str:
        if not self.context:
            return "-"
        if self.context >= 1_000_000:
            return f"{self.context / 1_000_000:.1f}M"
        return f"{self.context // 1000}K"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "name": self.name,
            "capabilities": self.caps,
            "context_window": self.context,
            "price_input": self.price_in,
            "price_output": self.price_out,
            "free_kind": self.free_kind,
            "free_note": self.free_note,
            "verify": self.verify,
            "source": self.source,
        }


# --------------------------------------------------------------------------------------
# 模式一：千问 OAuth 设备码登录（不需要 API Key）
# --------------------------------------------------------------------------------------


class QwenOAuth:
    def __init__(self, creds_path: Path, timeout: float = 30.0):
        self.creds_path = creds_path
        self.timeout = timeout
        self.creds: dict[str, Any] = {}

    # -- 凭据读写 ---------------------------------------------------------------
    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.creds_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not data.get("access_token"):
            return None
        self.creds = data
        return data

    def save(self) -> None:
        self.creds_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.creds_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.creds, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows 下 chmod 基本无效，交给 NTFS ACL
        tmp.replace(self.creds_path)

    # -- token 有效性 -----------------------------------------------------------
    @property
    def access_token(self) -> str | None:
        return self.creds.get("access_token")

    @property
    def base_url(self) -> str:
        return (self.creds.get("resource_url") or "https://portal.qwen.ai/v1").rstrip("/")

    @property
    def expired(self) -> bool:
        expiry = self.creds.get("expiry_date")
        if not expiry:
            return False
        return time.time() * 1000 >= float(expiry) - 60_000

    # -- 设备码流程 -------------------------------------------------------------
    def request_device_code(self) -> dict[str, Any]:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        )
        status, body = http_request(
            "POST",
            OAUTH_DEVICE_CODE_URL,
            headers={"x-request-id": str(uuid.uuid4())},
            form={
                "client_id": OAUTH_CLIENT_ID,
                "scope": OAUTH_SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            timeout=self.timeout,
        )
        if status != 200 or not isinstance(body, dict) or "device_code" not in body:
            raise RuntimeError(f"设备码申请失败（HTTP {status}）：{json.dumps(body, ensure_ascii=False)[:300]}")
        body["code_verifier"] = verifier
        return body

    def poll_token(
        self, device_code: str, code_verifier: str, *, timeout_s: float = 300.0, open_browser: bool = True
    ) -> dict[str, Any]:
        interval = 2.0
        deadline = time.time() + max(1.0, timeout_s)
        while time.time() < deadline:
            status, body = http_request(
                "POST",
                OAUTH_TOKEN_URL,
                form={
                    "grant_type": OAUTH_DEVICE_GRANT,
                    "client_id": OAUTH_CLIENT_ID,
                    "device_code": device_code,
                    "code_verifier": code_verifier,
                },
                timeout=self.timeout,
            )
            if status == 200 and isinstance(body, dict) and body.get("access_token"):
                self.creds = {
                    "access_token": body.get("access_token"),
                    "refresh_token": body.get("refresh_token"),
                    "token_type": body.get("token_type", "Bearer"),
                    "resource_url": body.get("resource_url"),
                    "expiry_date": (
                        int(time.time() * 1000) + int(body.get("expires_in") or 0) * 1000
                        if body.get("expires_in")
                        else None
                    ),
                }
                self.save()
                return self.creds
            code = body.get("error") if isinstance(body, dict) else None
            if status == 400 and code == "authorization_pending":
                time.sleep(interval)
                continue
            if status == 429 or code == "slow_down":
                interval = min(interval * 1.5, 10.0)
                time.sleep(interval)
                continue
            if is_waf_block(body) or status in (403, 405):
                raise WafBlocked(
                    f"token 端点被阿里云 WAF 拦截（HTTP {status}）。"
                    "常见原因是当前网络出口被风控：换网络（如手机热点）重试，"
                    "或用 --token 直接粘贴一个已登录机器上的 access_token。"
                )
            raise RuntimeError(f"轮询 token 失败（HTTP {status}）：{json.dumps(body, ensure_ascii=False)[:300]}")
        raise TimeoutError("等待授权超时，请重新运行 login")

    def refresh(self) -> bool:
        refresh_token = self.creds.get("refresh_token")
        if not refresh_token:
            return False
        status, body = http_request(
            "POST",
            OAUTH_TOKEN_URL,
            form={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            },
            timeout=self.timeout,
        )
        if status == 200 and isinstance(body, dict) and body.get("access_token"):
            self.creds["access_token"] = body["access_token"]
            self.creds["refresh_token"] = body.get("refresh_token") or refresh_token
            if body.get("resource_url"):
                self.creds["resource_url"] = body["resource_url"]
            if body.get("expires_in"):
                self.creds["expiry_date"] = int(time.time() * 1000) + int(body["expires_in"]) * 1000
            self.save()
            return True
        return False

    # -- 模型列表 ---------------------------------------------------------------
    def list_models(self) -> list[ModelRow]:
        status, body = http_request(
            "GET",
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=self.timeout,
        )
        rows: list[ModelRow] = []
        ids: list[str] = []
        if status == 200 and isinstance(body, dict):
            for item in body.get("data") or []:
                if isinstance(item, dict) and item.get("id"):
                    ids.append(str(item["id"]))
        if not ids:
            reason = (
                "被 WAF 拦截" if is_waf_block(body) else f"HTTP {status}"
            )
            warn(
                f"模型列表接口未按预期返回（{reason}），改用 OAuth 门户的已知免费模型。"
                f"原始响应：{json.dumps(body, ensure_ascii=False)[:200]}"
            )
            ids = ["coder-model", "vision-model"]
        for mid in ids:
            rows.append(
                ModelRow(
                    model=mid,
                    name=mid,
                    caps=["TG"] if "vision" not in mid else ["TG", "VU"],
                    context=None,
                    free_kind="OAuth免费",
                    free_note=OAUTH_FREE_NOTE,
                    source="oauth",
                )
            )
        return rows

    def verify(self, model: str, prompt: str = "ping", max_tokens: int = 8) -> tuple[bool, str]:
        started = time.time()
        status, body = http_request(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.access_token}"},
            json_body={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
                "stream": False,
            },
            timeout=90,
        )
        elapsed = time.time() - started
        if status == 200 and isinstance(body, dict):
            usage = body.get("usage") or {}
            token_txt = ""
            if usage:
                token_txt = f"，tokens in/out={usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}"
            return True, f"可用（{elapsed:.1f}s{token_txt}）"
        return False, f"{explain_error(status, body)}（{elapsed:.1f}s)"


# --------------------------------------------------------------------------------------
# 模式二：阿里云百炼（Model Studio / DashScope）API Key
# --------------------------------------------------------------------------------------


class BailianClient:
    def __init__(self, api_key: str, base_url: str = BAILIAN_BASE, timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def list_models(
        self, *, provider: str | None = "qwen", page_size: int = 100, max_pages: int = 10
    ) -> tuple[list[dict[str, Any]], str | None]:
        """翻页拉取模型列表；返回 (models, 错误信息)。"""
        collected: list[dict[str, Any]] = []
        total: int | None = None
        page = 1
        while page <= max_pages:
            query = {
                "page_no": str(page),
                "page_size": str(min(page_size, 200)),
                "language": "zh-CN",
            }
            if provider:
                query["providers"] = provider
            status, body = http_request(
                "GET",
                f"{self.base_url}/api/v1/models?{urllib.parse.urlencode(query)}",
                headers=self.headers,
                timeout=self.timeout,
            )
            if status != 200:
                return collected, f"HTTP {status} {json.dumps(body, ensure_ascii=False)[:200]}"
            output = (body or {}).get("output") or {}
            batch = output.get("models") or []
            collected.extend(batch)
            total = output.get("total") or total
            if not batch or (total is not None and len(collected) >= int(total)):
                break
            page += 1
        return collected, None

    def list_limits(self, page_size: int = 200) -> dict[str, dict[str, Any]]:
        """查询当前 Key 下各模型的限流/用量限制，返回 {model: limit}。"""
        result: dict[str, dict[str, Any]] = {}
        for page in range(1, 4):
            status, body = http_request(
                "GET",
                f"{self.base_url}/api/v1/models/limits?"
                + urllib.parse.urlencode({"page_no": str(page), "page_size": str(page_size)}),
                headers=self.headers,
                timeout=self.timeout,
            )
            if status != 200:
                break
            quotas = ((body or {}).get("output") or {}).get("quotas") or []
            if not quotas:
                break
            for item in quotas:
                if isinstance(item, dict) and item.get("model"):
                    result[str(item["model"])] = item
            if len(quotas) < page_size:
                break
        return result

    def verify(self, model: str, prompt: str = "ping", max_tokens: int = 8) -> tuple[bool, str]:
        started = time.time()
        status, body = http_request(
            "POST",
            f"{self.base_url}/compatible-mode/v1/chat/completions",
            headers=self.headers,
            json_body={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
                "stream": False,
            },
            timeout=90,
        )
        elapsed = time.time() - started
        if status == 200 and isinstance(body, dict):
            usage = body.get("usage") or {}
            token_txt = ""
            if usage:
                token_txt = (
                    f"，tokens in/out={usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')}"
                )
            return True, f"可用（{elapsed:.1f}s{token_txt}）"
        return False, f"{explain_error(status, body)}（{elapsed:.1f}s）"


def explain_error(status: int, body: Any) -> str:
    """把 DashScope / OpenAI 兼容接口的错误翻译成人话。"""
    payload = body if isinstance(body, dict) else {}
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(err.get("code") or payload.get("code") or "").strip()
    msg = str(err.get("message") or payload.get("message") or payload.get("raw") or "").strip()
    if status == 0:
        return f"网络异常：{msg or payload.get('error') or '未知'}"
    mapping = {
        "AllocationQuota.FreeTierOnly": "该模型的免费额度已用尽（控制台已开启「免费额度用完即停」）",
        "Arrearage": "阿里云账户欠费，免费额度也无法调用，请先充值",
        "InvalidApiKey": "API Key 无效",
        "Model.AccessDenied": "模型未开通或无权限",
        "Model.NotExist": "模型不存在或已下线",
        "Throttling.RateQuota": "触发限流，稍后重试",
        "Throttling.AllocationQuota": "并发/额度超限",
        "DataInspectionFailed": "内容审核未通过",
        "UnsupportedModel": "该模型不支持此接口",
    }
    if code in mapping:
        return f"{mapping[code]}（{code}）"
    head = f"HTTP {status}"
    if code:
        head += f" {code}"
    return f"{head} {msg[:120]}".strip()


# --------------------------------------------------------------------------------------
# 百炼模型列表 → 行数据（含免费判定）
# --------------------------------------------------------------------------------------


def flatten_prices(prices: Any) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for group in prices or []:
        for item in (group or {}).get("prices") or []:
            if isinstance(item, dict):
                flat.append(item)
    return flat


def classify_free(prices: Any) -> tuple[str, str]:
    """根据接口价格判断免费层：全 0 → 永久免费；否则 → 有新人免费额度。"""
    items = flatten_prices(prices)
    numeric: list[float] = []
    for item in items:
        try:
            numeric.append(float(str(item.get("price", "")).strip()))
        except (TypeError, ValueError):
            continue
    if not items:
        return "未知", "接口未返回价格，需登录控制台确认"
    if numeric and max(numeric) == 0:
        return "永久免费", "接口定价为 0 元/次"
    return "新人额度", BAILIAN_FREE_NOTE


def bailian_rows(models: Iterable[dict[str, Any]]) -> list[ModelRow]:
    rows: list[ModelRow] = []
    for m in models:
        if not isinstance(m, dict) or not m.get("model"):
            continue
        price_items = flatten_prices(m.get("prices"))
        price_in = price_out = "-"
        for item in price_items:
            unit = str(item.get("price_unit") or "")
            value = f"{item.get('price')}元/{unit}" if unit else f"{item.get('price')}元"
            if item.get("type") == "input_token":
                price_in = value
            elif item.get("type") == "output_token":
                price_out = value
        if price_items and price_in == "-" and price_out == "-":  # 图/视频等按张计费
            item = price_items[0]
            unit = str(item.get("price_unit") or "")
            price_in = f"{item.get('price')}元/{unit}" if unit else str(item.get("price"))
        free_kind, free_note = classify_free(m.get("prices"))
        model_info = m.get("model_info") or {}
        rows.append(
            ModelRow(
                model=str(m.get("model")),
                name=str(m.get("name") or ""),
                caps=[str(c) for c in (m.get("capabilities") or [])],
                context=model_info.get("context_window"),
                price_in=price_in,
                price_out=price_out,
                free_kind=free_kind,
                free_note=free_note,
                source="bailian",
                raw=m,
            )
        )
    rows.sort(key=lambda r: (r.free_kind not in ("永久免费",), r.model))
    return rows


# --------------------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------------------


def render_console(rows: list[ModelRow], *, source: str, with_verify: bool, limit: int | None, base_url: str) -> None:
    print()
    print(f"千问 AI 平台 · 可免费使用的模型（数据来源：{source}）")
    print(f"接口地址：{base_url}")
    if source == "oauth":
        print(f"免费额度：{OAUTH_FREE_NOTE}（与百炼控制台 API Key 额度相互独立）")
    else:
        print(f"免费额度：{BAILIAN_FREE_NOTE}")
    print()

    shown = rows if limit is None else rows[:limit]
    if source == "oauth":
        headers = ["模型 ID", "能力", "免费情况"]
        table = [[r.model, r.caps_text, r.free_note or r.free_kind] for r in shown]
    else:
        headers = ["模型 ID", "能力", "上下文", "输入价", "输出价", "免费情况"]
        table = [
            [r.model, r.caps_text, r.context_text, r.price_in, r.price_out, r.free_kind]
            for r in shown
        ]
    if with_verify:
        headers.append("实测")
        for row, r in zip(table, shown):
            row.append(r.verify or "-")
    print_table(headers, table)

    if limit is not None and len(rows) > limit:
        print(f"\n  （共 {len(rows)} 个，已显示前 {limit} 个；用 --limit 0 显示全部）")


def render_markdown(rows: list[ModelRow], *, source: str, base_url: str, with_verify: bool) -> str:
    lines = [
        "# 千问 AI 平台可免费使用的模型",
        "",
        f"- 数据来源：`{source}`",
        f"- 接口地址：`{base_url}`",
        f"- 免费额度：{OAUTH_FREE_NOTE if source == 'oauth' else BAILIAN_FREE_NOTE}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if source == "oauth":
        lines += ["| 模型 ID | 能力 | 免费情况 |", "| --- | --- | --- |"]
        for r in rows:
            lines.append(f"| `{r.model}` | {r.caps_text} | {r.free_note or r.free_kind} |")
    else:
        head = "| 模型 ID | 能力 | 上下文 | 输入价 | 输出价 | 免费情况 |"
        sep = "| --- | --- | --- | --- | --- | --- |"
        if with_verify:
            head += " 实测 |"
            sep += " --- |"
        lines += [head, sep]
        for r in rows:
            row = f"| `{r.model}` | {r.caps_text} | {r.context_text} | {r.price_in} | {r.price_out} | {r.free_kind} |"
            if with_verify:
                row += f" {r.verify or '-'} |"
            lines.append(row)
    lines += [
        "",
        "> 免费额度说明：百炼新人额度为每个模型约 100 万 tokens、仅华北2（北京）地域、有效期 90 天；",
        "> OAuth 登录为每天 2000 次调用。额度是否还有剩余以控制台为准：",
        f"> {URL_FREE_QUOTA}",
        "",
    ]
    return "\n".join(lines)


def write_text(path: str, content: str) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"✓ 已写入 {p}")


# --------------------------------------------------------------------------------------
# 认证解析
# --------------------------------------------------------------------------------------


def resolve_bailian_key(args: argparse.Namespace) -> tuple[str | None, str]:
    """按 命令行 > 环境变量 > .env 的顺序找 DASHSCOPE_API_KEY，返回 (key, 来源)。"""
    if args.api_key:
        return args.api_key, "命令行 --api-key"
    env_key = os.environ.get("DASHSCOPE_API_KEY")
    if env_key:
        return env_key, "环境变量 DASHSCOPE_API_KEY"
    env_file = find_env_file(args.env_file)
    if env_file:
        values = read_env_file(env_file)
        key = values.get("DASHSCOPE_API_KEY")
        if key:
            return key, f".env（{env_file}）"
    return None, ""


def prompt_for_key() -> str | None:
    if not sys.stdin.isatty():
        return None
    try:
        import getpass

        return getpass.getpass("请输入百炼 API Key（输入不回显，留空跳过）: ").strip() or None
    except Exception:  # noqa: BLE001
        return None


def open_url(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------------------


def cmd_login(args: argparse.Namespace) -> int:
    creds_path = Path(args.creds).expanduser() if args.creds else DEFAULT_CREDS_PATH
    client = QwenOAuth(creds_path, timeout=args.timeout)

    # 逃生通道：直接粘贴一个已登录机器上的 access_token（当前网络被 WAF 拦截时很有用）
    if args.token:
        client.creds = {
            "access_token": args.token,
            "refresh_token": args.refresh_token,
            "token_type": "Bearer",
            "resource_url": args.resource_url or "https://portal.qwen.ai/v1",
            "expiry_date": (
                int(time.time() * 1000) + args.expires_in * 1000 if args.expires_in else None
            ),
        }
        client.save()
        print(f"✓ 已写入凭据：{creds_path}")
        print(f"  access_token：{mask(args.token)}")
        print(f"  接口地址：{client.base_url}")
        return 0

    if client.load() and not client.expired:
        print(f"✓ 已存在有效凭据：{creds_path}")
        print(f"  access_token：{mask(client.access_token)}")
        print(f"  接口地址：{client.base_url}")
        if not args.force:
            print("  （要重新登录请加 --force）")
            return 0

    print("正在向千问 OAuth 申请设备码…")
    try:
        device = client.request_device_code()
    except RuntimeError as exc:
        die(str(exc))

    url = device.get("verification_uri_complete") or device.get("verification_uri")
    print()
    print("=" * 72)
    print("  请用浏览器打开下面的地址，登录阿里云账号 / qwen.ai 账号并点击确认：")
    print(f"  {url}")
    if device.get("user_code"):
        print(f"  设备码（如页面要求手输）：{device['user_code']}")
    print(f"  链接 {device.get('expires_in', '?')} 秒内有效，确认后本命令会自动继续。")
    print("=" * 72)
    if not args.no_browser:
        open_url(str(url))

    try:
        creds = client.poll_token(
            str(device["device_code"]),
            str(device["code_verifier"]),
            timeout_s=args.poll_timeout,
        )
    except TimeoutError as exc:
        die(f"{exc}（可用 --poll-timeout 延长等待时间）")
    except WafBlocked as exc:
        print(file=sys.stderr)
        print("✗ OAuth 登录没能完成：", file=sys.stderr)
        print(f"  {exc}", file=sys.stderr)
        print(file=sys.stderr)
        print("  三个可选做法：", file=sys.stderr)
        print("   1) 换网络重试（手机热点 / 关闭代理）后重跑 login；", file=sys.stderr)
        print("   2) 在能登录的机器上跑 qwen-code 或用本工具 login，再把 ~/.qwen/oauth_creds.json 拷过来；", file=sys.stderr)
        print("      或者直接把那边的 access_token 贴过来：", file=sys.stderr)
        print("      python qwen_free_models.py login --token <access_token>", file=sys.stderr)
        print("   3) 改用百炼 API Key（每个模型约 100 万 tokens 新人免费额度）：", file=sys.stderr)
        print(f"      {URL_API_KEY}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        die(str(exc))

    print()
    print("✓ 登录成功")
    print(f"  access_token：{mask(creds.get('access_token'))}")
    print(f"  refresh_token：{mask(creds.get('refresh_token'))}")
    print(f"  接口地址：{client.base_url}")
    print(f"  凭据文件：{creds_path}（与 Qwen Code CLI 共用）")
    print(f"  免费额度：{OAUTH_FREE_NOTE}")
    return 0


def build_rows(args: argparse.Namespace) -> tuple[list[ModelRow], str, str, BailianClient | None, QwenOAuth | None]:
    """按 --source 准备数据；返回 (rows, source, base_url, bailian, oauth)。"""
    source = args.source
    bailian: BailianClient | None = None
    oauth: QwenOAuth | None = None

    if source in ("auto", "bailian"):
        key, origin = resolve_bailian_key(args)
        if not key and source == "bailian":
            key = prompt_for_key()
            origin = origin or "交互输入"
        if key:
            bailian = BailianClient(key, args.base_url or BAILIAN_BASE, timeout=args.timeout)
            info(f"· 百炼模式：API Key {mask(key)}（来源：{origin}）")
        elif source == "bailian":
            die(
                "未找到 DASHSCOPE_API_KEY。\n"
                f"  方式一：在 {URL_API_KEY} 创建 API Key，写入 .env 或设置环境变量 DASHSCOPE_API_KEY\n"
                "  方式二：改用 OAuth 登录（无需 API Key）→ python qwen_free_models.py login"
            )

    if bailian is None:  # auto 且没有 Key，或显式 oauth
        creds_path = Path(args.creds).expanduser() if args.creds else DEFAULT_CREDS_PATH
        oauth = QwenOAuth(creds_path, timeout=args.timeout)
        if not oauth.load():
            env_token = os.environ.get("QWEN_OAUTH_ACCESS_TOKEN")
            if env_token:
                oauth.creds = {
                    "access_token": env_token,
                    "resource_url": os.environ.get("QWEN_OAUTH_BASE_URL") or "https://portal.qwen.ai/v1",
                }
                info("· OAuth 模式：使用环境变量 QWEN_OAUTH_ACCESS_TOKEN")
            else:
                die(
                    f"未登录：没有找到凭据 {creds_path}。请先执行：\n"
                    "  python qwen_free_models.py login"
                )
        if oauth.expired:
            info("· access_token 已过期，正在用 refresh_token 续期…")
            if not oauth.refresh():
                die("刷新 token 失败，请重新执行：python qwen_free_models.py login")
        info(f"· OAuth 模式：access_token {mask(oauth.access_token)}（{creds_path}）")
        source = "oauth"

    if bailian is not None:
        source = "bailian"
        info(f"· 拉取模型列表：{bailian.base_url}/api/v1/models")
        provider = None if args.all_providers else (args.provider or "qwen")
        models, err = bailian.list_models(provider=provider, page_size=args.page_size)
        if err and not models and bailian.base_url != BAILIAN_INTL_BASE:
            warn(f"主端点失败（{err}），尝试国际站 {BAILIAN_INTL_BASE}")
            bailian.base_url = BAILIAN_INTL_BASE
            models, err = bailian.list_models(provider=provider, page_size=args.page_size)
        if err and not models:
            die(f"模型列表接口调用失败：{err}")
        if err:
            warn(f"部分分页失败，已取得 {len(models)} 个模型：{err}")
        rows = bailian_rows(models)
        if not args.all_modalities:
            rows = [r for r in rows if TEXT_CAPABILITIES & set(r.caps) or not r.caps]
        return rows, source, bailian.base_url, bailian, None

    assert oauth is not None
    rows = oauth.list_models()
    return rows, "oauth", oauth.base_url, None, oauth


def run_verify(
    rows: list[ModelRow],
    bailian: BailianClient | None,
    oauth: QwenOAuth | None,
    *,
    targets: list[str] | None,
    limit: int,
    workers: int,
    prompt: str,
) -> None:
    if targets:
        selected = [r for r in rows if r.model in set(targets)]
        missing = set(targets) - {r.model for r in selected}
        for m in missing:
            selected.append(ModelRow(model=m, source=rows[0].source if rows else "bailian"))
    else:
        selected = rows[:limit] if limit > 0 else rows
    if not selected:
        warn("没有可实测的模型")
        return

    def _one(row: ModelRow) -> None:
        client = bailian or oauth
        assert client is not None
        try:
            ok, detail = client.verify(row.model, prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"异常：{exc}"
        row.verify = ("✅ " if ok else "❌ ") + detail

    info(f"· 实测 {len(selected)} 个模型（每个消耗 1 次调用额度）…")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(_one, selected))


def cmd_list(args: argparse.Namespace) -> int:
    rows, source, base_url, bailian, oauth = build_rows(args)

    if args.free_only:
        rows = [r for r in rows if r.free_kind in ("永久免费", "新人额度") or source == "oauth"]
    if not rows:
        warn("没有匹配的模型（可尝试 --all-providers / --all-modalities）")

    if args.verify:
        targets = args.verify_models or None
        run_verify(
            rows,
            bailian,
            oauth,
            targets=targets,
            limit=args.verify_limit,
            workers=args.workers,
            prompt=args.prompt,
        )

    limit = args.limit if args.limit and args.limit > 0 else None
    render_console(rows, source=source, with_verify=bool(args.verify), limit=limit, base_url=base_url)

    if source == "bailian" and bailian is not None and args.show_limits:
        limits = bailian.list_limits()
        hits = [(r.model, limits.get(r.model)) for r in rows[:20] if limits.get(r.model)]
        if hits:
            print("\n  账号限流（RPM / 用量限制）：")
            table = []
            for model, lim in hits:
                ml = (lim or {}).get("model_limit") or {}
                period = ml.get("request_limit_period")
                rpm = f"{ml.get('request_limit')}/{ '分钟' if period == 60 else f'{period}秒'}"
                usage = ml.get("usage_limit")
                usage_txt = f"{usage} {ml.get('usage_limit_field') or ''}" if usage else "无"
                table.append([model, rpm, usage_txt])
            print_table(["模型 ID", "请求限流", "用量限制"], table)

    if args.json:
        write_text(args.json, json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2))
    if args.markdown:
        write_text(args.markdown, render_markdown(rows, source=source, base_url=base_url, with_verify=bool(args.verify)))

    if args.write_env:
        write_env_snippet(args.write_env, rows, source, base_url, bailian, oauth)

    print()
    if source == "bailian":
        print(f"  免费额度余量 / 用完即停：{URL_FREE_QUOTA}")
        print(f"  模型广场：{URL_MODEL_MARKET}")
    else:
        print("  OAuth 免费额度为每天 2000 次调用，可随时用本工具重新列出模型。")
    return 0


def write_env_snippet(
    path: str,
    rows: list[ModelRow],
    source: str,
    base_url: str,
    bailian: BailianClient | None,
    oauth: QwenOAuth | None,
) -> None:
    """把可直接使用的模型配置写成 .env 片段（不覆盖其它行，同 key 原地替换）。"""
    values: dict[str, str] = {}
    if source == "oauth" and oauth is not None:
        values["QWEN_OAUTH_BASE_URL"] = base_url
        values["QWEN_OAUTH_ACCESS_TOKEN"] = str(oauth.access_token or "")
        if rows:
            values["LLM_MODEL"] = rows[0].model
    else:
        ids = [r.model for r in rows]
        best = next((r.model for r in rows if r.free_kind == "永久免费"), None)

        def pick(*candidates: str) -> str:
            for name in candidates:
                if name in ids:
                    return name
            return ids[0] if ids else ""

        main = pick("qwen-plus", "qwen-flash", "qwen3.8-flash")
        small = pick("qwen-flash", "qwen3.8-flash", "qwen3.5-flash") or main
        fallbacks = [m for m in dict.fromkeys([small, main, best or ""]) if m]
        values["DASHSCOPE_API_KEY"] = str(bailian.api_key if bailian else "")
        if main:
            values["LLM_MODEL"] = main
        if small:
            values["LLM_SMALL_MODEL"] = small
        if fallbacks:
            values["LLM_FALLBACK_MODELS"] = json.dumps(fallbacks, ensure_ascii=False)

    target = Path(path).expanduser()
    existing = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    out: list[str] = []
    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in values:
            out.append(f"{key}={values.pop(key)}")
        else:
            out.append(line)
    if values:
        if out and out[-1].strip():
            out.append("")
        out.append("# 由 qwen_free_models.py 生成（模型均已确认可用）")
        out.extend(f"{k}={v}" for k, v in values.items())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✓ 已更新 {target}（含密钥，注意不要提交到 git）")


def cmd_verify(args: argparse.Namespace) -> int:
    rows, source, base_url, bailian, oauth = build_rows(args)
    targets = args.models or None
    run_verify(
        rows,
        bailian,
        oauth,
        targets=targets,
        limit=args.limit or 3,
        workers=args.workers,
        prompt=args.prompt,
    )
    shown = [r for r in rows if r.verify]
    if not shown and targets:
        shown = [r for r in rows if r.model in set(targets)]
    print()
    print_table(
        ["模型 ID", "免费情况", "实测结果"],
        [[r.model, r.free_kind, r.verify or "-"] for r in shown],
    )
    if args.json:
        write_text(args.json, json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def add_common_options(target: argparse.ArgumentParser) -> None:
    """公共参数：既挂顶层也挂每个子命令，保证写在子命令前后都能识别。"""
    target.add_argument(
        "--source",
        choices=["auto", "bailian", "oauth"],
        default="auto",
        help="登录方式：auto=有 API Key 走百炼、否则 OAuth；bailian=百炼 API Key；oauth=千问 OAuth（默认 auto）",
    )
    target.add_argument("--api-key", help="百炼 API Key（默认读环境变量 DASHSCOPE_API_KEY 或 .env）")
    target.add_argument("--env-file", help="指定 .env 路径")
    target.add_argument("--base-url", help=f"百炼端点（默认 {BAILIAN_BASE}）")
    target.add_argument("--creds", help=f"OAuth 凭据文件（默认 {DEFAULT_CREDS_PATH}）")
    target.add_argument("--timeout", type=float, default=30.0, help="单次请求超时秒数（默认 30）")
    target.add_argument("--provider", default="qwen", help="百炼模型作者筛选（默认 qwen，--all-providers 关闭）")
    target.add_argument("--all-providers", action="store_true", help="不限制模型作者（含 DeepSeek/Kimi 等第三方）")
    target.add_argument("--all-modalities", action="store_true", help="不止文本/推理模型，含图像/语音/向量等")
    target.add_argument("--page-size", type=int, default=100, help="百炼分页大小（默认 100）")
    target.add_argument("--free-only", action="store_true", help="只保留判定为免费层的模型")
    target.add_argument("--limit", type=int, default=30, help="最多显示/实测多少个模型（0=全部，默认 30）")
    target.add_argument("--json", metavar="FILE", help="把结果导出为 JSON")
    target.add_argument("--markdown", metavar="FILE", help="把结果导出为 Markdown")
    target.add_argument(
        "--write-env", metavar="FILE", help="把可用模型配置写入指定 .env（含密钥，注意别提交）"
    )
    target.add_argument("--show-limits", action="store_true", help="额外查询账号级限流（/api/v1/models/limits）")
    target.add_argument("--workers", type=int, default=3, help="实测并发数（默认 3，避免触发限流）")
    target.add_argument("--prompt", default="ping", help="实测调用使用的提示词（默认 ping）")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    add_common_options(common)

    parser = argparse.ArgumentParser(
        prog="qwen_free_models.py",
        description="登录千问 / 阿里云账号，列出并在千问 AI 平台上免费使用模型。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
        epilog=(
            "示例:\n"
            "  python qwen_free_models.py login                  # 设备码登录（浏览器确认，免 API Key）\n"
            "  python qwen_free_models.py list                   # 列出可免费使用的模型\n"
            "  python qwen_free_models.py list --source bailian  # 用 .env 中的 DASHSCOPE_API_KEY\n"
            "  python qwen_free_models.py list --limit 0 --markdown free.md\n"
            "  python qwen_free_models.py verify qwen-flash --limit 1\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"qwen-free-models {VERSION}")

    sub = parser.add_subparsers(dest="command")

    p_login = sub.add_parser("login", parents=[common], help="千问 OAuth 设备码登录")
    p_login.add_argument("--force", action="store_true", help="已有凭据时也重新登录")
    p_login.add_argument("--no-browser", action="store_true", help="不要自动打开浏览器")
    p_login.add_argument("--poll-timeout", type=float, default=300.0, help="等待授权确认的秒数（默认 300）")
    p_login.add_argument("--token", help="跳过设备码流程，直接写入一个现成的 access_token")
    p_login.add_argument("--refresh-token", help="配合 --token 使用，写入 refresh_token")
    p_login.add_argument("--resource-url", help="配合 --token 使用，指定接口地址（默认 https://portal.qwen.ai/v1）")
    p_login.add_argument("--expires-in", type=int, default=0, help="配合 --token 使用，access_token 剩余有效秒数")
    p_login.set_defaults(func=cmd_login)

    p_list = sub.add_parser("list", parents=[common], help="列出可免费使用的模型")
    p_list.add_argument("--verify", action="store_true", help="顺带真实调用一次，确认额度还能用")
    p_list.add_argument("--verify-limit", type=int, default=3, help="实测的模型数量（默认 3）")
    p_list.add_argument("--verify-models", nargs="*", help="只实测指定模型")
    p_list.set_defaults(func=cmd_list)

    p_verify = sub.add_parser("verify", parents=[common], help="实测指定/前几个模型是否真的能免费用")
    p_verify.add_argument("models", nargs="*", help="要实测的模型 ID（省略则测前 --limit 个）")
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args.command = "list"
        args.func = cmd_list
        # 默认 list 时的缺省值
        for name, default in (
            ("verify", False),
            ("verify_limit", 3),
            ("verify_models", None),
        ):
            if not hasattr(args, name):
                setattr(args, name, default)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
