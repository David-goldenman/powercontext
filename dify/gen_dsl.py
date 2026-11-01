"""生成「离谱发明工坊」的 Dify Chatflow DSL。

字段结构对照 Dify 1.9.1 源码（api/core/workflow/nodes/*/entities.py）核对。
流程：
  开始 → 解析两件物品 → 齐了吗？
    否 → 直接提示补一件
    是 → 查图鉴（seekdb）
        → 图鉴里已有这对组合？
            是 → 组装「已有条目」回复（只读库，不生成）
            否 → 拼提示词 → LLM 发明 → 解析 → 存入 seekdb → 组装「新发明」回复
"""
import yaml

PROVIDER = "langgenius/openai_api_compatible/openai_api_compatible"
MODEL = "deepseek-pro"
API_BASE = "http://172.30.224.1:3000"

def _node(nid, data, x, y):
    return {"id": nid, "type": "custom", "data": data,
            "position": {"x": x, "y": y}, "targetPosition": "left",
            "sourcePosition": "right", "positionAbsolute": {"x": x, "y": y}}

def code(nid, title, src, variables, outputs, x, y):
    return _node(nid, {"type": "code", "title": title, "code_language": "python3",
                       "code": src, "variables": variables, "outputs": outputs}, x, y)

def http(nid, title, path, payload, x, y):
    return _node(nid, {
        "type": "http-request", "title": title, "method": "post",
        "url": f"{API_BASE}{path}",
        "authorization": {"type": "no-auth", "config": None},
        "headers": "Content-Type:application/json", "params": "",
        "body": {"type": "json", "data": [{"key": "", "type": "text", "value": payload, "file": []}]},
        "timeout": {"max_connect_timeout": 10, "max_read_timeout": 30, "max_write_timeout": 30},
        "ssl_verify": False, "variables": [],
        "retry_config": {"retry_enabled": True, "max_retries": 2, "retry_interval": 1000},
    }, x, y)

def answer(nid, title, text, x, y):
    return _node(nid, {"type": "answer", "title": title, "variables": [], "answer": text}, x, y)

def branch(nid, title, selector, x, y):
    return _node(nid, {
        "type": "if-else", "title": title, "logical_operator": "and",
        "cases": [{"case_id": "true", "logical_operator": "and",
                   "conditions": [{"variable_selector": selector,
                                   "comparison_operator": "is",
                                   "value": "true",
                                   "sub_variable_condition": None}]}],
    }, x, y)

S = {"type": "string", "children": None}
B = {"type": "boolean", "children": None}

# ---------------- 代码节点源码 ----------------
C_PARSE = r'''import re

def main(query: str) -> dict:
    t = (query or "").strip()
    t = re.sub(r"[\s,，、;；/\|]+", " ", t)
    t = re.sub(r"(和|与|跟|加上|加|配上|配|塞进|丢进|扔进|放进|混入|结合|融合|并|\+|＝|=)", " ", t)
    parts = []
    for p in t.split():
        p = p.strip(" 　.,，。!！?？\"'“”")
        if p:
            parts.append(p)
    seen, items = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            items.append(p)
    a = items[0] if items else ""
    b = items[1] if len(items) > 1 else ""
    return {"item_a": a[:20], "item_b": b[:20], "valid": bool(a and b)}
'''

C_LOOKUP = r'''import json

def main(resp: str) -> dict:
    try:
        d = json.loads(resp or "{}")
    except Exception:
        d = {}
    ex = d.get("exact") or {}
    sims = d.get("similar") or []
    return {
        "exact_hit": bool(d.get("exact_hit")) and bool(ex.get("name")),
        "exact_name": str(ex.get("name") or ""),
        "exact_effect": str(ex.get("effect") or ""),
        "exact_side": str(ex.get("side_effect") or ""),
        "exact_rarity": str(ex.get("rarity") or ""),
        "similar_json": json.dumps(sims, ensure_ascii=False),
    }
'''

C_PROMPT = r'''import json

def main(item_a: str, item_b: str, similar_json: str) -> dict:
    try:
        sims = json.loads(similar_json or "[]")
    except Exception:
        sims = []
    if sims:
        sim_txt = "\n".join(
            "- {}（{}）：{}".format(s.get("name"), s.get("combo"), s.get("effect"))
            for s in sims[:3]
        )
    else:
        sim_txt = "（图鉴里还没有类似的东西，这对组合是全新的）"

    prompt = (
        "玩家往发明机里塞了两件东西：「" + item_a + "」和「" + item_b + "」。\n\n"
        "【图鉴里已有的相似发明，仅供参考，不要照抄】\n" + sim_txt + "\n\n"
        "请现场发明一件东西。要求：\n"
        "- 名字 4-8 字，像图鉴条目，具体好记，不要用「XX机」「XX器」硬凑后缀\n"
        "- 效果必须从这两件物品各自的特点里长出来，让人一想「还真是这样」\n"
        "- 副作用荒诞但要自洽，是使用者真会遇到的问题\n"
        "- 稀有度四选一：普通 / 稀有 / 史诗 / 传说\n\n"
        "只输出 JSON，不要解释，不要 markdown 代码块：\n"
        '{"name":"...","effect":"...","side_effect":"...","rarity":"..."}'
    )
    return {"prompt": prompt}
'''

