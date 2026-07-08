"""生成「今日待看」HTML：一个按最新在前排列的待看队列，可勾选已看、可写感想。

待看队列 = 之前没看完的（未勾"看了"）+ 最新跑出来的（新抓到的自动进队列）。
- 勾"看了" → 移出队列，下次不再出现；不勾 → 下次继续待看。
- 每条可写「感想」（含知识卡片模板），已看/感想都存在本机浏览器 localStorage，
  每天重建文件也不会丢（同一文件路径，localStorage 保留）。
- 首次打开：最近14天的内容进待看，更早的历史自动归档（不刷屏）；之后每天新内容自动进队列。

由 main.py 每日抓取成功后自动重建（和知识图谱一起）。也可手动：
    python src/build_reading.py
输出：项目根目录 今日待看.html
"""
import sys, json, logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.feishu_client import FeishuClient

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "今日待看.html"


def to_int(v):
    try: return int(float(v))
    except (TypeError, ValueError): return 0


def build():
    feishu = FeishuClient(config.FEISHU_APP_ID, config.FEISHU_APP_SECRET,
                          config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID)
    items = feishu.list_all_records()
    posts = []
    for it in items:
        fd = it["fields"]
        summ = fd.get("摘要") or (fd.get("正文", "") or "")[:60]
        if not summ:
            continue
        t = fd.get("发布时间") or 0
        posts.append({
            "id": str(fd.get("帖子ID", "")),
            "ts": t, "date": f"{datetime.fromtimestamp(t/1000):%Y-%m-%d}" if t else "",
            "planet": fd.get("星球名称", ""), "author": fd.get("作者", "") or "（佚名）",
            "topic": fd.get("专题", "") or "", "likes": to_int(fd.get("点赞数")),
            "sum": summ, "link": (fd.get("原文链接") or {}).get("link", ""),
        })
    posts.sort(key=lambda p: p["ts"], reverse=True)  # 最新在前
    html = TEMPLATE.replace("__DATA__", json.dumps(posts, ensure_ascii=False)) \
                   .replace("__UPDATED__", f"{datetime.now():%Y-%m-%d %H:%M}") \
                   .replace("__TOTAL__", str(len(posts)))
    OUT.write_text(html, encoding="utf-8")
    logging.info(f"今日待看已生成: {OUT}（候选 {len(posts)} 条）")


