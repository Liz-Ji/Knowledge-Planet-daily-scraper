"""从 .env 加载运行配置。"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_APP_TOKEN = os.getenv("FEISHU_APP_TOKEN", "")
FEISHU_TABLE_ID = os.getenv("FEISHU_TABLE_ID", "")
FEISHU_ALERT_WEBHOOK = os.getenv("FEISHU_ALERT_WEBHOOK", "")

ZSXQ_COOKIE = os.getenv("ZSXQ_COOKIE", "")

STATE_DIR = ROOT_DIR / "state"
LOG_DIR = ROOT_DIR / "logs"


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
