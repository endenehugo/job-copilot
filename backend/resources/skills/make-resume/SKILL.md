---
name: make-resume
description: 中文简历制作技能：默认基于内置 A4 单栏高密度技术简历骨架，根据用户经历制作、修改或复刻 HTML 简历并给出下载链接；用户也可以指定自己提供的模板、HTML 或参考截图。用户输入“/make-resume”“我要阿酥同款简历”或要求制作、修改、复刻简历文件时使用。
---

# /make-resume：制作与复刻简历文件

`/make-resume` 是唯一的简历文件制作入口。默认使用本文件末尾内置的 A4 单栏高密度技术简历骨架；用户明确指定其他模板、本地 HTML 或参考截图时，优先按指定样式生成。**交付物必须是落盘的真实 `.html` 文件 + 下载链接**，而不是对话里的 HTML 源码。

## 路由边界

- 只改写经历、项目或 HR 话术，不产出文件：使用 `/great-resume`；
- 制作、修改、复刻任何 HTML 简历文件，或选择简历模板：使用 `/make-resume`；
- “我要阿酥同款简历”等同于选择默认 ASu 风格骨架，仍走本技能。

## 运行环境约束（必须先读懂）

本平台**不是**本地编码环境，以下几点决定了唯一可行的交付路径：

1. 你**不能**直接读写磁盘，也没有 shell 和浏览器可执行环境。
2. 唯一的落盘手段是工具 **`html_document_tool`**。所有“给用户一个文件”的动作都必须经过它。
3. **严禁把 HTML 源码直接贴进回答。** 前端会把 `<` `>` 转义/剥离，用户只会看到一堆乱码或空白——这是本技能历史上最常见的失败方式。
4. 因此**不需要也不应该**尝试去读取任何模板文件，或运行 `inline-template.mjs` / `export-resume-pdf.mjs` 之类的脚本——它们在本平台不存在。默认骨架已经完整写在本文件末尾，直接复制改写即可。
5. 无法自动做浏览器打印预览截图。用下方“交付前 QA 自查清单”逐条核对，并在交付说明中如实写明未自动截图。

## 默认模板骨架

本文件末尾的 `## 默认模板骨架` 段落是一份**完整、可直接落盘、自包含**（无外部 CSS/JS/图片依赖）的 A4 中文简历模板。使用方式：

- 复制整份 HTML，把占位内容替换成用户的真实信息，再写出去；
- 骨架里的版式、配色、字号已经调好，不要自己另写一套 CSS；
- 深入版式规则可用 `load_skill(skill_name="make-resume", reference="references/default-resume-template.md")` 读取，印刷/分页 QA 细则对应 `references/page-balance-qa.md`。

## 工作流程

1. **盘点素材**：从用户消息、会话文档和检索上下文里提取姓名、联系方式、教育、实习/工作、项目、技能、目标岗位、公开链接、页数和照片需求。
2. **分析参考材料**：用户提供 PDF、旧简历、截图或图片时，先提取文字，分析栏位、间距、字体层级、颜色、照片位置和分页，再决定沿用哪种样式。未指定模板时用默认骨架；指定了就不擅自替换。
3. **补齐缺口**：只补问真正影响交付的内容，一次最多 3 个问题。姓名、学校、公司、职位、时间和成果指标**不得捏造**；无法补齐时写 `【待补：指标/职责/链接】` 占位，并在交付说明里集中列出让用户核对。
4. **决定页数**：内容能在正常字号下形成完整单页时优先单页；需要双页时先填实第一页，再让第二页承接补充内容，不为了模板结构过早分页。
5. **落盘（核心步骤）**：调用 `html_document_tool` 写入。内容较长时**必须分块**，否则会被截断：
   - `mode="start"`：写入 `<html>` 到 `<body>` 标签 + 头部信息区；
   - `mode="append"`：逐段追加各 `<section>`（每次一个区块）；
   - `mode="finish"`：追加最后一个区块与 `</body></html>` 收尾，工具返回下载链接。
   - 三次调用之间必须使用**相同的 `file_name`**，`file_name` 只允许字母、数字、下划线、连字符（如 `zhangsan-resume`）。
   - 内容很短的一页式简历可以用 `mode="once"` 一次写完。
