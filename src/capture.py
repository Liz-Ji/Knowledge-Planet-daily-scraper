"""本地速记小应用：一个网页界面，输入即落盘到知识库 00-Inbox，不经过任何 chatbox。

用法（双击 scripts\\capture.ps1，或直接跑）：
    .venv\\Scripts\\python.exe src\\capture.py
启动后自动打开浏览器 http://localhost:8824/ 。填内容点保存 → 立刻写成一条 md 到
知识库的 00-Inbox。手记/灵感、网页剪藏都在这记；PDF/本地文件把文件拖进 03-Resources
文件夹，再在这里记一条「我想从它提取什么」即可。

用 Python 标准库，无新依赖。关掉窗口即停。
"""
import sys, json, re, webbrowser, logging
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src import config

PORT = 8824


def _sanitize(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n]+', " ", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:40] or "速记"


def _yaml_scalar(v) -> str:
    """把一个值渲染成安全的 YAML 标量：含特殊字符（含 : / URL）就加引号。"""
    s = "" if v is None else str(v)
    if s == "":
        return ""
    if any(c in s for c in ':#[]{},&*!|>"%@`') or s != s.strip():
        return '"' + s.replace('"', '\\"') + '"'
    return s


def make_frontmatter(fields: dict) -> str:
    """生成 Obsidian YAML frontmatter 块（供手写模板与自动生成卡片统一格式）。"""
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            lines.append(f"{k}: [" + ", ".join(_yaml_scalar(x) for x in v) + "]")
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines)


def save_note(data: dict) -> str:
    """把一条速记写成 md 落到 00-Inbox，返回文件名。"""
    typ = (data.get("type") or "灵感/手记").strip()
    title = (data.get("title") or "").strip()
    url = (data.get("url") or "").strip()
    body = (data.get("body") or "").strip()
    if not body and not title:
        raise ValueError("正文和标题不能都为空")

    now = datetime.now()
    head = title or body.splitlines()[0][:40]
    fname = f"{now:%Y-%m-%d-%H%M}-{_sanitize(head)}.md"

    fm = make_frontmatter({
        "created": f"{now:%Y-%m-%d}",
        "type": typ,
        "source": url,
        "tags": [],
    })
    text = f"{fm}\n\n# {head}\n\n{body}\n"

    config.KB_INBOX.mkdir(parents=True, exist_ok=True)
    path = config.KB_INBOX / fname
    # 极小概率同分钟同标题重名，加序号避免覆盖
    i = 2
    while path.exists():
        path = config.KB_INBOX / f"{now:%Y-%m-%d-%H%M}-{_sanitize(head)}-{i}.md"
        i += 1
    path.write_text(text, encoding="utf-8")
    return path.name


def recent(n: int = 8):
    if not config.KB_INBOX.exists():
        return []
    files = [p for p in config.KB_INBOX.glob("*.md") if not p.name.startswith("_")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in files[:n]]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默默认访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path.startswith("/recent"):
            self._send(200, json.dumps({"files": recent()}, ensure_ascii=False))
        else:
            self._send(404, json.dumps({"ok": False, "err": "not found"}))

    def do_POST(self):
        if self.path.rstrip("/") != "/save":
            self._send(404, json.dumps({"ok": False, "err": "not found"}))
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            fname = save_note(data)
            self._send(200, json.dumps({"ok": True, "file": fname}, ensure_ascii=False))
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "err": str(e)}, ensure_ascii=False))


PAGE = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>速记 · 知识库</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f7;color:#2b2b2b;line-height:1.7}
.wrap{max-width:680px;margin:0 auto;padding:26px 18px 80px}
h1{font-size:23px;margin:0 0 2px}.sub{color:#8a8a8a;font-size:13px;margin-bottom:18px}
.card{background:#fff;border-radius:12px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
label{display:block;font-size:12.5px;color:#8a8a8a;margin:12px 0 4px}
label:first-child{margin-top:0}
input,select,textarea{width:100%;border:1px solid #e3e6ea;border-radius:8px;padding:9px 11px;font-size:14px;font-family:inherit;color:#333;background:#fcfcfb}
input:focus,select:focus,textarea:focus{outline:none;border-color:#1a6dc4;background:#fff}
textarea{min-height:150px;resize:vertical}
.row{display:flex;gap:10px}.row>div{flex:1}
.btn{margin-top:16px;width:100%;padding:11px;border:none;border-radius:8px;background:#1a6dc4;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
.btn:hover{background:#155aa8}.btn:disabled{background:#c7ced6}
.toast{margin-top:12px;font-size:13px;color:#37a06a;min-height:18px}
.recent{margin-top:22px}.recent h2{font-size:13px;color:#8a8a8a;font-weight:600;margin:0 0 8px}
.recent ul{list-style:none;margin:0;padding:0}.recent li{font-size:12.5px;color:#7a8794;padding:3px 0;border-top:1px dashed #e6e9ec}
.tip{color:#aaa;font-size:12px;margin-top:10px}
kbd{background:#eef2f7;border-radius:4px;padding:1px 5px;font-size:11px;color:#5b6b7a}
</style></head><body><div class="wrap">
<h1>✍️ 速记</h1>
<div class="sub">写下就落进知识库 00-Inbox。周末让 AI 把它们连成卡片。</div>
<div class="card">
  <div class="row">
    <div><label>类型</label>
      <select id="type">
        <option>灵感/手记</option><option>网页剪藏</option>
        <option>待读文件/PDF</option><option>金句摘录</option><option>其他</option>
      </select></div>
    <div><label>标题（可留空，自动取首句）</label><input id="title" placeholder="一句话概括"></div>
  </div>
  <label>来源链接（剪藏时填，可留空）</label><input id="url" placeholder="https://…">
  <label>正文 *</label>
  <textarea id="body" placeholder="今天读到的一句话 / 一个想法 / 一个问题…&#10;（Ctrl+Enter 保存）"></textarea>
  <button class="btn" id="save">保存到 Inbox</button>
  <div class="toast" id="toast"></div>
  <div class="tip">待读文件/PDF：先把文件拖进知识库的 <b>03-Resources</b> 文件夹，再在这记一条「想从它提取什么」。</div>
</div>
<div class="recent"><h2>最近记的</h2><ul id="recent"></ul></div>
</div>
<script>
const $=id=>document.getElementById(id);
async function loadRecent(){
  try{const r=await fetch('/recent');const d=await r.json();
    $('recent').innerHTML=(d.files||[]).map(f=>'<li>'+f.replace(/</g,'&lt;')+'</li>').join('')||'<li style="color:#bbb">还没有</li>';
  }catch(e){}
}
async function save(){
  const body=$('body').value.trim(), title=$('title').value.trim();
  if(!body && !title){$('toast').textContent='写点什么再保存吧';return;}
  $('save').disabled=true;
  try{
    const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type:$('type').value,title:title,url:$('url').value.trim(),body:body})});
    const d=await r.json();
    if(d.ok){$('toast').textContent='✓ 已保存：'+d.file;$('body').value='';$('title').value='';$('url').value='';loadRecent();$('body').focus();}
    else{$('toast').textContent='× 保存失败：'+(d.err||'未知错误');}
  }catch(e){$('toast').textContent='× 出错：'+e.message;}
  $('save').disabled=false;
}
$('save').addEventListener('click',save);
$('body').addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter')save();});
loadRecent();$('body').focus();
</script></body></html>"""


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = f"http://localhost:{PORT}/"
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"速记已启动：{url}")
    print(f"写入目录：{config.KB_INBOX}")
    print("关闭此窗口即停止。")
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
