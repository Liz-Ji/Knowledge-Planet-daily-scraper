"""生成/刷新「知识图谱」HTML（按专题聚合，可独立浏览器打开）。

结构：中心「知识库」→ 5 大类 → 24 专题（节点按帖子数），点专题看脉络综述+帖子清单（可跳原文）。
- 结构层（节点/帖子）：只读飞书、不调大模型，很快。每日抓取后自动重建。
- 脉络综述：调大模型，带缓存（state/graph_summaries.json）。只在 refresh=True 或某专题帖子数变化时重算。
用法：
    python src/build_graph.py            # 结构 + 用缓存综述（缺失的会补算）
    python src/build_graph.py --refresh  # 强制重算所有专题综述
输出：项目根目录 知识图谱.html
"""
import sys, json, math, html, logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.feishu_client import FeishuClient
from src import summarizer
from src.topics import TOPIC_NAMES, TOPIC_CATEGORY, CATEGORIES

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "知识图谱.html"
CACHE = config.STATE_DIR / "graph_summaries.json"
COLORS = {"投资与市场": "#c0504d", "宏观与政策": "#4f6d7a", "科技与AI": "#5b8a72",
          "创业与变现": "#c77f2a", "成长与认知": "#7a5c9e"}


def to_int(v):
    try: return int(float(v))
    except (TypeError, ValueError): return 0


def gen_overview(name, posts):
    ps = sorted(posts, key=lambda p: p["ts"])
    if len(ps) > 60:
        step = len(ps) / 60.0
        ps = [ps[int(i * step)] for i in range(60)]
    listing = "\n".join(f"{p['date']} {p['who']} 赞{p['likes']}：{p['sum']}" for p in ps)
    SYS = ("你是投资/财经/个人成长内容的知识梳理专家。下面是某专题下的帖子摘要（按时间排序）。"
           "用250字以内写一段脉络综述：这个专题下的核心观点是什么、从早到晚观点如何演进或深化。"
           "纯文本，不要用markdown符号。")
    return summarizer.chat(SYS, f"专题：{name}\n\n{listing}", max_tokens=700, temperature=0.3).strip()


def build(refresh=False):
    feishu = FeishuClient(config.FEISHU_APP_ID, config.FEISHU_APP_SECRET,
                          config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID)
    items = feishu.list_all_records()

    by_topic = {t: [] for t in TOPIC_NAMES}
    for it in items:
        fd = it["fields"]
        tp = fd.get("专题")
        if tp not in by_topic:
            continue
        t = fd.get("发布时间") or 0
        by_topic[tp].append({
            "ts": t, "date": f"{datetime.fromtimestamp(t/1000):%Y-%m-%d}" if t else "",
            "who": f"{fd.get('星球名称','')}·{fd.get('作者','')}",
            "likes": to_int(fd.get("点赞数")),
            "sum": fd.get("摘要") or (fd.get("正文", "")[:50]),
            "link": (fd.get("原文链接") or {}).get("link", ""),
        })

    cache = {}
    if CACHE.exists():
        try: cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception: cache = {}

    # 综述：refresh 或 帖子数变化 或 尚无缓存 时重算
    for t in TOPIC_NAMES:
        posts = by_topic[t]
        if not posts:
            continue
        c = cache.get(t)
        if refresh or not c or c.get("count") != len(posts):
            try:
                ov = gen_overview(t, posts)
                cache[t] = {"overview": ov, "count": len(posts)}
                logging.info(f"  综述已生成: {t}（{len(posts)}篇）")
            except Exception:
                logging.exception(f"  综述生成失败: {t}")
    config.STATE_DIR.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    # 组织数据（只保留有内容的专题）
    themes = {}
    for t in TOPIC_NAMES:
        posts = by_topic[t]
        if not posts:
            continue
        posts.sort(key=lambda p: p["likes"], reverse=True)
        themes[t] = {
            "cat": TOPIC_CATEGORY[t],
            "overview": cache.get(t, {}).get("overview", ""),
            "count": len(posts),
            "posts": [{"date": p["date"], "who": p["who"], "likes": p["likes"],
                       "sum": p["sum"], "link": p["link"]} for p in posts[:50]],
        }

    total = sum(len(v) for v in by_topic.values())
    html_str = render(themes, total)
    OUT.write_text(html_str, encoding="utf-8")
    logging.info(f"知识图谱已生成: {OUT}（{len(themes)}个专题，{total}条已归类）")


