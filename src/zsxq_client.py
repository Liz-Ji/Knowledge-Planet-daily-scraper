"""知识星球(ZSXQ)未官方 API 客户端。

只依赖 Cookie + User-Agent 即可读取 topics 列表（无需签名头），
参考社区多个开源实现（如 chanwoood/crawl-zsxq）验证过的最小可用方案。
"""
import re
import time
import requests

API_BASE = "https://api.zsxq.com/v1.10"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class CookieExpiredError(Exception):
    """ZSXQ 登录态失效（Cookie 过期）。"""


def _extract_access_token(cookie_raw: str) -> str:
    """从完整 Cookie 字符串中提取 zsxq_access_token；若本身就是 token 则原样返回。"""
    match = re.search(r"zsxq_access_token=([^;]+)", cookie_raw)
    if match:
        return match.group(1)
    return cookie_raw.strip()


class ZsxqClient:
    def __init__(self, cookie_raw: str):
        if not cookie_raw:
            raise ValueError("ZSXQ_COOKIE 未配置")
        self._token = _extract_access_token(cookie_raw)
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": self._token,
                "User-Agent": USER_AGENT,
            }
        )

    def fetch_topics(self, group_id: str, scope: str, max_pages: int = 5, count: int = 20):
        """抓取指定星球+范围(scope=by_owner/digests)的主题列表，按发布时间分页向后翻。

        返回标准化后的 topic 字典列表（未去重，调用方负责去重）。
        """
        results = []
        end_time = None
        for _ in range(max_pages):
            params = {"scope": scope, "count": count}
            if end_time:
                params["end_time"] = end_time
            resp = self._session.get(
                f"{API_BASE}/groups/{group_id}/topics", params=params, timeout=15
            )
            if resp.status_code in (401, 403):
                raise CookieExpiredError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()
            if data.get("succeeded") is False:
                msg = str(data.get("info") or data.get("error") or data)
                if "登录" in msg or "login" in msg.lower():
                    raise CookieExpiredError(msg)
                raise RuntimeError(f"ZSXQ API error: {msg}")

            topics = data.get("resp_data", {}).get("topics", [])
            if not topics:
                break

            for topic in topics:
                results.append(_normalize_topic(topic, group_id, scope))

            end_time = topics[-1].get("create_time")
            if not end_time:
                break
            time.sleep(0.5)  # 简单限速，避免触发风控

        return results


def _normalize_topic(topic: dict, group_id: str, scope: str) -> dict:
    topic_id = topic.get("topic_id")
    ttype = topic.get("type", "")

    author = ""
    title = ""
    content = ""

    talk = topic.get("talk")
    if talk:
        author = talk.get("owner", {}).get("name", "")
        content = talk.get("text", "") or ""
        article = talk.get("article")
        if article:
            title = article.get("title", "")

    question = topic.get("question")
    if question:
        author = question.get("owner", {}).get("name", "")
        content = question.get("text", "") or ""

    answer = topic.get("answer")
    if answer and not content:
        content = answer.get("text", "") or ""

    return {
        "topic_id": str(topic_id),
        "group_id": group_id,
        "scope": scope,
        "type": ttype,
        "author": author,
        "title": title,
        "content": content,
        "create_time": topic.get("create_time", ""),
        "likes_count": topic.get("likes_count", 0) or 0,
        "comments_count": topic.get("comments_count", 0) or 0,
        "url": f"https://wx.zsxq.com/dweb2/index/topic_detail/{topic_id}",
        "digested": bool(topic.get("digested", False)),
        "raw": topic,
    }
