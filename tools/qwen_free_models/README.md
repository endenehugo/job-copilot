# 千问免费模型探测器（qwen_free_models.py）

**一句话**：用你的阿里云 / 千问账号登录一次，列出千问 AI 平台上你**可以免费使用**的模型，并真实调用一次确认额度确实还能用。

只依赖 Python 标准库（3.8+），不需要 `pip install` 任何东西。

---

## 它解决什么问题

千问（百炼 / Model Studio）的免费额度有两套体系，而控制台只能一个个模型点开看：

| 登录方式 | 免费额度 | 有效期 | 地域 | 说明 |
| --- | --- | --- | --- | --- |
| 百炼 API Key | **每个模型约 100 万 tokens** | 90 天 | 仅华北2（北京） | 额度按模型独立发放，不共享、不互通；带日期快照与最新版各算一个模型 |
| 千问 OAuth 登录 | **每天 2000 次调用** | 每天重置 | — | 与 API Key 的额度体系相互独立；需浏览器点一下确认 |

本工具把这两套额度都摸清楚：**列全** → **标注免费层** → **实测验证** → **直接生成 `.env` 配置**。

---

## 快速开始

```bash
# 1) 看看现在有哪些模型可以免费用（自动选登录方式）
python tools/qwen_free_models/qwen_free_models.py list

# 2) 明确用百炼 API Key（读环境变量 DASHSCOPE_API_KEY 或仓库根目录 .env）
python tools/qwen_free_models/qwen_free_models.py list --source bailian

# 3) 千问 OAuth 设备码登录（免 API Key，浏览器里点一下确认）
python tools/qwen_free_models/qwen_free_models.py login

# 4) 实测：真的调用一次，确认免费额度还能用
python tools/qwen_free_models/qwen_free_models.py verify qwen-flash qwen-plus

# 5) 导出报告 + 写回项目 .env
python tools/qwen_free_models/qwen_free_models.py list --limit 0 \
    --markdown free-models.md --json free-models.json --write-env .env
```

## 常用参数

| 参数 | 作用 |
| --- | --- |
| `--source auto\|bailian\|oauth` | 登录方式；`auto`（默认）= 有 API Key 走百炼，否则走 OAuth |
| `--limit N` | 最多显示多少个模型，`0` = 全部（默认 30） |
| `--all-providers` | 不限制模型作者，连 DeepSeek / Kimi / MiniMax 等第三方一起列 |
| `--all-modalities` | 连图像 / 视频 / 语音 / 向量模型一起列（默认只看文本与推理类） |
| `--verify` / `verify <模型…>` | 真实调用一次做验证（每次调用消耗 1 次额度） |
| `--show-limits` | 附带查询账号级限流（RPM / 用量限制） |
| `--json` / `--markdown` | 导出 JSON / Markdown 报告 |
| `--write-env FILE` | 把 `DASHSCOPE_API_KEY` / `LLM_MODEL` / `LLM_SMALL_MODEL` / `LLM_FALLBACK_MODELS` 原地写回指定 `.env`（含密钥，别提交） |
| `--api-key` / `--env-file` / `--base-url` | 手动指定 Key / `.env` 路径 / 地域端点（默认 `https://dashscope.aliyuncs.com`） |

---

## 免费层是怎么判定的

1. **接口定价全为 0** → 标记 `永久免费`（例如账号口径下的 `decision-model-preview`）。
2. **有定价但享有新人免费额度** → 标记 `新人额度`（百炼几乎所有模型都发，每模型约 100 万 tokens）。
3. **OAuth 模式** → 统一标记 `OAuth免费`（2000 次/天）。
4. **`--verify` 实测**才是最终结论，它会把人话写进「实测」列：
   - `✅ 可用（耗时，tokens in/out=…）`
   - `❌ 该模型的免费额度已用尽（AllocationQuota.FreeTierOnly）`
   - `❌ 阿里云账户欠费…（Arrearage）` / `❌ 模型未开通或无权限（Model.AccessDenied）` 等

> 控制台的免费额度数据是分钟级更新，**余量以控制台为准**：
> <https://bailian.console.aliyun.com/cn-beijing/costing-balance/free-quota>

---

## 和本仓库（job-copilot）的关系

`backend/app/core/config.py` 里已经按「免费额度按模型独立发放」的思路做了分流：
`LLM_MODEL`（主模型）、`LLM_SMALL_MODEL`（压缩/校验/重排等高频低价值调用）、
`LLM_FALLBACK_MODELS`（额度耗尽后的降级链）。用本工具刷新这三个值即可把免费额度用满：

```bash
python tools/qwen_free_models/qwen_free_models.py list --source bailian --limit 0 --write-env .env
```

它会挑 `qwen-plus` 作主模型、`qwen-flash` 作小模型，并把两者写成降级链（键已存在则原地替换，不动其它行）。

---

## 排障

| 现象 | 原因 / 处理 |
| --- | --- |
| `token 端点被阿里云 WAF 拦截（HTTP 405）` | 当前网络出口被风控。换网络（手机热点 / 关代理）重试；或在能登录的机器上登录后把 `~/.qwen/oauth_creds.json` 拷过来，也可用 `login --token <access_token>` 直接写入 |
| `未登录：没有找到凭据 ~/.qwen/oauth_creds.json` | 先跑 `login`；该文件与 Qwen Code CLI 共用，登录过 qwen-code 的机器可直接复用 |
| 实测报 `AllocationQuota.FreeTierOnly` | 该模型的 100 万 tokens 免费额度已用尽，且控制台开了「免费额度用完即停」。换一个有额度的模型，或在控制台关闭该开关（会转为按量付费扣费） |
| 模型列表不全 | 默认只列 `providers=qwen` 的文本/推理模型，加 `--all-providers --all-modalities`；接口分页上限 200/页，工具会自动翻页 |
| 想让调用不落到收费模型 | 用 `--write-env` 把 `LLM_FALLBACK_MODELS` 限制在实测可用的模型上；账户欠费时任何额度都用不了，先充值 |

---

## 本次实测快照

`free-models.md` / `free-models.json` 是最近一次真实运行的结果（41 个文本类模型可选，其中 `qwen-flash`、`qwen-plus`、`qwen3.5-flash` 三个做了真实调用验证，全部返回 200）。

---

## 它是怎么实现的（无依赖）

- 百炼：`GET /api/v1/models`（模型 + 定价 + 上下文）与 `GET /api/v1/models/limits`（账号限流），
  调用走 OpenAI 兼容端点 `POST /compatible-mode/v1/chat/completions` 做实测。
- 千问 OAuth：RFC 8628 设备码授权（PKCE S256），
  端点 `chat.qwen.ai/api/v1/oauth2/device/code` → `…/oauth2/token`，与开源 Qwen Code CLI 使用同一套公开客户端配置，
  凭据格式兼容 `~/.qwen/oauth_creds.json`。
- HTTP 全部用 `urllib`，并发实测用 `ThreadPoolExecutor`（默认 3 并发，避免触发限流）。
