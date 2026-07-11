"""知识库驾驶舱：一个本地面板，把「速记 / 待看 / 拖文件 / 搜索」整合进一个网页。

用法（双击 scripts\\panel.ps1，或直接跑）：
    .venv\\Scripts\\python.exe src\\panel.py
启动后自动打开 http://localhost:8825/ 。全部内容落成真实 Markdown 文件（不进浏览器缓存）：
- 速记 → 写进知识库 00-Inbox
- 待看 → 星球内容队列，勾看/写感想的状态存在 state\\reading_state.json（不怕清缓存）；
        「导出为知识卡片」由服务端直接写进 02-Areas
- 拖文件/PDF → 存进 03-Resources
- 搜索 → 全库 Markdown 秒搜，点结果用系统默认程序打开

第二步会再接 Claude 的「整理 / 口播」按钮。用 Python 标准库，无新依赖。
"""
import sys, os, json, re, webbrowser, logging, urllib.parse, threading
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src import config
from src.capture import save_note, recent, _sanitize, make_frontmatter

PORT = 8825
RECENT_DAYS = 14
STATE_FILE = config.STATE_DIR / "reading_state.json"
POSTS_FILE = config.STATE_DIR / "reading_posts.json"
AREAS_DIR = config.KB_DIR / "02-Areas"


