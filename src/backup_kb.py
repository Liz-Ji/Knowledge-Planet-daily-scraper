"""知识库 Git 自动备份：给知识库文件夹存一次带时间戳的版本，能回滚、防丢。

首次会 git init；之后每次把改动提交一版。可手动跑，也被每日任务自动调用：
    .venv\\Scripts\\python.exe src\\backup_kb.py
用 Python 做（不用 .ps1）是因为知识库路径含中文，PowerShell 脚本易踩编码坑。
"""
import sys, subprocess, logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src import config

GITIGNORE = (
    ".obsidian/workspace.json\n"
    ".obsidian/workspace-mobile.json\n"
    ".obsidian/cache\n"
    ".trash/\n"
)


def _git(*args):
    return subprocess.run(["git", *args], cwd=str(config.KB_DIR),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def backup():
    kb = config.KB_DIR
    if not kb.exists():
        logging.warning(f"知识库目录不存在，跳过备份：{kb}")
        return
    gi = kb / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE, encoding="utf-8")
    if not (kb / ".git").exists():
        _git("init")
        _git("config", "user.name", "KB Backup")
        _git("config", "user.email", "kb-backup@local")
    _git("add", "-A")
    status = _git("status", "--porcelain")
    if not status.stdout.strip():
        logging.info("知识库无变化，跳过备份。")
        return
    _git("commit", "-m", "auto backup " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    logging.info("已备份知识库一版。")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    backup()
