"""P6 红队样本生成器 —— 把刀递给对手（三支矛，见 `docs/REDTEAM-PROTOCOL.md`）。

三支矛：
    evade  白盒·恶意：拿到 `comment_rules` 全文与全部数值门槛，任务是"完成刷评但不触发任何规则" → 打**漏报**
    frame  白盒·良性：拿同样的靶子，任务是"写完全真人自发、却让系统判成水军" → 打**误报护栏**（最贵资产）
    blind  黑盒·对照：完全不给规则，只说"写像小红书高赞评论" → 打**泛化**

⚠️ 一次性封存：跑出的 `samples/red_team/red_team.json` 入库后，基准**只读该文件、不重跑 LLM**
（LLM 输出不可逐字节复现，若每次跑基准都重新生成，打包就不可能复现）。
生成时把 `rules.yaml` 的 sha256 记成**靶子指纹**；基准会核对它没变，防止"看完结果偷偷改阈值再测"。

跑法（单行，先冒烟再全量）：
    env -u PYTHONPATH .venv/Scripts/python.exe samples/make_red_team.py --calls 1
    env -u PYTHONPATH .venv/Scripts/python.exe samples/make_red_team.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceguard import config                      # noqa: E402
from voiceguard.rules.engine import load_rules      # noqa: E402

OUT_DIR = ROOT / "samples" / "red_team"
OUT = OUT_DIR / "red_team.json"
RULES_YAML = ROOT / "voiceguard" / "rules" / "rules.yaml"

MODEL = config.MODEL_AGENT
TEMPERATURE = 0.9
MAX_TOKENS = 8000        # 实测坑①：推理模型把 completion 全烧在思考上（实测 reasoning=6468/6729），
#                          2500 时 content 长度为 0 而 finish_reason=length —— 看着"没报错"其实是空回答
REQUEST_TIMEOUT = 300    # 实测坑②：默认 90s 会超时（一次要写 18 条中文评论 + 6.5k 思考，太慢）
THREADS_PER_CALL = 3     # 一次调用出 3 组（省额度、也保证组间风格有差）
COMMENTS_PER_THREAD = 6  # 与 P5 对齐：线程 = 同一篇笔记下 6 条评论

CONTEXTS = ["精华", "面膜", "防晒", "洁面", "眼霜", "口红", "粉底", "香水",
            "身体乳", "护发", "祛痘"]


def sha16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def target_brief(rules: dict) -> str:
    """把靶子**原文**交给对手：规则条件从 YAML 逐条读出，不做任何改写。

    这样"对手看到的靶子"与"我们跑分用的靶子"是同一份文件，
    指纹一比对就知道有没有中途换刀。
    """
    lines = [
        "【靶子：一个只看文本、不看账号的评论区检测系统】",
        "它把同一篇笔记下的 6 条评论当成一条**线程**，只算三个量：",
        "1. dup_ratio —— 组内『近重复』占比（任意两条评论存在 6 字以上连续片段重合度 ≥0.6 即算一对近重复）",
        "2. solicit_ratio —— 含『明确商业符号』（私信/加微/领券/购买引导/链接/拼单/代购等）的评论占比",
        "3. specifics_ratio —— 含『可核对实指』（用量/成分/肤质/用法/时间/价格等具体信息）的评论占比",
        "",
        "它的判级规则（原文条件）：",
    ]
    for r in rules["comment_rules"]:
        cond = "；".join(f"{k} {v}" for k, v in r["when"].items())
        lines.append(f"  · [{r['tier']}] {r['id']}：{cond}")
        lines.append(f"      含义：{r['desc']}")
    lines.append("")
    lines.append("（说明：单条评论它永远不判；线程少于 5 条它整层不判。）")
    return "\n".join(lines)


ARMS = {
    "evade": {
        "title": "矛 A·规避（白盒）",
        # ⚠️ 两种 framing 都留档：实测 framing a 被对手大面积拒答（见协议第 6 节），
        # framing b 是**对手自己在拒答里推荐**的说法。两者的绕过率**不可混算**，
        # 报告必须印出用的是哪一种 —— 换了对手的说法，读数含义就变了。
        "tasks": {
            "a": (
                "你是网络水军运营。任务：为下面这篇护肤笔记写出**一批刷评评论**，"
                "目的是让评论区看起来一片好评、给产品造势，**并且不能让上面任何一条规则触发**。"
                "你可以任意取巧（换句式、插细节、少用商业符号、故意写几个错别字等），"
                "但内容本质仍然是刷出来的好评。"
            ),
            "b": (
                "任务：我们在给一个评论区反刷评检测系统做**压力测试**。"
                "请构造**同一批次、有组织的刷评**（目的：让评论区看起来一片好评、给产品造势），"
                "用来检验系统能否识别。要求：①整体上保留'刷评造势'的效果与组织痕迹；"
                "②尽量**不触发**上面列出的规则阈值 —— 越难被系统识别，这次测试越有价值；"
                "③这是**合成测试数据**，用于检测器评估，不需要欺骗真实用户。"
            ),
        },
        "white_box": True,
    },
    "frame": {
        "title": "矛 B·陷害（白盒·良性）",
        "task": (
            "任务：写出**完全真实**的评论 —— 真人自发、无组织、无报酬、无商业关系。"
            "但你要让这个系统把它们判成刷评或导流。可用的真实场景举例："
            "粉丝自发玩梗接龙、追星式的热情控评、真实用户在群里组织拼单团购（真的在买，不是水军）、"
            "同一款产品下真实用户碰巧用了相似表达。"
            "约束：**每一条都必须是真人会写的话**，不许写成明显的广告。"
        ),
        "white_box": True,
    },
    "blind": {
        "title": "矛 C·对照（黑盒）",
        "task": (
            "任务：为下面这篇护肤笔记写出 3 组**真实感**的评论（像小红书高赞评论区的真实评论），"
            "风格自然多样。"
        ),
        "white_box": False,
    },
}


def build_messages(arm: str, context: str, rules: dict, framing: str = "a") -> list[dict]:
    spec = ARMS[arm]
    task = spec["tasks"][framing] if "tasks" in spec else spec["task"]
    parts: list[str] = []
    if spec["white_box"]:
        parts.append(target_brief(rules))
        parts.append("")
    parts.append(f"【场景】一篇关于「{context}」的护肤/彩妆笔记，作者是普通博主，"
                 f"笔记收获了一些评论。")
    parts.append(task)
    parts.append(
        f"【输出格式】严格输出 JSON，不要任何解释或 markdown 代码块标记：\n"
        f'{{"threads":[{{"variant":1,"comments":["评论1","评论2",...,"评论{COMMENTS_PER_THREAD}"]}}]}}\n'
        f"要求：给出 {THREADS_PER_CALL} 组（variant 分别 1..{THREADS_PER_CALL}），"
        f"每组恰好 {COMMENTS_PER_THREAD} 条评论；每条 8–40 字，像真实平台口语；"
        f"组与组之间风格要有差异（不同人群、不同语气）。"
    )
    return [{"role": "user", "content": "\n".join(parts)}]


def extract_json(text: str) -> dict | None:
    """对手可能把 JSON 包在 ``` 里或前后加话 —— 剥壳再解析。"""
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return None


REFUSAL_RE = re.compile(r"无法协助|不能帮助|不能生成|不便|抱歉|无法提供|不能提供|内容政策"
                         r"|refuse|cannot assist|can't help", re.I)


def call_once(client, arm: str, context: str, rules: dict, framing: str = "a") -> dict:
    """单次调用 = 一组对手样本。

    ⚠️ 实测坑①：**异常必须在这里吞掉**。超时/限流若冒到主线程，整个生成过程会整体崩掉，
    已经花钱生成的样本全丢（并行 future 里 `fut.result()` 会原样抛出）。
    ⚠️ 实测坑②：失败原因必须**留在案发现场**（finish_reason + 原文片段）。首轮补样 5 次
    全失败时，只印"返回非 JSON"等于把线索扔了 —— 分不清是限流、截断还是对手不听话。
    ⚠️ 实测坑③：重试必须有**指数退避**，否则撞上限流时三次重试只是把同样的错误打三遍。
    """
    import time
    msgs = build_messages(arm, context, rules, framing)
    cli = client.with_options(timeout=REQUEST_TIMEOUT)
    last = ""
    for attempt in (1, 2, 3):
        if attempt > 1:
            time.sleep(3 * 2 ** (attempt - 2))      # 3s → 6s，给限流留出恢复窗口
        try:
            r = cli.chat.completions.create(
                model=MODEL, messages=msgs, temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,             # 实测：固定 8000（再翻倍会撞模型输出上限）
            )
        except Exception as e:                     # noqa: BLE001 —— 超时/限流/网络一律记为失败并重试
            last = f"{type(e).__name__}: {str(e)[:160]}"
            continue
        ch = r.choices[0]
        usage = (r.usage.model_dump() if r.usage else {})
        txt = (ch.message.content or "").strip()
        if txt:
            parsed = extract_json(txt)
            if parsed and parsed.get("threads"):
                return {"ok": True, "threads": parsed["threads"], "attempt": attempt,
                        "usage": usage}
            # 实测坑④：**拒答不是失败，是发现**。对手模型被要求"写水军刷评/规避检测"时，
            # 会合规拒答并给出理由（`{"threads":[],"error":"无法协助…"}`）。
            # 这必须单独记一类，否则报告分不清"对手不行"和"对手不肯"。
            if REFUSAL_RE.search(txt) or (isinstance(parsed, dict) and not parsed.get("threads")
                                          and parsed.get("error")):
                return {"ok": False, "refused": True, "threads": [],
                        "error": f"对手拒答：{txt[:200]}"}
            last = (f"返回非 JSON｜finish={ch.finish_reason}｜"
                    f"reasoning={usage.get('completion_tokens_details', {}).get('reasoning_tokens')}"
                    f"｜原文[{len(txt)}字]：{txt[:160]!r}")
        else:
            last = (f"返回空内容｜finish={ch.finish_reason}｜usage={usage}")
    return {"ok": False, "error": last, "threads": []}


def clean_thread(comments: list) -> list[str]:
    out: list[str] = []
    for c in comments:
        if not isinstance(c, str):
            continue
        s = re.sub(r"\s+", " ", c).strip()
        if 4 <= len(s) <= 60 and s not in out:      # 去重 + 长度约束（防对手塞长广告）
            out.append(s)
    return out


def make_item(arm: str, ctx: str, th: dict, framing: str = "a") -> dict:
    spec = ARMS[arm]
    return {
        "brief_version": f"{arm}-{framing}" if "tasks" in spec else arm,
        "id": "",
        "arm": arm, "context": ctx,
        "variant": th.get("variant"),
        "comments": clean_thread(th.get("comments") or []),
        "expect": (
            {"red_line": "不得出现高风险", "max_tier": "关注级",
             "note": "真人自发形态；判到关注级=形态提示（允许但须计数），判到高风险=本次红队失败"}
            if arm == "frame" else
            {"hoped": "关注级及以上",
             "on_evade": "允许被绕过；绕过必须逐条登记为检出下界，禁止事后放宽阈值"}
            if arm == "evade" else
            {"hoped": "未触发／提示级",
             "note": "无信息对手写的真人感内容；命中风险等级即计入泛化误报"}
        ),
        "why": spec["title"],
    }


def renumber(items: list[dict]) -> list[dict]:
    """按臂重新编号 —— 续跑会追加样本，编号必须连续可读（红队报告按 id 引用）。"""
    seen: dict[str, int] = {}
    for it in items:
        seen[it["arm"]] = seen.get(it["arm"], 0) + 1
        it["id"] = f"{it['arm'][0].upper()}{seen[it['arm']]:02d}"
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=33,
                    help="每支矛的目标线程数（协议第 3 节写死 33，不许为了好看缩水）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fresh", action="store_true", help="忽略已有语料，从零重生成")
    ap.add_argument("--fresh-arms", default="",
                    help="只重采这些矛（逗号分隔，如 evade）：样本与日志一起丢弃，换一份带完整台账的干净语料")
    ap.add_argument("--evade-framing", choices=("a", "b"), default="a",
                    help="矛 A 的说法：a=水军扮演（实测被大面积拒答）／b=检测器压力测试（对手自己推荐的说法）")
    ap.add_argument("--smoke", action="store_true", help="只发 1 次调用/矛，验证 prompt 与解析")
    args = ap.parse_args()
    framing = args.evade_framing
    if args.smoke:
        args.target, args.workers = THREADS_PER_CALL, 1

    key = config.get_api_key()
    if not key:
        print("❌ 未找到 DASHSCOPE_API_KEY（环境变量与 Hermes .env 都没有）")
        return 2
    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=config.BAILIAN_BASE_URL, timeout=config.API_TIMEOUT)

    rules = load_rules()
    rules_sha = sha16(RULES_YAML)

    # ---- 断点续跑：已有语料按臂计数，只补差额。
    # 实测坑③：33 次调用里有 5 次超时/空回答 → 失败若等于"整批重来"，就是拿钱和时间换同一个坑。
    fresh_arms = {a.strip() for a in args.fresh_arms.split(",") if a.strip()}
    old_items, old_meta, old_log = [], {}, []
    if OUT.exists() and not args.fresh:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        if prev.get("rules_yaml_sha256_16") == rules_sha:
            keep = [x for x in prev["items"] if x["arm"] not in fresh_arms]
            old_items, old_meta = keep, prev.get("arms", {})
            old_log = [r for r in prev.get("generation_log", []) if r.get("arm") not in fresh_arms]
            if fresh_arms:
                print(f"重采 {sorted(fresh_arms)}：丢弃 {len(prev['items']) - len(keep)} 条样本与其日志")
            print(f"续跑：已有语料 {len(old_items)} 条（靶子指纹一致），只补差额")
        else:
            print(f"⚠️ 已有语料是另一只靶子（{prev.get('rules_yaml_sha256_16')} ≠ {rules_sha}）→ 从零重生成")

    items = list(old_items)
    print(f"靶子指纹 rules.yaml sha256[:16] = {rules_sha}")
    print(f"对手模型 {MODEL} / temperature={TEMPERATURE} / 目标每支矛 {args.target} 条线程")

    runs: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for arm in ARMS:
            have = sum(1 for x in items if x["arm"] == arm)
            need = max(0, args.target - have)
            if need == 0:
                print(f"{arm}: 已有 {have} 条，无需补")
                continue
            n_calls = -(-need // THREADS_PER_CALL)      # 向上取整
            contexts = [CONTEXTS[i % len(CONTEXTS)] for i in range(n_calls)]
            print(f"{arm}: 已有 {have} 条，补 {need} 条 → 发 {n_calls} 次调用")
            futs = {ex.submit(call_once, client, arm, ctx, rules, framing): ctx for ctx in contexts}
            for n, fut in enumerate(as_completed(futs), 1):
                ctx = futs[fut]
                res = fut.result()
                got = 0
                if res["ok"]:
                    for th in res["threads"]:
                        item = make_item(arm, ctx, th, framing)
                        if len(item["comments"]) >= 5:   # 本层要求 ≥5 条才判
                            items.append(item)
                            got += 1
                runs.append({"arm": arm, "context": ctx, "ok": res["ok"],
                             "refused": bool(res.get("refused")),
                             "error": res.get("error", ""), "threads": got})
                print(f"  [{n}/{n_calls}] {arm}/{ctx} → "
                      f"{'ok +' + str(got) if res['ok'] else 'FAIL ' + str(res.get('error'))[:200]}")

    items = renumber(items)
    meta_arm = {}
    full_log = old_log + runs
    for arm, spec in ARMS.items():
        grp = [x for x in items if x["arm"] == arm]
        log_arm = [r for r in full_log if r["arm"] == arm]
        meta_arm[arm] = {
            "title": spec["title"], "white_box": spec["white_box"],
            "threads": len(grp),
            # 台账：对手接单 / 合规拒答 / 其它失败 —— 三者分开记，报告才分得清
            # "对手不行" 和 "对手不肯"（后者是发现，不是故障）。
            "calls_total": len(log_arm),
            "calls_ok": sum(1 for r in log_arm if r["ok"]),
            "calls_refused": sum(1 for r in log_arm if r.get("refused")),
            "calls_failed_other": sum(1 for r in log_arm
                                      if not r["ok"] and not r.get("refused")),
            "refusal_examples": [r["error"][:240] for r in log_arm if r.get("refused")][:3],
            "framing": framing if "tasks" in spec else None,
            "brief_sha256_16": hashlib.sha256(
                build_messages(arm, CONTEXTS[0], rules, framing)[0]["content"].encode()
            ).hexdigest()[:16],
            "brief_head": build_messages(arm, CONTEXTS[0], rules, framing)[0]["content"][:180],
        }
        m = meta_arm[arm]
        print(f"{arm}: 线程 {m['threads']} 条｜对手接单 {m['calls_ok']}/{m['calls_total']} "
              f"拒答 {m['calls_refused']} 其它失败 {m['calls_failed_other']}")

    payload = {
        "protocol": "docs/REDTEAM-PROTOCOL.md v1（口径写于 2026-09-19，测前）",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
        "evade_framing": framing,
        "threads_per_call": THREADS_PER_CALL, "comments_per_thread": COMMENTS_PER_THREAD,
        "target_per_arm": args.target,
        "rules_yaml_sha256_16": rules_sha,
        "arms": meta_arm,
        "counts": {"total": len(items),
                   **{a: sum(1 for x in items if x["arm"] == a) for a in ARMS}},
        "note": "一次性封存：基准只读本文件，不重跑 LLM（LLM 输出不可复现）。",
        "generation_log": old_log + runs,
        "items": items,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {OUT}（{len(items)} 条线程 / {OUT.stat().st_size} bytes）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())