# ---------- 数据层 ----------
def load_posts():
    if not POSTS_FILE.exists():
        from src import build_reading
        build_reading.build()  # 首次没有缓存时，拉一次飞书生成
    try:
        return json.loads(POSTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def load_state():
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            s = {}
    else:
        s = {}
    s.setdefault("status", {})
    s.setdefault("notes", {})
    s.setdefault("carded", {})
    s.setdefault("_inited", False)
    return s


def save_state(s):
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")


# 多线程服务里，"打勾"和"写感想"可能同时读改写状态文件而互相覆盖（导致点了看了又冒出来）。
# 用一把锁把「读状态→改→存」串起来，避免丢更新。
_state_lock = threading.Lock()


def dedup_posts(posts):
    """同一帖子ID只保留一条（防止重复记录在待看里出现两张卡）。"""
    seen, out = set(), []
    for p in posts:
        pid = p.get("id")
        if pid and pid in seen:
            continue
        seen.add(pid)
        out.append(p)
    return out


def reading_updated():
    """待看数据的更新时间 = reading_posts.json 的最后写入时间。"""
    try:
        return datetime.fromtimestamp(POSTS_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def materialize(posts, s):
    """给新帖子分配初始状态：首次初始化时按14天分（旧的归档、近的进队列）；之后新抓的默认进队列。"""
    cut = datetime.now().timestamp() * 1000 - RECENT_DAYS * 86400000
    changed = False
    inited = s["_inited"]
    for p in posts:
        pid = p["id"]
        if pid not in s["status"]:
            s["status"][pid] = "queue" if (inited or (p.get("ts", 0) >= cut)) else "done"
            changed = True
    if not s["_inited"]:
        s["_inited"] = True
        changed = True
    if changed:
        save_state(s)
    return s


def send_reading_to_organize():
    """把「写了感想且未送」的待看条目作为原材料写进 00-Inbox（保留原帖出处），
    之后由「整理」和速记内容一起统一成卡、把关质量、查连接。返回送出数。"""
    posts = {p["id"]: p for p in load_posts()}
    s = load_state()
    n = 0
    config.KB_INBOX.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    for pid, note in s["notes"].items():
        note = (note or "").strip()
        if not note or s["carded"].get(pid) or pid not in posts:
            continue
        p = posts[pid]
        fm = make_frontmatter({
            "created": now.strftime("%Y-%m-%d"),
            "type": "待看感想",
            "source": p.get("link", ""),
            "tags": [],
        })
        title = _sanitize(p.get("sum", "") or "待看")
        body = (f"{fm}\n\n# {p.get('sum','')}\n\n"
                f"- 星球：{p.get('planet','')} · {p.get('author','')} · {p.get('date','')}\n"
                f"- 原文：{p.get('link','')}\n\n## 我的感想\n{note}\n")
        path = config.KB_INBOX / f"{now:%Y-%m-%d-%H%M}-{title}.md"
        i = 2
        while path.exists():
            path = config.KB_INBOX / f"{now:%Y-%m-%d-%H%M}-{title}-{i}.md"
            i += 1
        path.write_text(body, encoding="utf-8")
        s["carded"][pid] = 1
        n += 1
    if n:
        save_state(s)
    return n


def search_kb(q, limit=40):
    q = (q or "").strip()
    if not q or not config.KB_DIR.exists():
        return []
    ql = q.lower()
    out = []
    for path in config.KB_DIR.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        idx = text.lower().find(ql)
        if idx < 0:
            continue
        a = max(0, idx - 30)
        snippet = text[a:idx + 70].replace("\n", " ")
        rel = str(path.relative_to(config.KB_DIR))
        out.append({"rel": rel, "path": str(path), "snippet": snippet})
        if len(out) >= limit:
            break
    return out


def save_upload(filename, data: bytes):
    config.KB_RESOURCES.mkdir(parents=True, exist_ok=True)
    name = _sanitize(Path(filename).stem) + Path(filename).suffix.lower()
    path = config.KB_RESOURCES / name
    i = 2
    while path.exists():
        path = config.KB_RESOURCES / (_sanitize(Path(filename).stem) + f"-{i}" + Path(filename).suffix.lower())
        i += 1
    path.write_bytes(data)
    return path.name


# ---------- Claude：整理成卡片 / 写口播 ----------
DRAFTS_DIR = config.KB_DRAFTS

ORGANIZE_SYSTEM = (
    "你是我的知识卡片铸造助手。我会给你一批我随手记在「灵感库」的零散笔记，以及我已有的卡片标题清单。\n"
    "任务：把这些零散笔记提炼成「知识卡片」。要求：\n"
    "- 讲同一件事的笔记合并成一张卡片；不要一条笔记硬凑一张。\n"
    "- 每张卡片正文用这个结构（Markdown，不含 # 标题行，标题单独放到 TITLE 里）：\n"
    "  ## 核心概念（用我自己的话讲清，别抄）\n"
    "  ## 为什么值得关注（为什么成立 + 因果链2-3步）\n"
    "  ## 能用在哪 / 不能用在哪\n"
    "  ## 最小行动（看完能马上做的一件事，≤20字）\n"
    "  最后一节，标题严格写作『## 和已有知识的连接』（不要在标题里加括号说明），其下严格写三行：\n"
    "    - 冲突：跟『我已有的卡片』里哪张观点打架？有就点名（用 [[标题]]）并一句话说哪里冲突；没有就写『暂无发现』，别硬凑。\n"
    "    - 会改变我手上哪件事：对照『我的项目』，说这张卡会让哪个项目的做法变化；都不改就写『暂不改变在做的事』。\n"
    "    - 相关卡片：把相关的已有卡片用 [[标题]] 列出（只能用清单里出现过的标题），没有就写『暂无』。\n"
    "- 精炼：抽掉任意一句话卡片还成立就删掉那句。\n"
    "输出格式（重要，别用 JSON，避免转义出错）：每张卡片之间用**单独一行** ===CARD=== 分隔；每张卡片：\n"
    "第一行  TITLE: 卡片标题\n"
    "第二行  TAG: 主题词（不带 #、不含空格）\n"
    "第三行起：卡片正文，从 ## 核心概念 开始（不要再写 # 标题，不要 frontmatter）。\n"
    "只输出卡片内容，不要任何开场白或结尾说明，也不要代码块围栏。"
)

KOUBAN_SYSTEM = (
    "你是帮我写口播稿的搭子。我要能直接对着镜头念、观众一听就懂的**大白话**稿子。铁律：\n"
    "- 大白话第一：像跟朋友唠嗑，不用行话、书面语、大词。任何一句，一个初中生听不懂，就重写。\n"
    "- **必须举例子/打比方**：每抛一个抽象观点，紧跟一个具体例子或生活化比方（把难懂的东西翻译成日常场景，"
    "像'API 就是餐厅服务员'那种），让人当场'看见'。宁可多举例，也别干讲道理。\n"
    "- 讲厚：把推理过程、小故事、真实感受揉进去，别只丢一句结论。\n"
    "- 用「你」不用「大家」；短句为主；开头 3 秒先甩一句反直觉或戳痛点的话把人钩住。\n"
    "- 结构：钩子 → 谁在什么场景下正被这个问题卡住 → 给方法（为什么管用，配一个例子）→ "
    "结尾给一个马上能做的小动作（别做总结）。\n"
    "- 不出现任何具体品牌名，用通用说法（如'命令行 AI 助手'）。\n"
    "- 350 字上下。只输出稿子正文，不要标题、不要小标题、不要旁白解说。"
)


def panel_chat(system, user, model, max_tokens=2500):
    from src import summarizer
    return summarizer.chat(system, user, max_tokens=max_tokens, temperature=0.4,
                           provider=config.PANEL_LLM_PROVIDER, model=model,
                           api_key=config.PANEL_LLM_API_KEY, base_url=config.PANEL_LLM_BASE_URL)


def list_inbox():
    if not config.KB_INBOX.exists():
        return []
    return [p for p in sorted(config.KB_INBOX.glob("*.md")) if not p.name.startswith("_")]


def list_areas():
    if not config.KB_AREAS.exists():
        return []
    return [p for p in sorted(config.KB_AREAS.glob("*.md")) if not p.name.startswith("_")]


def list_projects():
    d = config.KB_DIR / "01-Projects"
    if not d.exists():
        return []
    return [p for p in sorted(d.glob("*.md")) if not p.name.startswith("_")]


def _strip_fm(text: str) -> str:
    return re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)


def area_digest(exclude=None) -> str:
    """全部 Areas 卡片的「标题 :: 摘要」清单，给模型查冲突/连接用。"""
    lines = []
    for p in list_areas():
        if exclude and p.name == exclude:
            continue
        snippet = re.sub(r"\s+", " ", _strip_fm(p.read_text(encoding="utf-8"))).strip()[:160]
        lines.append(f"【{p.stem}】{snippet}")
    return "\n".join(lines)


def projects_text() -> str:
    out = []
    for p in list_projects():
        body = _strip_fm(p.read_text(encoding="utf-8")).strip()[:1500]
        out.append(f"【{p.stem}】\n{body}")
    return "\n\n".join(out)


def replace_section(text: str, heading: str, new_block: str) -> str:
    """把 `## {heading}...` 到下一个 `## ` 之间（或到文末）替换为 new_block；没有该节则追加到末尾。"""
    lines = text.splitlines(keepends=True)
    nb = new_block if new_block.endswith("\n") else new_block + "\n"
    start = next((i for i, ln in enumerate(lines) if ln.strip().startswith(f"## {heading}")), None)
    if start is None:
        sep = "" if text.endswith("\n") else "\n"
        return text + sep + "\n" + nb
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    tail = "".join(lines[end:])
    return "".join(lines[:start]) + nb + ("\n" + tail if tail else "")


RELINK_SYSTEM = (
    "你是我的知识库连接助手。我给你一张卡片、我全部已有卡片的『标题::摘要』清单、以及我的项目说明。\n"
    "只针对这一张卡片，判断三件事，用 JSON 返回：\n"
    "- conflict：这张卡跟清单里哪张卡观点打架？有就写清是哪张、哪里冲突；没有就写『暂无发现』，别硬凑。\n"
    "- impact：对照我的项目，这张卡会改变我手上哪件事的做法？都不改就写『暂不改变在做的事』。\n"
    "- links：相关的已有卡片标题数组（只能用清单里出现过的标题，最多6个，按相关度排）。\n"
    '只输出 JSON：{"conflict":"...","impact":"...","links":["标题A","标题B"]}，不要多余文字。'
)


def relink_card(name):
    """让 Claude 拿这张卡跟全库比对，自动把『冲突/会改哪件事/相关卡片[[]]』写回卡片的连接栏。"""
    p = config.KB_AREAS / name
    if not p.exists():
        return {"file": name, "ok": False, "err": "卡片不存在"}
    text = p.read_text(encoding="utf-8")
    user = (f"我的项目（判断 impact 用）：\n{projects_text() or '（暂无项目说明）'}\n\n"
            f"我全部已有卡片（标题::摘要，查冲突和 links 用，只能引用这里的标题）：\n{area_digest(exclude=name) or '（无其它卡片）'}\n\n"
            f"要分析的这张卡片：\n{_strip_fm(text).strip()[:1600]}\n\n只输出 JSON。")
    raw = panel_chat(RELINK_SYSTEM, user, config.PANEL_MODEL_ORGANIZE, max_tokens=1200)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        d = {}
    conflict = str(d.get("conflict") or "暂无发现").strip()
    impact = str(d.get("impact") or "暂不改变在做的事").strip()
    valid = {q.stem for q in list_areas()}
    links = [str(x).strip() for x in (d.get("links") or []) if str(x).strip() in valid][:6]
    linkline = " ".join(f"[[{l}]]" for l in links) if links else "暂无"
    block = (f"## 和已有知识的连接\n- 冲突：{conflict}\n"
             f"- 会改变我手上哪件事：{impact}\n- 相关卡片：{linkline}\n")
    p.write_text(replace_section(text, "和已有知识的连接", block), encoding="utf-8")
    return {"file": name, "ok": True, "conflict": conflict, "impact": impact, "links": links}


def _parse_cards(raw):
    """解析 ===CARD=== 分隔的卡片（比 JSON 抗造，不怕正文里的换行/引号）。"""
    cards = []
    for chunk in re.split(r"(?m)^\s*===CARD===\s*$", raw or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        mt = re.search(r"(?mi)^\s*TITLE:\s*(.+)$", chunk)
        if not mt:
            continue
        title = mt.group(1).strip()
        mg = re.search(r"(?mi)^\s*TAG:\s*(.+)$", chunk)
        tag = mg.group(1).strip() if mg else ""
        anchor = mg.end() if mg else mt.end()
        body = chunk[anchor:].strip()
        if not body:
            continue
        cards.append({"title": title, "tag": tag, "markdown": f"# {title}\n\n{body}"})
    return cards


def organize_inbox():
    if not config.panel_llm_configured():
        return {"ok": False, "err": "面板未配置 Claude（.env 的 PANEL_LLM_*）。"}
    notes = list_inbox()
    if not notes:
        return {"ok": False, "err": "灵感库(00-Inbox)是空的，先去速记几条再整理。"}
    blocks = [f"【{p.stem}】\n{p.read_text(encoding='utf-8').strip()}" for p in notes]
    user = (f"我的项目（01-Projects，用来判断『会改变哪件事』）：\n{projects_text() or '（暂无项目说明）'}\n\n"
            f"我已有的卡片（标题::摘要，用来查冲突和 [[双链]]，只能引用这里出现过的标题）：\n{area_digest() or '（还没有卡片）'}\n\n"
            f"我的零散原材料（灵感库）：\n" + "\n\n".join(blocks) + "\n\n请整理成知识卡片，只输出 JSON。")
    raw = panel_chat(ORGANIZE_SYSTEM, user, config.PANEL_MODEL_ORGANIZE, max_tokens=16000)
    cards = _parse_cards(raw)
    if not cards:
        return {"ok": False, "err": "这次没提炼出卡片（可能笔记太少太零碎，先多攒几条再整理）。"}
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    written = []
    for c in cards:
        title = _sanitize(c.get("title") or "未命名卡片")
        md = (c.get("markdown") or c.get("body") or "").strip()
        if not md:
            continue
        tag = (c.get("tag") or "").strip().lstrip("#").replace(" ", "")
        fm = make_frontmatter({
            "created": today,
            "source": "00-Inbox 整理",
            "tags": [tag] if tag else [],
        })
        body = f"{fm}\n\n{md}\n"
        path = DRAFTS_DIR / (title + ".md")
        i = 2
        while path.exists():
            path = DRAFTS_DIR / (title + f"-{i}.md")
            i += 1
        path.write_text(body + "\n", encoding="utf-8")
        written.append(path.name)
    return {"ok": True, "n": len(written), "cards": written, "used": [p.name for p in notes]}


def write_kouban(files):
    if not config.panel_llm_configured():
        return {"ok": False, "err": "面板未配置 Claude（.env 的 PANEL_LLM_*）。"}
    texts = []
    for name in files or []:
        p = config.KB_AREAS / name
        if p.exists():
            texts.append(p.read_text(encoding="utf-8"))
    if not texts:
        return {"ok": False, "err": "没选到卡片。"}
    user = "根据下面的知识卡片写一条口播稿：\n\n" + "\n\n---\n\n".join(texts)
    text = panel_chat(KOUBAN_SYSTEM, user, config.PANEL_MODEL_WRITE, max_tokens=2000).strip()
    # 只生成、不落盘：先给用户看/改，满意再 /write/save 保存
    return {"ok": True, "title": _sanitize(Path(files[0]).stem), "text": text}


def save_kouban(title, text):
    """把用户过目/改好的口播稿保存到 _草稿。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "err": "内容为空，没保存。"}
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    title = _sanitize(title or "口播")
    path = DRAFTS_DIR / ("口播-" + title + ".md")
    i = 2
    while path.exists():
        path = DRAFTS_DIR / ("口播-" + title + f"-{i}.md")
        i += 1
    path.write_text(f"# 口播：{title}\n\n{text}\n", encoding="utf-8")
    return {"ok": True, "file": path.name}


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path.rstrip("/")
        if p in ("", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif p == "/recent":
            self._json({"files": recent()})
        elif p == "/reading/data":
            posts = dedup_posts(load_posts())
            with _state_lock:
                s = materialize(posts, load_state())
            self._json({"posts": posts, "status": s["status"], "notes": s["notes"],
                        "carded": s["carded"], "updated": reading_updated()})
        elif p == "/search":
            q = urllib.parse.parse_qs(u.query).get("q", [""])[0]
            self._json({"hits": search_kb(q)})
        elif p == "/areas/list":
            self._json({"cards": [x.name for x in list_areas()], "inbox": len(list_inbox()),
                        "claude": config.panel_llm_configured()})
        elif p == "/open":
            fp = urllib.parse.parse_qs(u.query).get("path", [""])[0]
            try:
                cand = Path(fp)
                if not cand.is_absolute():          # 允许传知识库内相对路径，如 _草稿/xxx.md
                    cand = config.KB_DIR / fp
                rp = cand.resolve()
                rp.relative_to(config.KB_DIR.resolve())  # 只允许打开知识库内文件
                os.startfile(str(rp))                    # noqa: Windows
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "err": str(e)}, 400)
        else:
            self._json({"ok": False, "err": "not found"}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            if p == "/save":
                fn = save_note(json.loads(self._body().decode("utf-8") or "{}"))
                self._json({"ok": True, "file": fn})
            elif p == "/reading/set":
                d = json.loads(self._body().decode("utf-8") or "{}")
                pid, field, val = d.get("id"), d.get("field"), d.get("value")
                with _state_lock:  # 串行化，避免并发保存互相覆盖
                    s = load_state()
                    if field == "status":
                        s["status"][pid] = val
                    elif field == "note":
                        s["notes"][pid] = val
                    save_state(s)
                self._json({"ok": True})
            elif p == "/reading/import":
                d = json.loads(self._body().decode("utf-8") or "{}")
                with _state_lock:
                    s = load_state()
                    for k in ("status", "notes", "carded"):
                        if isinstance(d.get(k), dict):
                            s[k].update(d[k])
                    s["_inited"] = True
                    save_state(s)
                self._json({"ok": True, "n": len(d.get("status", {}))})
            elif p == "/reading/send":
                self._json({"ok": True, "n": send_reading_to_organize()})
            elif p == "/upload":
                fn = urllib.parse.unquote(self.headers.get("X-Filename", "file"))
                name = save_upload(fn, self._body())
                self._json({"ok": True, "file": name})
            elif p == "/organize":
                self._json(organize_inbox())
            elif p == "/write":
                d = json.loads(self._body().decode("utf-8") or "{}")
                self._json(write_kouban(d.get("files") or []))
            elif p == "/write/save":
                d = json.loads(self._body().decode("utf-8") or "{}")
                self._json(save_kouban(d.get("title"), d.get("text")))
            elif p == "/relink":
                d = json.loads(self._body().decode("utf-8") or "{}")
                self._json({"ok": True, "results": [relink_card(n) for n in (d.get("files") or [])]})
            else:
                self._json({"ok": False, "err": "not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "err": str(e)}, 400)


PAGE = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>知识库驾驶舱</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f7;color:#2b2b2b;line-height:1.7}
.wrap{max-width:760px;margin:0 auto;padding:22px 18px 90px}
h1{font-size:22px;margin:0 0 12px}
.tabs{display:flex;gap:6px;margin-bottom:16px;flex-wrap:wrap}
.tab{padding:7px 15px;border-radius:20px;background:#e8ebee;color:#5b6b7a;font-size:14px;cursor:pointer;user-select:none}
.tab.on{background:#1a6dc4;color:#fff}
section{display:none}section.on{display:block}
.card{background:#fff;border-radius:12px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:12px}
label{display:block;font-size:12.5px;color:#8a8a8a;margin:12px 0 4px}label:first-child{margin-top:0}
input,select,textarea{width:100%;border:1px solid #e3e6ea;border-radius:8px;padding:9px 11px;font-size:14px;font-family:inherit;color:#333;background:#fcfcfb}
input:focus,select:focus,textarea:focus{outline:none;border-color:#1a6dc4;background:#fff}
textarea{min-height:120px;resize:vertical}
.row{display:flex;gap:10px}.row>div{flex:1}
.btn{margin-top:14px;padding:10px 16px;border:none;border-radius:8px;background:#1a6dc4;color:#fff;font-size:14.5px;font-weight:600;cursor:pointer}
.btn:hover{background:#155aa8}.btn:disabled{background:#c7ced6}
.btn.sm{padding:7px 13px;font-size:13px;margin-top:0}
.toast{margin-top:10px;font-size:13px;color:#37a06a;min-height:18px}
.muted{color:#8a8a8a;font-size:12.5px}
.stat{font-size:14px;color:#1a6dc4;font-weight:600;margin:2px 0 10px}
.item{background:#fff;border-radius:11px;padding:12px 15px;margin-bottom:10px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.item.done{opacity:.55}
.ititle{font-size:15px;font-weight:600}.ititle a{color:#173a63;text-decoration:none}.ititle a:hover{text-decoration:underline}
.imeta{color:#999;font-size:12px;margin:3px 0 7px}
.tag{display:inline-block;background:#eef2f7;color:#5b6b7a;border-radius:10px;padding:1px 8px;font-size:11.5px;margin-left:6px}
.row2{display:flex;align-items:center;justify-content:space-between;margin-top:6px}
.tplbtn{font-size:12px;color:#1a6dc4;cursor:pointer;user-select:none}
.chk{display:flex;align-items:center;gap:6px;font-size:13.5px;color:#555;cursor:pointer}.chk input{width:17px;height:17px}
details{margin-top:16px}summary{cursor:pointer;color:#888;font-size:13.5px}
#drop{border:2px dashed #c3ccd6;border-radius:12px;padding:38px 16px;text-align:center;color:#8a94a0;background:#fafbfc}
#drop.hot{border-color:#1a6dc4;background:#eef5fd;color:#1a6dc4}
.hit{background:#fff;border-radius:10px;padding:10px 13px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,.05);cursor:pointer}
.hit:hover{background:#f0f6ff}.hit .p{font-size:12px;color:#1a6dc4}.hit .s{font-size:13px;color:#555;margin-top:2px}
.recent{margin-top:8px}.recent li{list-style:none;font-size:12.5px;color:#7a8794;padding:3px 0;border-top:1px dashed #e6e9ec}
</style></head><body><div class="wrap">
<h1>🧭 知识库驾驶舱</h1>
<div class="tabs">
  <div class="tab on" data-t="jot">✍️ 速记</div>
  <div class="tab" data-t="read">📖 待看</div>
  <div class="tab" data-t="file">📎 拖文件</div>
  <div class="tab" data-t="find">🔍 搜索</div>
  <div class="tab" data-t="orga">🔗 整理</div>
  <div class="tab" data-t="pub">🎬 出稿</div>
  <div class="tab" data-t="link">🧩 连接</div>
</div>

<section id="jot" class="on"><div class="card">
  <div class="row">
    <div><label>类型</label><select id="j_type">
      <option>灵感/手记</option><option>网页剪藏</option><option>金句摘录</option><option>其他</option></select></div>
    <div><label>标题（可留空，自动取首句）</label><input id="j_title" placeholder="一句话概括"></div>
  </div>
  <label>来源链接（剪藏时填，可留空）</label><input id="j_url" placeholder="https://…">
  <label>正文 *</label><textarea id="j_body" placeholder="今天读到的一句话 / 一个想法 / 一个问题…（Ctrl+Enter 保存）"></textarea>
  <button class="btn" id="j_save">保存到 Inbox</button>
  <div class="toast" id="j_toast"></div>
</div><div class="card"><div class="muted" style="margin-bottom:6px">最近记的</div><ul class="recent" id="j_recent"></ul></div></section>

<section id="read"><div class="stat" id="r_stat"></div>
  <div style="margin-bottom:12px">
    <button class="btn sm" id="r_export" disabled>📤 送去整理</button>
    <label class="muted" style="display:inline-block;margin-left:10px;cursor:pointer">导入旧记录
      <input type="file" id="r_import" accept="application/json" style="display:none"></label>
  </div>
  <div id="r_list"></div>
  <details id="r_donebox"><summary></summary><div id="r_donelist"></div></details>
</section>

<section id="file"><div class="card">
  <div id="drop">把本地文件 / PDF 拖到这里<br><span class="muted">存进知识库 03-Resources，之后可让 Claude 读它做成卡片</span></div>
  <div class="toast" id="f_toast"></div>
  <div id="f_note" style="display:none;margin-top:12px">
    <label>刚存了文件，顺手记一句「想从它提取什么」（可留空）</label>
    <textarea id="f_body" style="min-height:70px" placeholder="比如：提取第3章关于定价的方法，做成卡片"></textarea>
    <button class="btn sm" id="f_savenote">记进 Inbox</button>
  </div>
</div></section>

<section id="find"><div class="card">
  <input id="q" placeholder="搜全库（标题/正文/卡片/笔记）… 回车搜索" autocomplete="off">
  <div class="muted" style="margin-top:6px">点结果用默认程序打开对应文件</div>
</div><div id="hits"></div></section>

<section id="orga"><div class="card">
  <div class="muted" id="o_info">读取灵感库…</div>
  <button class="btn" id="o_run" disabled>🔗 整理 Inbox 成卡片（Claude）</button>
  <div class="toast" id="o_toast"></div>
  <div class="muted" style="margin-top:6px">生成的卡片草稿放进知识库 <b>_草稿</b> 文件夹。去 Obsidian 审一遍，满意的拖进 02-Areas，Inbox 原稿可清掉。</div>
</div><div id="o_result"></div></section>

<section id="pub"><div class="card">
  <div class="muted">勾选要出稿的卡片（02-Areas）：</div>
  <div id="p_cards" style="max-height:300px;overflow:auto;margin:8px 0"></div>
  <button class="btn" id="p_run" disabled>🎬 写口播（Claude）</button>
  <div class="toast" id="p_toast"></div>
</div><div id="p_out"></div></section>

<section id="link"><div class="card">
  <div class="muted">勾选卡片（02-Areas）。Claude 会拿它跟全库 + 你的项目比一遍，自动把「冲突 / 会改哪件事 / 相关卡片[[]]」写回卡片的连接栏。</div>
  <div id="k_cards" style="max-height:300px;overflow:auto;margin:8px 0"></div>
  <button class="btn" id="k_run" disabled>🔎 查连接/冲突（Claude）</button>
  <div class="toast" id="k_toast"></div>
</div><div id="k_out"></div></section>

</div>
<script>
const $=id=>document.getElementById(id);
const jNL=(s)=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const TPL="标题：\n\n核心概念（用你自己的话，别抄书）：\n\n为什么值得关注（因果链2-3步）：\n\n能用在哪 / 不能用在哪：\n\n最小行动（≤20字）：\n\n和已有知识的连接：\n";
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));t.classList.add('on');
  document.querySelectorAll('section').forEach(s=>s.classList.remove('on'));$(t.dataset.t).classList.add('on');
  if(t.dataset.t==='read')loadReading();
  if(t.dataset.t==='orga')loadOrganize();
  if(t.dataset.t==='pub')loadAreas();
  if(t.dataset.t==='link')loadLink();
});
function bindOpen(sel){document.querySelectorAll(sel).forEach(el=>el.onclick=()=>fetch('/open?path='+encodeURIComponent(el.dataset.p)));}

// —— 速记 ——
async function loadRecent(){try{const d=await (await fetch('/recent')).json();
  $('j_recent').innerHTML=(d.files||[]).map(f=>'<li>'+jNL(f)+'</li>').join('')||'<li style="color:#bbb">还没有</li>';}catch(e){}}
async function jotSave(){
  const body=$('j_body').value.trim(),title=$('j_title').value.trim();
  if(!body&&!title){$('j_toast').textContent='写点什么再保存吧';return;}
  $('j_save').disabled=true;
  try{const d=await (await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type:$('j_type').value,title,url:$('j_url').value.trim(),body})})).json();
    if(d.ok){$('j_toast').textContent='✓ 已保存：'+d.file;$('j_body').value='';$('j_title').value='';$('j_url').value='';loadRecent();$('j_body').focus();}
    else $('j_toast').textContent='× '+(d.err||'失败');
  }catch(e){$('j_toast').textContent='× '+e.message;}
  $('j_save').disabled=false;
}
$('j_save').onclick=jotSave;
$('j_body').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter')jotSave();});

// —— 待看 ——
let RD={posts:[],status:{},notes:{},carded:{}};
async function loadReading(){
  const d=await (await fetch('/reading/data')).json();RD=d;renderReading();
}
function cardableCount(){return RD.posts.filter(p=>((RD.notes[p.id]||'').trim())&&!RD.carded[p.id]).length;}
function rcard(p,isDone){
  const nv=RD.notes[p.id]||'';
  return '<div class="item'+(isDone?' done':'')+'" data-id="'+jNL(p.id)+'">'
   +'<div class="ititle"><a href="'+p.link+'" target="_blank" rel="noopener">'+jNL(p.sum)+'</a>'
   +(p.topic?'<span class="tag">'+jNL(p.topic)+'</span>':'')+'</div>'
   +'<div class="imeta">'+jNL(p.date)+' · '+jNL(p.planet)+'·'+jNL(p.author)+' · 赞'+p.likes+'</div>'
   +'<textarea placeholder="写点感想…（点下方填入卡片模板）">'+jNL(nv)+'</textarea>'
   +'<div class="row2"><span class="tplbtn">＋ 填入知识卡片模板</span>'
   +'<label class="chk"><input type="checkbox"'+(isDone?' checked':'')+'>看了</label></div></div>';
}
function renderReading(){
  const queue=RD.posts.filter(p=>RD.status[p.id]==='queue');
  const done=RD.posts.filter(p=>RD.status[p.id]==='done');
  $('r_stat').textContent='待看 '+queue.length+' 条　·　已看 '+done.length+' 条　·　更新于 '+(RD.updated||'—');
  $('r_list').innerHTML=queue.length?queue.map(p=>rcard(p,false)).join(''):'<div class="muted" style="text-align:center;padding:30px 0">🎉 待看清空了</div>';
  $('r_donebox').style.display=done.length?'block':'none';
  $('r_donebox').querySelector('summary').textContent='已看（'+done.length+'）— 点开可撤销';
  $('r_donelist').innerHTML=done.slice(0,120).map(p=>rcard(p,true)).join('');
  const n=cardableCount();$('r_export').textContent='📤 送去整理'+(n?'（'+n+'）':'');$('r_export').disabled=!n;
  bindReading();
}
let noteTimer={};
function bindReading(){
  document.querySelectorAll('#read .item').forEach(el=>{
    const id=el.dataset.id,ta=el.querySelector('textarea');
    ta.oninput=()=>{RD.notes[id]=ta.value;clearTimeout(noteTimer[id]);
      noteTimer[id]=setTimeout(()=>{fetch('/reading/set',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({id,field:'note',value:ta.value})});
        const n=cardableCount();$('r_export').textContent='📤 送去整理'+(n?'（'+n+'）':'');$('r_export').disabled=!n;},500);};
    el.querySelector('.tplbtn').onclick=()=>{ta.value=ta.value.trim()?ta.value.replace(/\s*$/,'')+"\n\n"+TPL:TPL;ta.focus();
      RD.notes[id]=ta.value;fetch('/reading/set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,field:'note',value:ta.value})});};
    el.querySelector('input[type=checkbox]').onchange=async e=>{
      RD.status[id]=e.target.checked?'done':'queue';
      await fetch('/reading/set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,field:'status',value:RD.status[id]})});
      renderReading();};
  });
}
$('r_export').onclick=async()=>{
  const d=await (await fetch('/reading/send',{method:'POST'})).json();
  alert(d.ok?('已把 '+d.n+' 条送去整理（在 00-Inbox）。去「🔗 整理」点一下，跟速记内容一起成卡。'):('失败：'+d.err));loadReading();};
$('r_import').onchange=e=>{const f=e.target.files[0];if(!f)return;const rd=new FileReader();
  rd.onload=async()=>{try{const d=await (await fetch('/reading/import',{method:'POST',headers:{'Content-Type':'application/json'},body:rd.result})).json();
    alert(d.ok?('已导入旧记录（'+d.n+' 条状态）。'):('失败：'+d.err));loadReading();}catch(err){alert('文件格式不对：'+err.message);}};rd.readAsText(f);};

// —— 拖文件 ——
const drop=$('drop');
;['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('hot');}));
;['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('hot');}));
drop.addEventListener('drop',async e=>{
  const files=[...e.dataTransfer.files];if(!files.length)return;
  $('f_toast').textContent='上传中…';const names=[];
  for(const f of files){
    const r=await fetch('/upload',{method:'POST',headers:{'X-Filename':encodeURIComponent(f.name)},body:f});
    const d=await r.json();if(d.ok)names.push(d.file);
  }
  $('f_toast').textContent='✓ 已存进 03-Resources：'+names.join('、');
  $('f_note').style.display='block';$('f_note').dataset.files=names.join('、');$('f_body').focus();
});
$('f_savenote').onclick=async()=>{
  const files=$('f_note').dataset.files||'';const body='文件：'+files+'\n\n'+$('f_body').value.trim();
  await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'待读文件/PDF',title:'待读：'+files,url:'',body})});
  $('f_toast').textContent='✓ 已记进 Inbox';$('f_body').value='';$('f_note').style.display='none';
};

// —— 搜索 ——
$('q').addEventListener('keydown',async e=>{if(e.key!=='Enter')return;
  const q=$('q').value.trim();if(!q){$('hits').innerHTML='';return;}
  const d=await (await fetch('/search?q='+encodeURIComponent(q))).json();
  $('hits').innerHTML=(d.hits||[]).map(h=>'<div class="hit" data-p="'+jNL(h.path)+'"><div class="p">'+jNL(h.rel)+'</div><div class="s">…'+jNL(h.snippet)+'…</div></div>').join('')
    ||'<div class="muted" style="padding:10px">没找到。换个词试试。</div>';
  bindOpen('.hit');
});

// —— 整理 Inbox 成卡片（Claude）——
async function loadOrganize(){
  const d=await (await fetch('/areas/list')).json();
  $('o_info').textContent=d.claude?('灵感库有 '+d.inbox+' 条待整理'):'⚠️ 未配置 Claude（.env 的 PANEL_LLM_*），无法整理';
  $('o_run').disabled=!d.claude||!d.inbox;
}
$('o_run').onclick=async()=>{
  $('o_run').disabled=true;$('o_toast').textContent='整理中…Claude 思考约 10–40 秒，别关窗口';$('o_result').innerHTML='';
  try{const d=await (await fetch('/organize',{method:'POST'})).json();
    if(d.ok){$('o_toast').textContent='✓ 生成 '+d.n+' 张卡片草稿（在 _草稿 文件夹）';
      $('o_result').innerHTML=d.cards.map(f=>'<div class="hit" data-p="'+jNL('_草稿/'+f)+'"><div class="p">_草稿 / '+jNL(f)+'（点开）</div></div>').join('');
      bindOpen('#o_result .hit');}
    else{$('o_toast').textContent='× '+d.err;}
  }catch(e){$('o_toast').textContent='× '+e.message;}
  loadOrganize();
};

// —— 找卡片 → 写口播（Claude）——
async function loadAreas(){
  const d=await (await fetch('/areas/list')).json();
  if(!d.claude){$('p_cards').innerHTML='<div class="muted">⚠️ 未配置 Claude（.env 的 PANEL_LLM_*）</div>';return;}
  $('p_cards').innerHTML=d.cards.length?d.cards.map(c=>'<label class="chk" style="display:flex;padding:4px 0"><input type="checkbox" value="'+jNL(c)+'"> '+jNL(c.replace(/\.md$/,''))+'</label>').join(''):'<div class="muted">卡片区(02-Areas)还没有卡片</div>';
  document.querySelectorAll('#p_cards input').forEach(cb=>cb.onchange=()=>{$('p_run').disabled=!document.querySelectorAll('#p_cards input:checked').length;});
}
$('p_run').onclick=async()=>{
  const files=[...document.querySelectorAll('#p_cards input:checked')].map(x=>x.value);
  if(!files.length)return;
  $('p_run').disabled=true;$('p_toast').textContent='Claude 写稿中…约 10–30 秒';$('p_out').innerHTML='';
  try{const d=await (await fetch('/write',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files})})).json();
    if(d.ok){$('p_toast').textContent='✓ 写好了——先看/改，满意再保存（现在还没进知识库）';
      $('p_out').innerHTML='<div class="card"><div class="muted">草稿标题：'+jNL(d.title)+'　·　下面可直接改</div>'
        +'<textarea id="p_text" style="min-height:260px;margin-top:8px">'+jNL(d.text)+'</textarea>'
        +'<button class="btn sm" id="p_save">💾 保存到草稿</button> <span class="muted" id="p_saved"></span></div>';
      window._kbTitle=d.title;
      document.getElementById('p_save').onclick=async()=>{
        const r=await (await fetch('/write/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:window._kbTitle,text:document.getElementById('p_text').value})})).json();
        if(r.ok){document.getElementById('p_saved').innerHTML='✓ 已保存：<span class="p" data-p="'+jNL('_草稿/'+r.file)+'" style="cursor:pointer;color:#1a6dc4">'+jNL(r.file)+'（点开）</span>';bindOpen('#p_saved .p');}
        else document.getElementById('p_saved').textContent='× '+(r.err||'失败');
      };}
    else $('p_toast').textContent='× '+d.err;
  }catch(e){$('p_toast').textContent='× '+e.message;}
  $('p_run').disabled=false;
};

// —— 查连接/冲突（Claude 扫全库，自动写回卡片）——
async function loadLink(){
  const d=await (await fetch('/areas/list')).json();
  if(!d.claude){$('k_cards').innerHTML='<div class="muted">⚠️ 未配置 Claude</div>';return;}
  $('k_cards').innerHTML=d.cards.length?d.cards.map(c=>'<label class="chk" style="display:flex;padding:4px 0"><input type="checkbox" value="'+jNL(c)+'"> '+jNL(c.replace(/\.md$/,''))+'</label>').join(''):'<div class="muted">卡片区(02-Areas)还没有卡片</div>';
  document.querySelectorAll('#k_cards input').forEach(cb=>cb.onchange=()=>{$('k_run').disabled=!document.querySelectorAll('#k_cards input:checked').length;});
}
$('k_run').onclick=async()=>{
  const files=[...document.querySelectorAll('#k_cards input:checked')].map(x=>x.value);
  if(!files.length)return;
  $('k_run').disabled=true;$('k_toast').textContent='Claude 扫全库中…每张约 5–15 秒，别关窗口';$('k_out').innerHTML='';
  try{const d=await (await fetch('/relink',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files})})).json();
    if(d.ok){$('k_toast').textContent='✓ 已扫描 '+d.results.length+' 张，连接已写回卡片（去 Obsidian 看反向链接/图谱）';
      $('k_out').innerHTML=d.results.map(r=>{
        if(!r.ok)return '<div class="card"><b>'+jNL(r.file)+'</b>：'+jNL(r.err||'失败')+'</div>';
        const cf=(r.conflict&&r.conflict!=='暂无发现');
        return '<div class="card"><div class="ititle">'+jNL(r.file.replace(/\.md$/,''))+'</div>'
          +'<div style="margin-top:6px'+(cf?';color:#c0392b;font-weight:600':'')+'">冲突：'+jNL(r.conflict)+'</div>'
          +'<div>会改哪件事：'+jNL(r.impact)+'</div>'
          +'<div>相关卡片：'+(r.links.length?r.links.map(x=>'[['+jNL(x)+']]').join(' '):'暂无')+'</div></div>';
      }).join('');}
    else $('k_toast').textContent='× '+(d.err||'失败');
  }catch(e){$('k_toast').textContent='× '+e.message;}
  loadLink();
};
loadRecent();
</script></body></html>"""


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = f"http://localhost:{PORT}/"
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"知识库驾驶舱已启动：{url}")
    print(f"知识库目录：{config.KB_DIR}")
    print("关闭此窗口即停止。")
    if "--no-browser" not in sys.argv:   # 开机自启（后台服务）时不弹浏览器
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