6. **照片**：用户没有提供照片时，保留右上角占位框并在交付说明里提醒替换，**不要**虚构 base64 图片；用户提供了图片时用其真实 URL。
7. **Logo**：公司/平台名称能确认但没有可用 SVG 资源时，用纯文字公司条。**不要用相似公司的 Logo 代替，也不要引用不存在的本地图标目录**——联系方式用纯文本加 `·` 分隔即可。
8. **交付前 QA**：按下方清单逐条核对 HTML 源码；发现结构性错误就改了重新写一遍，不要带病交付。
9. **给链接**：在最终回答里用工具返回的 Markdown 链接交付，例如 `[下载 zhangsan-resume.html](/api/v1/conversation/file/zhangsan-resume.html)`，外加几句简短说明。

正式成稿只使用当前岗位适用且已确认的主张；“待确认”只能留在草稿，交付前必须确认或删除。

## 输入清单

- 基础信息：姓名、电话、邮箱、城市/籍贯、求职方向、个人主页/GitHub/作品集（可选）；
- 教育背景：学校、学历、专业、起止时间、GPA/排名、主修课程、奖项；
- 实习/工作：公司或团队、岗位、起止时间、业务背景、个人职责、技术栈、结果证据；
- 项目经历：项目名称、岗位相关性、个人边界、技术栈、核心动作、结果证据、公开链接；
- 技能：语言、框架、数据库、中间件、工具链、英语/证书；
- 投递目标：岗位名称、JD、校招/社招、页数要求、风格偏好、是否需要照片；
- 输出要求：文件命名、是否需要自己再导出 PDF。

## 缺失字段补问规则

- 一次最多补问 3 个最关键问题，优先问会改变排版结构的问题：目标岗位、页数、是否已有完整简历/附件；
- 用户要求先出初稿时，不等待字段齐全，用占位标记缺口；
- 教育、工作、项目的正式名称和时间不得代填；无证据的成果指标写可核验的定性结果；
- 同一字段多来源冲突时，列出冲突请用户确认，不自选“更好看”的版本；
- 发现真实隐私信息时，只写入用户要的简历文件，不写入模板、示例或 README。

## 模板选择矩阵

用户明确指定模板时始终以指定模板为准；未指定时使用默认 ASu 风格单栏骨架。

| 场景 | 推荐结构 | 适用内容密度 | 风格 |
| --- | --- | --- | --- |
| ASu 同款、AI/Agent/LLM 技术岗 | A4 单栏高密度，项目与技术证据优先 | 高：多段实习/项目 + 技术栈 | 专业、证据导向 |
| 后端开发 / Python / Java | A4 单页紧凑单栏或两列信息带 | 中高：2—3 段项目/实习 + 技能栈 | 稳重、工程化 |
| 数据分析 / 算法 / AI 应用 | A4 单页或双页，项目和实验结果优先 | 高：指标、模型、数据集 | 专业、证据导向 |
| 产品 / 运营 / 商务 | A4 单页，经历成果和业务指标优先 | 中：经历较均衡 | 简洁、结果导向 |
| 校招无实习或经历较少 | A4 单页，教育、技能、项目补足版面 | 低到中：1—2 个项目 | 稳妥、留白合理 |
| 社招或经历较多 | A4 双页，工作经历按时间倒序 | 高：多段工作/项目/成果 | 稳重、信息密度高 |

单页优先形成“身份定位—最强证据—核心经历—技能”的完整闭环；内容超单页且压缩会损害可读性时扩为双页，而不是把字号缩到不可读。

## 交付前 QA 自查清单

无法自动截图，因此**逐条读一遍自己生成的源码**再收尾：

