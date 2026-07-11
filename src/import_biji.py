"""从得到笔记(biji.com)公开分享的知识库，把全部笔记导入本地知识库 00-Inbox。

用法：
    .venv\\Scripts\\python.exe src\\import_biji.py "https://biji.com/topic/0QW69Abn"

前提：知识库权限设为「公开」→ 复制分享链接。重复运行按 note_id 去重，不会重复导入。
每条笔记写成一个 Markdown 文件（frontmatter + 标题 + 正文 + 录音链接），落到 00-Inbox，
之后在面板「🔗 整理」里和其它内容一起被 AI 整理成卡片。
"""
import sys, re, json, logging
from datetime import datetime
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src import config
from src.capture import _sanitize, make_frontmatter

API = "https://get-notes.luojilab.com/voicenotes/web/share/topics/{alias}/notes"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
    "Referer": "https://biji.com/", "Origin": "https://biji.com",
    "X-Appid": "3", "Accept": "application/json",
}
STATE = config.STATE_DIR / "biji_imported.json"


def alias_of(link: str) -> str:
    m = re.search(r"/topic/([A-Za-z0-9]+)", link.strip())
    return m.group(1) if m else link.strip()


def fetch_all(alias: str):
    out, page = [], 1
    while True:
        r = requests.get(API.format(alias=alias), headers=HEADERS,
                         params={"page": page, "page_size": 50}, timeout=20)
        c = r.json().get("c", {})
        out += c.get("resources", [])
        if not c.get("has_next"):
            break
        page += 1
    return out


def load_state() -> set:
    if STATE.exists():
        try:
            return set(json.loads(STATE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_state(ids: set):
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(sorted(ids), ensure_ascii=False), encoding="utf-8")


def to_md(r: dict, topic: str):
    nid = str(r.get("note_id") or r.get("id") or "")
    content = (r.get("content") or r.get("body_text") or "").strip()
    title = (r.get("title") or (content.split("\n")[0][:40] if content else "") or "得到笔记").strip()
    tags = [t.get("name") for t in r.get("tags", []) if t.get("name") and t.get("visible")]
    ts = r.get("create_time") or r.get("update_time")
    try:
        created = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d") if ts else datetime.now().strftime("%Y-%m-%d")
    except Exception:
        created = datetime.now().strftime("%Y-%m-%d")
    fm = make_frontmatter({"created": created, "type": "得到笔记", "source": f"得到·{topic}", "tags": tags})
    body = f"{fm}\n\n# {title}\n\n{content}\n"
    audios = [a.get("url") for a in r.get("attachments", []) if a.get("type") == "audio" and a.get("url")]
    if audios:
        body += f"\n- 录音：{audios[0]}\n"
    return nid, title, body


def run(link: str):
    alias = alias_of(link)
    res = fetch_all(alias)
    if not res:
        print("没取到内容。请确认这个知识库已设为「公开」，以及链接是否正确。")
        return
    topic = alias
    try:
        topic = res[0]["topics"][0]["topic_name"] or alias
    except Exception:
        pass
    done = load_state()
    config.KB_INBOX.mkdir(parents=True, exist_ok=True)
    n = 0
    for r in res:
        nid, title, body = to_md(r, topic)
        if nid and nid in done:
            continue
        base = f"得到-{_sanitize(topic)}-{_sanitize(title)}"
        path = config.KB_INBOX / f"{base}.md"
        i = 2
        while path.exists():
            path = config.KB_INBOX / f"{base}-{i}.md"
            i += 1
        path.write_text(body, encoding="utf-8")
        if nid:
            done.add(nid)
        n += 1
    save_state(done)
    print(f"知识库「{topic}」：共 {len(res)} 条，新导入 {n} 条 → 00-Inbox（已去重）")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        print('用法: python src/import_biji.py "https://biji.com/topic/XXXX"')
        sys.exit()
    run(" ".join(sys.argv[1:]))