def render(themes, total):
    ROW, x_center, x_cat, x_theme, top = 30, 90, 360, 610, 30
    nodes, edges, y = [], [], top
    cat_y = {}
    for cat in CATEGORIES:
        ts = [t for t in TOPIC_NAMES if TOPIC_CATEGORY[t] == cat and t in themes]
        if not ts:
            continue
        ys = []
        for t in ts:
            th = themes[t]
            r = min(15, 5 + th["count"] ** 0.5)
            edges.append(f'<line x1="{x_cat}" y1="__CATY_{cat}__" x2="{x_theme-8}" y2="{y}" stroke="{COLORS[cat]}" stroke-opacity="0.35" stroke-width="1"/>')
            nodes.append(
                f'<g class="nd" data-t="{html.escape(t)}" style="cursor:pointer">'
                f'<circle cx="{x_theme}" cy="{y}" r="{r:.0f}" fill="{COLORS[cat]}" fill-opacity="0.85"/>'
                f'<text x="{x_theme+14}" y="{y+4}" font-size="13" fill="#333">{html.escape(t)} '
                f'<tspan fill="#999" font-size="11">{th["count"]}</tspan></text></g>'
            )
            ys.append(y)
            y += ROW
        cy = sum(ys) / len(ys)
        cat_y[cat] = cy
        edges.append(f'<line x1="{x_center+30}" y1="__ALLCY__" x2="{x_cat-2}" y2="{cy:.0f}" stroke="{COLORS[cat]}" stroke-opacity="0.5" stroke-width="1.5"/>')
        nodes.append(
            f'<g class="cat" data-c="{html.escape(cat)}" style="cursor:pointer">'
            f'<rect x="{x_cat-2}" y="{cy-15:.0f}" width="150" height="30" rx="15" fill="{COLORS[cat]}"/>'
            f'<text x="{x_cat+73}" y="{cy+4:.0f}" text-anchor="middle" font-size="13" fill="#fff">{html.escape(cat)}</text></g>'
        )
        y += 12
    H = y + 10
    allcy = sum(cat_y.values()) / len(cat_y) if cat_y else H / 2
    center = (f'<circle cx="{x_center}" cy="{allcy:.0f}" r="34" fill="#2b2b2b"/>'
              f'<text x="{x_center}" y="{allcy-2:.0f}" text-anchor="middle" font-size="14" fill="#fff">知识库</text>'
              f'<text x="{x_center}" y="{allcy+14:.0f}" text-anchor="middle" font-size="10" fill="#bbb">{total}条</text>')
    svg = (f'<svg viewBox="0 0 980 {H:.0f}" width="100%" xmlns="http://www.w3.org/2000/svg">'
           + "".join(edges) + center + "".join(nodes) + "</svg>")
    svg = svg.replace("__ALLCY__", f"{allcy:.0f}")
    for cat, cy in cat_y.items():
        svg = svg.replace(f"__CATY_{cat}__", f"{cy:.0f}")

    data = json.dumps(themes, ensure_ascii=False)
    updated = f"{datetime.now():%Y-%m-%d %H:%M}"
    return TEMPLATE.replace("__SVG__", svg).replace("__DATA__", data).replace("__UPDATED__", updated).replace("__TOTAL__", str(total))


TEMPLATE = '''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>星球知识图谱</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f7;color:#333;line-height:1.7}
.wrap{max-width:1000px;margin:0 auto;padding:24px 20px 80px}
h1{font-size:22px;font-weight:600;margin:0 0 2px}.sub{color:#888;font-size:13px;margin-bottom:16px}
.card{background:#fff;border-radius:12px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.nd:hover text,.cat:hover text{font-weight:600}
#panel{margin-top:16px}
.ov{background:#fbfaf7;border-left:3px solid #c77f2a;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:8px;font-size:15px}
.post{padding:9px 0;border-top:1px solid #eee}
.post a{color:#1a6dc4;text-decoration:none;font-size:14px}.post a:hover{text-decoration:underline}
.meta{color:#999;font-size:12px;margin-top:2px}
.hint{color:#aaa;font-size:13px;margin:6px 0 10px}
</style></head><body><div class="wrap">
<h1>星球知识图谱</h1>
<div class="sub">姜胡说 · 珍大户的经济圈　|　共 __TOTAL__ 条已归类　|　更新于 __UPDATED__</div>
<div class="hint">点右侧任一专题查看脉络综述与帖子（点标题跳原文）；点大类可展开该类专题</div>
<div class="card">__SVG__</div>
<div id="panel"></div>
</div>
<script>
const DATA = __DATA__;
const panel = document.getElementById('panel');
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function showTheme(t){
  const d = DATA[t]; if(!d) return;
  let rows = d.posts.map(p =>
    '<div class="post"><a href="'+p.link+'" target="_blank" rel="noopener">'+esc(p.sum)+'</a>'
    + '<div class="meta">'+esc(p.date)+' · '+esc(p.who)+' · 赞'+p.likes+'</div></div>').join('');
  panel.innerHTML = '<div class="card"><div style="font-size:17px;font-weight:600;margin-bottom:2px">'+esc(t)
    + ' <span style="font-size:13px;color:#999;font-weight:400">'+d.count+'篇</span></div>'
    + (d.overview?'<div class="ov">'+esc(d.overview)+'</div>':'')
    + (d.posts.length<d.count?'<div class="hint">按点赞展示前 '+d.posts.length+' 篇</div>':'')
    + rows + '</div>';
  panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function showCat(c){
  const ts = Object.keys(DATA).filter(t=>DATA[t].cat===c);
  let chips = ts.map(t=>'<a href="#" data-t="'+esc(t)+'" style="display:inline-block;margin:4px 8px 4px 0;padding:5px 12px;background:#f0f0f0;border-radius:14px;color:#333;text-decoration:none;font-size:13px">'+esc(t)+' '+DATA[t].count+'</a>').join('');
  panel.innerHTML='<div class="card"><div style="font-size:17px;font-weight:600;margin-bottom:8px">'+esc(c)+'</div>'+chips+'</div>';
  panel.querySelectorAll('a[data-t]').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();showTheme(a.dataset.t);}));
}
document.querySelectorAll('.nd').forEach(n=>n.addEventListener('click',()=>showTheme(n.dataset.t)));
document.querySelectorAll('.cat').forEach(n=>n.addEventListener('click',()=>showCat(n.dataset.c)));
</script></body></html>'''


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    for _n in ("httpx", "openai", "httpcore"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    build(refresh="--refresh" in sys.argv)