- [ ] 只有一份 `<div class="sheet">` 表示单页；双页时第二份之前有 `page-break`；
- [ ] `@page { size: A4; }` 存在，`body` 没有多余外边距；
- [ ] 姓名、长邮箱、长 URL、项目标题没有使用 `white-space: nowrap`，不会溢出 `186mm` 宽度；
- [ ] 照片使用 `<img>` 且设置了固定宽高，比例不是被拉伸的明显错误；
- [ ] 链接保留了可识别的目的地文本（域名/关键路径），不是只有“网站”“PAPER”“CODE”；
- [ ] 分区标题、公司条、项目标题、技能行都成对闭合，没有留下未闭合的 `<div>`；
- [ ] 中文字体族写在 CSS 里（`Microsoft YaHei` / `PingFang SC` / `SimHei` 等），`<meta charset="utf-8">` 存在；
- [ ] 没有引用任何外部 CSS/JS/图片文件（用户自备的照片 URL 除外）——要保证断网也能打开；
- [ ] `page-break-inside: avoid` 加在了 `.item` 上，单个经历不会被拆到两页。

## PDF 导出

本平台不能自动导出 PDF。在交付说明里给出人工步骤：Chrome/Edge 打开 HTML → `Ctrl/Cmd + P` → 目标选“另存为 PDF” → 纸张 A4 → 缩放 100% → **勾选“背景图形”** → 关闭页眉页脚。并说明：没装浏览器时可直接投递 HTML 文件本身。

## 交付话术模板

```
已按 <模板名> 生成简历，篇幅/照片位按你的要求做了 <取舍说明>。

[下载 <文件名>](<工具返回的 url>)

- 页数：1 页（或 2 页），<各页内容分布一句话>
- 待你补齐：<列出的占位字段>
- 改文字：直接编辑 HTML 里的文字；导出 PDF：浏览器打印并勾选背景图形
- 注意：本次未做自动打印预览截图，导出 PDF 后请自己看一眼分页
```

---

## 默认模板骨架