TEMPLATE = r'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>今日待看</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f7;color:#2b2b2b;line-height:1.7}
.wrap{max-width:820px;margin:0 auto;padding:24px 18px 100px}
h1{font-size:24px;font-weight:700;margin:0 0 2px}
.sub{color:#8a8a8a;font-size:13px;margin-bottom:4px}
.hint{color:#aaa;font-size:12.5px;margin-bottom:16px}
.stat{font-size:14px;color:#1a6dc4;font-weight:600;margin-bottom:12px}
.item{background:#fff;border-radius:12px;padding:14px 16px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.item.done{opacity:.55}
.top{display:flex;align-items:flex-start;gap:10px}
.title{flex:1;font-size:16px;font-weight:600}
.title a{color:#173a63;text-decoration:none}.title a:hover{color:#1a6dc4;text-decoration:underline}
.meta{color:#999;font-size:12.5px;margin:4px 0 8px}
.tag{display:inline-block;background:#eef2f7;color:#5b6b7a;border-radius:10px;padding:1px 9px;font-size:12px;margin-left:6px}
textarea{width:100%;min-height:64px;border:1px solid #e3e6ea;border-radius:8px;padding:8px 10px;font-size:13.5px;font-family:inherit;resize:vertical;color:#333;background:#fcfcfb}
textarea:focus{outline:none;border-color:#1a6dc4;background:#fff}
.row2{display:flex;align-items:center;justify-content:space-between;margin-top:6px}
.tplbtn{font-size:12px;color:#1a6dc4;cursor:pointer;user-select:none}
.chk{display:flex;align-items:center;gap:6px;font-size:14px;color:#555;cursor:pointer;user-select:none}
.chk input{width:18px;height:18px;cursor:pointer}
details{margin-top:24px}summary{cursor:pointer;color:#888;font-size:14px}
.empty{color:#aaa;text-align:center;padding:40px 0;font-size:15px}
.savedhint{font-size:11px;color:#37a06a;margin-left:8px;opacity:0;transition:opacity .3s}
</style></head><body><div class="wrap">
<h1>今日待看</h1>
<div class="sub">姜胡说 · 珍大户的经济圈　|　更新于 __UPDATED__</div>
<div class="hint">最新在最上面。看完勾「看了」它就消失；不勾就留到下次继续待看。可在下面写感想（点「填入知识卡片模板」）。已看和感想都存在本机浏览器，每天自动刷新也不会丢。</div>
<div class="stat" id="stat"></div>
<div id="list"></div>
<details id="donebox"><summary></summary><div id="donelist"></div></details>
</div>
<script>
const DATA = __DATA__;
const RECENT_DAYS = 14;
const TPL = "标题：\n\n核心概念（用你自己的话，别抄书）：\n\n为什么值得关注（它为什么对+因果链2-3步）：\n\n能用在哪 / 不能用在哪：\n\n最小行动（看完马上能做的一件事，≤20字）：\n\n和已有知识的连接（互补还是冲突）：\n";
function LS(k,d){try{return JSON.parse(localStorage.getItem(k)||d);}catch(e){return JSON.parse(d);}}
function saveStatus(s){localStorage.setItem('todo_status',JSON.stringify(s));}
function saveNotes(n){localStorage.setItem('todo_notes',JSON.stringify(n));}
function initStatus(){
  const s=LS('todo_status','{}');const inited=localStorage.getItem('todo_inited');
  const cut=Date.now()-RECENT_DAYS*86400000;
  DATA.forEach(p=>{if(s[p.id]===undefined){s[p.id]=inited?'queue':((p.ts>=cut)?'queue':'done');}});
  localStorage.setItem('todo_inited','1');saveStatus(s);return s;
}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function render(){
  const s=initStatus();const notes=LS('todo_notes','{}');
  const queue=DATA.filter(p=>s[p.id]==='queue');
  const done=DATA.filter(p=>s[p.id]==='done');
  document.getElementById('stat').textContent='待看 '+queue.length+' 条　·　已看 '+done.length+' 条';
  const list=document.getElementById('list');
  list.innerHTML = queue.length? queue.map(p=>card(p,notes,false)).join('')
    : '<div class="empty">🎉 待看清单已清空，去喝杯茶吧</div>';
  document.getElementById('donebox').style.display = done.length?'block':'none';
  document.querySelector('#donebox summary').textContent='已看（'+done.length+'）— 点开可撤销';
  document.getElementById('donelist').innerHTML = done.slice(0,120).map(p=>card(p,notes,true)).join('');
  bind();
}
function card(p,notes,isDone){
  const nv=notes[p.id]||'';
  return '<div class="item'+(isDone?' done':'')+'" data-id="'+esc(p.id)+'">'
    +'<div class="top"><div class="title"><a href="'+p.link+'" target="_blank" rel="noopener">'+esc(p.sum)+'</a>'
    +(p.topic?'<span class="tag">'+esc(p.topic)+'</span>':'')+'</div></div>'
    +'<div class="meta">'+esc(p.date)+' · '+esc(p.planet)+'·'+esc(p.author)+' · 赞'+p.likes+'</div>'
    +'<textarea placeholder="写点感想…（点右边填入知识卡片模板）">'+esc(nv)+'</textarea>'
    +'<div class="row2"><span class="tplbtn">＋ 填入知识卡片模板</span>'
    +'<label class="chk"><input type="checkbox"'+(isDone?' checked':'')+'>看了</label></div></div>';
}
function bind(){
  document.querySelectorAll('.item').forEach(el=>{
    const id=el.dataset.id;
    const ta=el.querySelector('textarea');
    ta.addEventListener('input',()=>{const n=LS('todo_notes','{}');n[id]=ta.value;saveNotes(n);});
    el.querySelector('.tplbtn').addEventListener('click',()=>{if(!ta.value.trim()){ta.value=TPL;}else{ta.value=ta.value.replace(/\s*$/,'')+"\n\n"+TPL;}ta.focus();const n=LS('todo_notes','{}');n[id]=ta.value;saveNotes(n);});
    el.querySelector('input[type=checkbox]').addEventListener('change',e=>{
      const s=LS('todo_status','{}');s[id]=e.target.checked?'done':'queue';saveStatus(s);render();
    });
  });
}
render();
</script></body></html>'''


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    build()
