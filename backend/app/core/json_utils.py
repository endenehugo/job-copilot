"""LLM 结构化输出的容错解析管道（公共模块）。

由各业务服务中四处重复的同名实现收拢合并而来，策略与顺序保持不变：
1. 直接 json.loads
2. 从 markdown 代码块提取 → 修复后重试
3. 括号计数提取最外层 JSON 对象 → 修复后重试
4. 对原始内容修复后重试
5. 正则兜底提取最外层非嵌套对象
全部失败时抛出 ValueError，由调用方决定业务报错文案。
"""

from __future__ import annotations

import json
import re


def repair_json(text: str) -> str:
    """修复 LLM 常见 JSON 格式错误：数组被错误地用 } 闭合而不是 ]。"""
    pattern = r'"\s*\n(\s+)},\s*\n\1(")'
    fixed = re.sub(pattern, lambda m: '"\n' + m.group(1) + '],\n' + m.group(1) + m.group(2), text)
    return fixed


def extract_json_from_markdown(text: str) -> str | None:
    """从 markdown 代码块中提取 JSON 内容（不限位置）"""
    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        candidate = match.strip()
        if candidate.startswith("{"):
            return candidate
    return None


def extract_json_by_braces(text: str) -> str | None:
    """通过括号计数提取最外层 JSON 对象"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_llm_json(content: str, error_message: str) -> dict:
    """按五级降级策略从 LLM 输出中提取 JSON 对象。

    候选文本按原始管道的尝试顺序排列，任何一个候选解析成功即返回；
    全部失败抛出 ValueError(error_message)。
    """
    candidates: list[str] = [content]

    markdown_text = extract_json_from_markdown(content)
    if markdown_text:
        candidates.append(markdown_text)
        repaired = repair_json(markdown_text)
        if repaired != markdown_text:
            candidates.append(repaired)

    braces_text = extract_json_by_braces(content)
    if braces_text:
        candidates.append(braces_text)
        repaired = repair_json(braces_text)
        if repaired != braces_text:
            candidates.append(repaired)

    repaired_raw = repair_json(content)
    if repaired_raw != content:
        candidates.append(repaired_raw)

    json_match = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", content, re.DOTALL)
    if json_match:
        candidates.append(json_match.group(0))

    for candidate in candidates:
        try:
            result = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            return result

    raise ValueError(error_message)