下面是**可直接复制改写**的完整自包含 A4 单栏模板。占位文字务必全部替换成真实内容；不要把这段骨架原样写出去。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>姓名-简历</title>
<style>
  @page { size: A4; margin: 12mm; }
  * { box-sizing: border-box; }
  html { background: #f0f1f3; }
  body {
    margin: 0; padding: 0;
    font-family: "Microsoft YaHei", "PingFang SC", "Source Han Sans SC", "SimHei", sans-serif;
    font-size: 10.5pt; line-height: 1.42; color: #1f2328;
  }
  .sheet {
    width: 186mm; min-height: 273mm; margin: 14px auto; padding: 10mm 11mm;
    background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.12);
  }
  .page-break { page-break-before: always; break-before: page; }
  a { color: #2458B8; text-decoration: none; word-break: break-all; }
  a:hover { text-decoration: underline; }

  header.hd { display: flex; justify-content: space-between; align-items: flex-start; gap: 10mm; }
  .hd-main { flex: 1; min-width: 0; }
  h1.name { margin: 0 0 2px; font-size: 22pt; font-weight: 700; letter-spacing: 1px; color: #111; }
  .tagline { margin: 0 0 4px; font-size: 10.5pt; color: #2458B8; font-weight: 500; }
  ul.contacts { margin: 0; padding: 0; list-style: none; font-size: 9.5pt; color: #444; }
  ul.contacts li { display: inline-block; margin-right: 10px; }
  .photo { flex: none; width: 26mm; height: 36mm; border: 1px solid #d0d3d8; overflow: hidden; }
  .photo img { width: 100%; height: 100%; object-fit: cover; display: block; }

  section.sec { margin-top: 12px; }
  h2.sec-title {
    margin: 0 0 6px; font-size: 13pt; font-weight: 700; color: #2458B8;
    border-bottom: 1.5px solid #2458B8; padding-bottom: 2px; letter-spacing: 1px;
  }
  .item { margin-bottom: 8px; page-break-inside: avoid; break-inside: avoid; }
  .bar {
    display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap;
    background: #F3F4F6; border-radius: 6px; padding: 3px 8px; margin-bottom: 3px;
  }
  .bar-org { font-weight: 700; font-size: 10.5pt; }
  .bar-role { font-size: 10pt; color: #333; }
  .bar-time { margin-left: auto; font-size: 9.5pt; color: #555; }
  .proj-title { font-size: 11pt; font-weight: 700; color: #2458B8; margin-bottom: 1px; }
  .proj-title .tag { font-size: 8.5pt; font-weight: 400; color: #666; margin-left: 6px; }
  .stack { font-size: 9.5pt; color: #555; margin-bottom: 2px; }
  ul.bullets { margin: 0; padding-left: 1.35em; }
  ul.bullets li { margin-bottom: 1px; }
  ul.kv { margin: 0; padding-left: 0; list-style: none; }
  ul.kv li { margin-bottom: 2px; }
  ul.kv b { display: inline-block; min-width: 62px; color: #2458B8; }
  b.kw { font-weight: 700; }
  .pending { color: #B45309; }

  @media print {
    html { background: #fff; }
    .sheet { width: auto; min-height: auto; margin: 0; padding: 0; box-shadow: none; }
  }
</style>
</head>
<body>

<div class="sheet">
  <header class="hd">
    <div class="hd-main">
      <h1 class="name">张三</h1>
      <p class="tagline">求职方向：后端开发工程师 · 2027 届本科 · 可实习 6 个月以上</p>
      <ul class="contacts">
        <li>138-0000-0000</li>
        <li>zhangsan@example.com</li>
        <li>上海</li>
        <li>GitHub：github.com/zhangsan</li>
      </ul>
    </div>
    <div class="photo"><img src="" alt="证件照，请替换为本人照片"></div>
  </header>

  <section class="sec">
    <h2 class="sec-title">教育背景</h2>
    <div class="item">
      <div class="bar">
        <span class="bar-org">某某大学</span>
        <span class="bar-role">计算机科学与技术 · 本科</span>
        <span class="bar-time">2023.09 - 2027.06</span>
      </div>
      <ul class="bullets">
        <li>GPA 3.7/4.0（专业前 10%）<span class="pending">【待补：排名口径】</span></li>
        <li>主修：数据结构、操作系统、计算机网络、数据库系统</li>
      </ul>
    </div>
  </section>

  <section class="sec">
    <h2 class="sec-title">实习经历</h2>
    <div class="item">
      <div class="bar">
        <span class="bar-org">某某科技有限公司 · 平台研发组</span>
        <span class="bar-role">后端开发实习生</span>
        <span class="bar-time">2025.06 - 2025.09</span>
      </div>
      <ul class="bullets">
        <li><b class="kw">背景</b>：负责 XX 服务的接口性能治理，日请求量约 XX 万。</li>
        <li><b class="kw">动作</b>：定位慢查询并把热点数据迁到 Redis，接口 P95 从 820ms 降到 180ms<span class="pending">【待补：指标出处】</span></li>
        <li><b class="kw">结果</b>：上线后连续 4 周无超时告警。</li>
      </ul>
    </div>
  </section>

  <section class="sec">
    <h2 class="sec-title">项目经历</h2>
    <div class="item">
      <div class="proj-title">项目名称 <span class="tag">个人项目 · 2025.03 - 2025.05</span></div>
      <div class="stack">技术栈：Python · FastAPI · PostgreSQL · Docker</div>
      <ul class="bullets">
        <li><b class="kw">做了什么</b>：一句话说清项目边界与个人负责的模块。</li>
        <li><b class="kw">关键动作</b>：写清一个能在面试中被追问的技术点，而不是罗列所有功能。</li>
        <li><b class="kw">结果</b>：给出可核验的量化结果，仓库地址 github.com/zhangsan/xxx。</li>
      </ul>
    </div>
  </section>

  <section class="sec">
    <h2 class="sec-title">技能与其他</h2>
    <ul class="kv">
      <li><b>语言</b> Python、Java、SQL、C++</li>
      <li><b>框架</b> FastAPI、Spring Boot、SQLAlchemy</li>
      <li><b>数据库</b> MySQL、PostgreSQL、Redis</li>
      <li><b>工具</b> Git、Docker、Linux</li>
      <li><b>英语</b> CET-6，可无障碍阅读英文技术文档</li>
    </ul>
  </section>
</div>

</body>
</html>
```
