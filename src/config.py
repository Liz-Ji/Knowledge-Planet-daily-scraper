"""从 .env 加载运行配置。"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
# override=True：让项目 .env 覆盖机器上可能存在的同名系统环境变量
# （曾遇到系统里残留的 FEISHU_APP_ID/SECRET 指向另一个飞书应用，导致鉴权失败）
load_dotenv(ROOT_DIR / ".env", override=True)

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_APP_TOKEN = os.getenv("FEISHU_APP_TOKEN", "")
FEISHU_TABLE_ID = os.getenv("FEISHU_TABLE_ID", "")
FEISHU_ALERT_WEBHOOK = os.getenv("FEISHU_ALERT_WEBHOOK", "")

ZSXQ_COOKIE = os.getenv("ZSXQ_COOKIE", "")

# 大模型「摘要+标签」加工配置（可切换）。留空 LLM_API_KEY 则跳过加工。
# LLM_PROVIDER: deepseek / openai / claude / 其它 OpenAI 兼容服务
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

STATE_DIR = ROOT_DIR / "state"
LOG_DIR = ROOT_DIR / "logs"

# 本地知识库根目录（第二大脑）。速记 capture.py 写到 00-Inbox；可用 .env 的 KB_DIR 覆盖。
KB_DIR = Path(os.getenv("KB_DIR", r"D:\【AI学习】\我的知识库"))
KB_INBOX = KB_DIR / "00-Inbox"
KB_RESOURCES = KB_DIR / "03-Resources"
KB_AREAS = KB_DIR / "02-Areas"
KB_DRAFTS = KB_DIR / "_草稿"

# 面板专用大模型（驾驶舱「整理/口播」按钮用，独立于后台 LLM_*，互不影响）。
PANEL_LLM_PROVIDER = os.getenv("PANEL_LLM_PROVIDER", "")
PANEL_LLM_BASE_URL = os.getenv("PANEL_LLM_BASE_URL", "")
PANEL_LLM_API_KEY = os.getenv("PANEL_LLM_API_KEY", "")
PANEL_MODEL_ORGANIZE = os.getenv("PANEL_MODEL_ORGANIZE", "claude-sonnet-5")   # 整理成卡片
PANEL_MODEL_WRITE = os.getenv("PANEL_MODEL_WRITE", "claude-opus-4-8")         # 写口播/出稿


def panel_llm_configured() -> bool:
    return bool((PANEL_LLM_PROVIDER or "").strip() and PANEL_LLM_API_KEY.strip())


def get_groups():
    """解析 ZSXQ_GROUPS 为 [(group_id, name), ...]"""
    raw = os.getenv("ZSXQ_GROUPS", "")
    groups = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        group_id, _, name = item.partition(":")
        groups.append((group_id.strip(), name.strip() or group_id.strip()))
    return groups


SCOPES = ["by_owner", "digests"]
