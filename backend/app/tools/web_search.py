import os
from typing import Any, Type

import requests
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool


class WebSearchInput(BaseModel):
	query: str = Field(description="需要搜索的关键词或问题")


class WebSearchTool(BaseTool):
	"""基于秘塔 AI 的网页搜索工具。"""

	name: str = "web_search_tool"
	description: str = "当需要查询最新网页信息、新闻、官网说明或互联网资料时使用该工具"
	args_schema: Type[BaseModel] = WebSearchInput

	def _run(self, *args: Any, **kwargs: Any) -> str:
		query = (kwargs.get("query") or "").strip()
		if not query:
			return "错误：缺少搜索关键词 query。"

		api_key = os.getenv("METASO_API_KEY")
		if not api_key:
			return "错误：未配置 METASO_API_KEY，无法执行网页搜索。"

		headers = {
			"Authorization": f"Bearer {api_key}",
			"Accept": "application/json",
			"Content-Type": "application/json",
		}
		payload = {
			"q": query,
			"scope": "webpage",
			"includeSummary": False,
			"size": 10,
			"includeRawContent": False,
			"conciseSnippet": False,
		}

		try:
			response = requests.post(
				"https://metaso.cn/api/v1/search",
				headers=headers,
				json=payload,
				timeout=30,
			)
			response.raise_for_status()
			result = response.json()
		except requests.RequestException as exc:
			return f"搜索时发生网络错误: {exc}"
		except ValueError as exc:
			return f"搜索结果解析失败: {exc}"

		err_code = result.get("errCode")
		err_msg = result.get("errMsg")
		if err_code not in (None, 0, "0"):
			return f"搜索接口返回错误: errCode={err_code}, errMsg={err_msg or '未知错误'}"

		records = result.get("webpages") or result.get("data", {}).get("records", [])
		if not records:
			return "未检索到相关网页结果。"

		snippets = []
		for index, item in enumerate(records[:5], start=1):
			title = item.get("title") or "无标题"
			snippet = item.get("snippet") or item.get("content") or "无摘要"
			url = item.get("url") or item.get("link") or ""
			date = item.get("date") or ""

			entry = f"[{index}] {title}\n{snippet}"
			if date:
				entry = f"{entry}\n时间: {date}"
			if url:
				entry = f"{entry}\n链接: {url}"
			snippets.append(entry)

		return "\n\n".join(snippets)