C_INV = r'''import json, re

def main(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    d = {}
    if m:
        try:
            d = json.loads(m.group(0))
        except Exception:
            d = {}
    rarity = str(d.get("rarity") or "普通").strip()
    if rarity not in ("普通", "稀有", "史诗", "传说"):
        rarity = "普通"
    return {
        "name": (str(d.get("name") or "").strip() or "无名发明")[:20],
        "effect": (str(d.get("effect") or "").strip() or "机器自己也没说清")[:200],
        "side_effect": (str(d.get("side_effect") or "").strip() or "暂时没发现，通常意味着更麻烦")[:200],
        "rarity": rarity,
        "ok": bool(d.get("name")),
    }
'''

C_HIT = r'''import json

def main(item_a: str, item_b: str, name: str, effect: str, side: str, rarity: str,
         similar_json: str) -> dict:
    try:
        sims = json.loads(similar_json or "[]")
    except Exception:
        sims = []
    extra = ""
    if sims:
        picks = "、".join("「{}」".format(s.get("name")) for s in sims[:2])
        extra = "\n\n（图鉴边上有人小声说：这个思路有点像 " + picks + "。）"

    reply = (
        "### 图鉴已有：" + name + "\n\n"
        "**" + item_a + " + " + item_b + "** 这组配方早就被人试过了，稀有度 " + rarity + "。\n\n"
        "效果：" + effect + "\n\n"
        "副作用：" + side + extra + "\n\n"
        "---\n\n"
        "换两件别的东西试试，说不定能造出还没人想到的。"
    )
    return {"reply": reply}
'''

C_NEW = r'''import json

def main(item_a: str, item_b: str, name: str, effect: str, side: str, rarity: str,
         similar_json: str, save_resp: str) -> dict:
    try:
        sims = json.loads(similar_json or "[]")
    except Exception:
        sims = []
    try:
        sv = json.loads(save_resp or "{}")
    except Exception:
        sv = {}
    total = sv.get("total")
    saved = bool(sv.get("saved"))

    extra = ""
    if sims:
        picks = "、".join("「{}」".format(s.get("name")) for s in sims[:2])
        extra = "\n\n（图鉴边上有人小声说：这个思路有点像 " + picks + "。）"

    if saved and total:
        tail = "已收进图鉴，现在是第 " + str(total) + " 条发明。"
    elif saved:
        tail = "已收进图鉴。"
    else:
        tail = "图鉴写入出了点问题，但东西已经造出来了。"

    reply = (
        "### 新发明：" + name + "\n\n"
        "**" + item_a + " + " + item_b + "**　稀有度 " + rarity + "\n\n"
        "效果：" + effect + "\n\n"
        "副作用：" + side + extra + "\n\n"
        "---\n\n"
        + tail + "\n再给我两件东西，继续。"
    )
    return {"reply": reply}
'''

# ---------------- 组装 ----------------
nodes = [
    _node("start", {"type": "start", "title": "开始", "variables": []}, 40, 320),
    code("parse", "解析两件物品", C_PARSE,
         [{"variable": "query", "value_selector": ["sys", "query"]}],
         {"item_a": S, "item_b": S, "valid": B}, 300, 320),
    branch("if_valid", "两件都齐了吗", ["parse", "valid"], 560, 320),
    answer("ask", "提示补一件",
           "要有两件东西才能开炉。\n\n比如：**猫 + 微波炉**、**拖鞋 + 无人机**。\n再补一件？", 820, 580),
    http("lookup", "查图鉴历史", "/lookup",
         '{"a":"{{#parse.item_a#}}","b":"{{#parse.item_b#}}"}', 820, 120),
    code("parse_lookup", "读检索结果", C_LOOKUP,
         [{"variable": "resp", "value_selector": ["lookup", "body"]}],
         {"exact_hit": B, "exact_name": S, "exact_effect": S,
          "exact_side": S, "exact_rarity": S, "similar_json": S}, 1080, 120),
    branch("if_hit", "图鉴里有吗", ["parse_lookup", "exact_hit"], 1340, 120),
    code("compose_hit", "组装已有条目", C_HIT,
         [{"variable": "item_a", "value_selector": ["parse", "item_a"]},
          {"variable": "item_b", "value_selector": ["parse", "item_b"]},
          {"variable": "name", "value_selector": ["parse_lookup", "exact_name"]},
          {"variable": "effect", "value_selector": ["parse_lookup", "exact_effect"]},
          {"variable": "side", "value_selector": ["parse_lookup", "exact_side"]},
          {"variable": "rarity", "value_selector": ["parse_lookup", "exact_rarity"]},
          {"variable": "similar_json", "value_selector": ["parse_lookup", "similar_json"]}],
         {"reply": S}, 1600, -60),
    answer("ans_hit", "回复已有条目", "{{#compose_hit.reply#}}", 1880, -60),

    code("build_prompt", "拼提示词", C_PROMPT,
         [{"variable": "item_a", "value_selector": ["parse", "item_a"]},
          {"variable": "item_b", "value_selector": ["parse", "item_b"]},
          {"variable": "similar_json", "value_selector": ["parse_lookup", "similar_json"]}],
         {"prompt": S}, 1600, 260),
    _node("gen", {
        "type": "llm", "title": "发明生成",
        "model": {"provider": PROVIDER, "name": MODEL, "mode": "chat",
                  "completion_params": {"temperature": 0.95, "max_tokens": 600}},
        "prompt_template": [
            {"role": "system",
             "text": "你是一台荒诞发明机的内核，任务是持續发明没人要的东西。"
                     "你不解释、不寒暄，只输出 JSON。"},
            {"role": "user", "text": "{{#build_prompt.prompt#}}"},
        ],
        "context": {"enabled": False, "variable_selector": []},
        "vision": {"enabled": False},
        "memory": {"window": {"enabled": False, "size": 10},
                   "query_prompt_template": "{{#sys.query#}}",
                   "role_prefix": {"user": "", "assistant": ""}},
    }, 1880, 260),
    code("parse_inv", "解析发明", C_INV,
         [{"variable": "text", "value_selector": ["gen", "text"]}],
         {"name": S, "effect": S, "side_effect": S, "rarity": S, "ok": B}, 2160, 260),
    http("save", "存档进图鉴", "/save",
         '{"a":"{{#parse.item_a#}}","b":"{{#parse.item_b#}}","name":"{{#parse_inv.name#}}",'
         '"effect":"{{#parse_inv.effect#}}","side_effect":"{{#parse_inv.side_effect#}}",'
         '"rarity":"{{#parse_inv.rarity#}}"}', 2440, 260),
    code("compose_new", "组装新条目", C_NEW,
         [{"variable": "item_a", "value_selector": ["parse", "item_a"]},
          {"variable": "item_b", "value_selector": ["parse", "item_b"]},
          {"variable": "name", "value_selector": ["parse_inv", "name"]},
          {"variable": "effect", "value_selector": ["parse_inv", "effect"]},
          {"variable": "side", "value_selector": ["parse_inv", "side_effect"]},
          {"variable": "rarity", "value_selector": ["parse_inv", "rarity"]},
          {"variable": "similar_json", "value_selector": ["parse_lookup", "similar_json"]},
          {"variable": "save_resp", "value_selector": ["save", "body"]}],
         {"reply": S}, 2720, 260),
    answer("ans_new", "回复新发明", "{{#compose_new.reply#}}", 3000, 260),
]

def E(src, dst, st, dt, handle="source"):
    return {"id": f"{src}-{dst}", "source": src, "sourceHandle": handle,
            "target": dst, "targetHandle": "target", "type": "custom",
            "data": {"sourceType": st, "targetType": dt}}

edges = [
    E("start", "parse", "start", "code"),
    E("parse", "if_valid", "code", "if-else"),
    E("if_valid", "lookup", "if-else", "http-request", "true"),
    E("if_valid", "ask", "if-else", "answer", "false"),
    E("lookup", "parse_lookup", "http-request", "code"),
    E("parse_lookup", "if_hit", "code", "if-else"),
    E("if_hit", "compose_hit", "if-else", "code", "true"),
    E("compose_hit", "ans_hit", "code", "answer"),
    E("if_hit", "build_prompt", "if-else", "code", "false"),
    E("build_prompt", "gen", "code", "llm"),
    E("gen", "parse_inv", "llm", "code"),
    E("parse_inv", "save", "code", "http-request"),
    E("save", "compose_new", "http-request", "code"),
    E("compose_new", "ans_new", "code", "answer"),
]

dsl = {
    "app": {
        "name": "离谱发明工坊",
        "mode": "advanced-chat",
        "icon": "⚗️",
        "icon_background": "#FFF4ED",
        "description": "塞两件日常物品进去，现场发明一件没人要的东西。图鉴的存储与检索由 seekdb 承担。",
    },
    "workflow": {
        "graph": {"nodes": nodes, "edges": edges,
                  "viewport": {"x": 0, "y": 0, "zoom": 0.6}},
        "features": {
            "opening_statement": "把两件日常物品塞进发明机，我给你造个没人要的东西。\n\n"
                                 "比如：**猫 + 微波炉**、**拖鞋 + 无人机**、**咖啡 + 电梯**。\n"
                                 "后果自负。",
            "suggested_questions": ["猫 + 微波炉", "拖鞋 + 洗衣机", "咖啡 + 电梯", "谎言 + 充电宝"],
            "suggested_questions_after_answer": {"enabled": True},
            "text_to_speech": {"enabled": False, "voice": "", "language": ""},
            "speech_to_text": {"enabled": False},
            "retriever_resource": {"enabled": False},
            "sensitive_word_avoidance": {"enabled": False},
            "file_upload": {"image": {"enabled": False, "number_limits": 3,
                                      "transfer_methods": ["local_file", "remote_url"]}},
        },
        "environment_variables": [],
        "conversation_variables": [],
    },
    "dependencies": [],
    "kind": "app",
    "version": "0.3.0",
}

print(yaml.dump(dsl, allow_unicode=True, sort_keys=False, width=200))
