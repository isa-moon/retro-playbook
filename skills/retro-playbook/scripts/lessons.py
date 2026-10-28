#!/usr/bin/env python3
"""复盘书 · 经验库工具（仅标准库，Python 3.8+）。

只做确定性的事：建库、检索、体检、记账、归档。判断交给 Agent 和人。
除 init / log / archive / index 外，所有命令只读。

用法：
  lessons.py init [路径]            建库（默认 ~/Documents/经验库），并记住位置
  lessons.py where                  打印经验库位置
  lessons.py next-id                下一个教训编号
  lessons.py search 关键词… [--tags a,b] [--limit 8] [--json]
  lessons.py lint [--json]          只读体检：缺字段/空话/过期/疑似重复/僵尸/有害
  lessons.py log ID 事件 [--project 名] [--note 说明]
                                    事件：recalled 被唤醒 / applied 被采用 / helped 有用 / hurt 误导
                                    加 --undo 撤回一次（计数 -1，记录保留）
  lessons.py archive ID --reason 原因  移入 归档/（不删除）
  lessons.py trail 项目名            列出唤醒/验证记录里提到该项目的教训（复盘回填用）
  lessons.py index                  重建 INDEX.md
通用参数：--lib 路径（放在子命令前后都可；优先于环境变量 RETRO_PLAYBOOK_HOME 与 ~/.retro-playbook 指针文件）
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

POINTER = Path.home() / ".retro-playbook"
DEFAULT_LIB = Path.home() / "Documents" / "经验库"
LESSON_DIR, RETRO_DIR, ARCHIVE_DIR = "教训", "复盘", "归档"

REQUIRED = ["id", "title", "when", "do", "type", "confidence", "source", "created"]
FIELD_ORDER = ["id", "title", "when", "do", "scope", "type", "tags", "confidence",
               "cost", "source", "created", "review_by",
               "recalled", "applied", "helped", "hurt"]
COUNTERS = {"recalled": "被唤醒", "applied": "被采用", "helped": "有用", "hurt": "误导"}
INACTIVE = {"已证伪"}

# 写进教训里就说明还没想清楚的词。命中不代表一定不合格，但必须被追问。
VAGUE = ["加强沟通", "多沟通", "沟通不足", "提前准备", "提前规划", "早点", "注意细节",
         "更加重视", "加强管理", "提高效率", "多注意", "下次注意", "做好", "把控好",
         "尽量", "适当", "及时跟进", "加强协同", "提升意识", "引起重视"]
GENERIC_WHEN = ["下次", "以后", "今后", "项目中", "所有项目", "任何时候", "平时"]

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def resolve_lib(arg):
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("RETRO_PLAYBOOK_HOME")
    if env:
        return Path(env).expanduser()
    if POINTER.exists():
        p = POINTER.read_text(encoding="utf-8").strip()
        if p:
            return Path(p).expanduser()
    return None


def need_lib(arg):
    lib = resolve_lib(arg)
    if not lib or not (lib / LESSON_DIR).is_dir():
        die("经验库未初始化：先运行 `lessons.py init [路径]`（默认 %s）" % DEFAULT_LIB, 2)
    return lib


# ---------- frontmatter ----------

def parse(path):
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.split(" #", 1)[0].strip()
        body = text[m.end():]
    else:
        body = text
    return meta, body


def dump(meta, body):
    keys = [k for k in FIELD_ORDER if k in meta] + [k for k in meta if k not in FIELD_ORDER]
    head = "\n".join("%s: %s" % (k, meta[k]) for k in keys)
    return "---\n%s\n---\n%s" % (head, body if body.startswith("\n") else "\n" + body)


def load_all(lib):
    out = []
    for p in sorted((lib / LESSON_DIR).glob("*.md")):
        meta, body = parse(p)
        meta["_path"] = str(p)
        meta["_body"] = body
        out.append(meta)
    return out


def num(meta, k):
    try:
        return int(meta.get(k, 0) or 0)
    except ValueError:
        return 0


def tags_of(meta):
    return [t.strip() for t in re.split(r"[,，、/]", meta.get("tags", "")) if t.strip()]


# ---------- 文本相似度（中文按二字组，英文按词） ----------

CJK = re.compile(r"[一-鿿]+")
WORD = re.compile(r"[A-Za-z0-9]+")


def grams(text):
    text = text or ""
    g = set()
    for run in CJK.findall(text):
        if len(run) == 1:
            g.add(run)
        g.update(run[i:i + 2] for i in range(len(run) - 1))
    g.update(w.lower() for w in WORD.findall(text))
    return g


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


# ---------- 命令 ----------

README = """# 经验库（复盘书）

这里存的是**会被下次用上**的教训，不是复盘报告的堆放处。

- `教训/`：一条教训一个文件。每条都写明「什么时候用（when）」和「到时候做什么（do）」。
- `复盘/`：每次复盘的原始记录，教训从这里提炼，可追溯。
- `归档/`：被合并、证伪或过期的教训。只移不删，随时可以找回。
- `INDEX.md`：自动生成的目录，不要手改。

可以放进团队共享盘或 Git 仓库给大家一起用。
"""


def cmd_init(a):
    lib = Path(a.path or a.lib or DEFAULT_LIB).expanduser().resolve()
    for d in (LESSON_DIR, RETRO_DIR, ARCHIVE_DIR):
        (lib / d).mkdir(parents=True, exist_ok=True)
    if not (lib / "README.md").exists():
        (lib / "README.md").write_text(README, encoding="utf-8")
    POINTER.write_text(str(lib) + "\n", encoding="utf-8")
    write_index(lib)
    print("经验库就绪：%s\n（位置已记在 %s）" % (lib, POINTER))


def cmd_where(a):
    lib = resolve_lib(a.lib)
    if not lib or not (lib / LESSON_DIR).is_dir():
        print("未初始化")
        sys.exit(2)
    print(lib)


def cmd_next_id(a):
    lib = need_lib(a.lib)
    ids = []
    for d in (LESSON_DIR, ARCHIVE_DIR):
        for p in (lib / d).glob("L*.md"):
            m = re.match(r"L(\d+)", p.name)
            if m:
                ids.append(int(m.group(1)))
    print("L%04d" % (max(ids, default=0) + 1))


# 存疑的只轻微降权：风险类教训常常证据不足，但被漏掉的代价更大
CONF_WEIGHT = {"多次验证": 1.3, "单次观察": 1.0, "存疑": 0.8}


def cmd_search(a):
    lib = need_lib(a.lib)
    q = " ".join(a.query)
    qg = grams(q)
    want_tags = {t.strip() for t in re.split(r"[,，、]", a.tags or "") if t.strip()}
    rows = []
    for m in load_all(lib):
        if m.get("confidence") in INACTIVE:
            continue
        lt = set(tags_of(m))
        s = (2.0 * jaccard(qg, grams(m.get("when", ""))) +
             1.0 * jaccard(qg, grams(m.get("title", ""))) +
             0.5 * jaccard(qg, grams(m.get("do", ""))) +
             0.3 * jaccard(qg, grams(m.get("_body", ""))))
        s += 0.4 * len(want_tags & lt) + 0.25 * len({t for t in lt if t and t in q})
        s *= CONF_WEIGHT.get(m.get("confidence", ""), 1.0)
        if num(m, "hurt") > num(m, "helped"):
            s *= 0.5
        if s >= 0.05:
            rows.append((round(s, 3), m))
    rows.sort(key=lambda r: -r[0])
    rows = rows[: a.limit]
    if a.json:
        print(json.dumps([dict({k: v for k, v in m.items() if k != "_body"}, score=s)
                          for s, m in rows], ensure_ascii=False, indent=2))
        return
    if not rows:
        print("没有相关教训。")
        return
    for s, m in rows:
        print("[%s] %s  (相关度 %.2f · %s · 唤醒%d/采用%d/有用%d/误导%d)" % (
            m.get("id"), m.get("title"), s, m.get("confidence", "?"),
            num(m, "recalled"), num(m, "applied"), num(m, "helped"), num(m, "hurt")))
        print("    什么时候：%s" % m.get("when", ""))
        print("    做什么：  %s" % m.get("do", ""))
        if m.get("scope"):
            print("    不适用：  %s" % m.get("scope"))
        print("    文件：    %s" % m["_path"])


def lint_lessons(lib):
    today = dt.date.today()
    lessons = load_all(lib)
    issues = []

    def add(m, kind, msg):
        issues.append({"id": m.get("id", "?"), "title": m.get("title", ""),
                       "kind": kind, "msg": msg, "path": m["_path"]})

    for m in lessons:
        missing = [k for k in REQUIRED if not m.get(k)]
        if missing:
            add(m, "缺字段", "缺少 " + "、".join(missing))
        hits = [w for w in VAGUE if w in (m.get("title", "") + m.get("do", ""))]
        if hits:
            add(m, "空话", "含「%s」——说不出具体动作就不是教训" % "」「".join(hits))
        when = m.get("when", "")
        if when and (len(when) < 8 or any(when.strip().startswith(w) for w in GENERIC_WHEN)):
            add(m, "触发太泛", "when=「%s」，未来没有一个可观察的时刻能对上它" % when)
        if "## 证据" not in m["_body"] and "## 发生了什么" not in m["_body"]:
            add(m, "无证据", "正文缺「发生了什么」证据段")
        rb = m.get("review_by", "")
        try:
            if rb and dt.date.fromisoformat(rb) < today:
                add(m, "到期复核", "review_by=%s 已过，规则/市场可能变了" % rb)
        except ValueError:
            add(m, "格式", "review_by 不是 YYYY-MM-DD")
        try:
            created = dt.date.fromisoformat(m.get("created", ""))
            if (today - created).days > 180 and num(m, "recalled") == 0:
                add(m, "僵尸", "半年没被唤醒过：触发条件写错了，或者它不重要")
        except ValueError:
            pass
        if num(m, "hurt") and num(m, "hurt") >= num(m, "helped"):
            add(m, "有害", "误导 %d 次 ≥ 有用 %d 次" % (num(m, "hurt"), num(m, "helped")))
        if num(m, "helped") >= 2 and m.get("confidence") == "单次观察":
            add(m, "可升级", "已 %d 次有用，可考虑升为「多次验证」" % num(m, "helped"))

    active = [m for m in lessons if m.get("confidence") not in INACTIVE]
    for i, x in enumerate(active):
        gx = grams(x.get("title", "") + x.get("when", ""))
        for y in active[i + 1:]:
            sim = jaccard(gx, grams(y.get("title", "") + y.get("when", "")))
            if sim >= 0.35:
                add(x, "疑似重复", "与 %s「%s」相似度 %.2f，考虑合并" % (
                    y.get("id"), y.get("title"), sim))
    return lessons, issues


def cmd_lint(a):
    lib = need_lib(a.lib)
    lessons, issues = lint_lessons(lib)
    if a.json:
        print(json.dumps({"total": len(lessons), "issues": issues}, ensure_ascii=False, indent=2))
        return
    print("共 %d 条教训，发现 %d 个问题（只读，未修改任何文件）" % (len(lessons), len(issues)))
    for it in issues:
        print("- [%s] %s %s：%s" % (it["kind"], it["id"], it["title"], it["msg"]))


def find_lesson(lib, lid):
    for p in sorted((lib / LESSON_DIR).glob("%s*.md" % lid)):
        if re.match(r"%s(?!\d)" % re.escape(lid), p.name):
            return p
    die("找不到教训 %s（已归档的在 %s/ 里）" % (lid, ARCHIVE_DIR))


def cmd_log(a):
    lib = need_lib(a.lib)
    if a.event not in COUNTERS:
        die("事件只能是：" + " / ".join(COUNTERS))
    p = find_lesson(lib, a.id)
    meta, body = parse(p)
    meta[a.event] = str(max(0, num(meta, a.event) + (-1 if a.undo else 1)))
    line = "- %s %s%s%s%s" % (dt.date.today().isoformat(), "撤回" if a.undo else "",
                              COUNTERS[a.event],
                            "（%s）" % a.project if a.project else "",
                            "：" + a.note if a.note else "")
    if "## 验证记录" not in body:
        body = body.rstrip() + "\n\n## 验证记录\n"
    body = body.rstrip() + "\n" + line + "\n"
    p.write_text(dump(meta, body), encoding="utf-8")
    print("%s %s → %s" % (a.id, COUNTERS[a.event], meta[a.event]))
    if a.event == "helped" and num(meta, "helped") >= 2 and meta.get("confidence") == "单次观察":
        print("提示：已 %s 次有用，可询问用户是否升为「多次验证」" % meta["helped"])
    if a.event == "hurt":
        print("提示：记下误导原因；误导 ≥ 有用时 lint 会标为「有害」，需和用户决定改写、收窄 scope 或证伪")


def cmd_archive(a):
    lib = need_lib(a.lib)
    p = find_lesson(lib, a.id)
    meta, body = parse(p)
    body = body.rstrip() + "\n\n## 归档\n- %s：%s\n" % (dt.date.today().isoformat(), a.reason)
    dest = lib / ARCHIVE_DIR / p.name
    n = 2
    while dest.exists():
        dest = lib / ARCHIVE_DIR / ("%s-%d%s" % (p.stem, n, p.suffix))
        n += 1
    dest.write_text(dump(meta, body), encoding="utf-8")
    p.unlink()
    write_index(lib)
    print("已移入 %s（未删除）" % dest)


def cmd_trail(a):
    lib = need_lib(a.lib)
    tag = "（%s）" % a.project
    found = 0
    for d in (LESSON_DIR, ARCHIVE_DIR):
        for p in sorted((lib / d).glob("*.md")):
            meta, body = parse(p)
            lines = [l for l in body.splitlines() if l.startswith("- ") and tag in l]
            if lines:
                found += 1
                print("[%s] %s%s" % (meta.get("id"), meta.get("title"), "（已归档）" if d == ARCHIVE_DIR else ""))
                for l in lines:
                    print("    " + l[2:])
    if not found:
        print("项目「%s」没有唤醒记录。" % a.project)


def write_index(lib):
    lessons = [m for m in load_all(lib) if m.get("confidence") not in INACTIVE]
    groups = {}
    for m in lessons:
        for t in (tags_of(m) or ["未分类"])[:1]:
            groups.setdefault(t, []).append(m)
    lines = ["# 经验库目录（自动生成，勿手改）", "",
             "共 %d 条现役教训 · 更新于 %s" % (len(lessons), dt.date.today().isoformat()), ""]
    for t in sorted(groups):
        lines.append("## %s" % t)
        for m in groups[t]:
            rel = os.path.relpath(m["_path"], lib)
            lines.append("- [%s](%s) %s —— **什么时候**：%s" % (
                m.get("id"), rel.replace(os.sep, "/"), m.get("title"), m.get("when")))
        lines.append("")
    (lib / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def cmd_index(a):
    lib = need_lib(a.lib)
    write_index(lib)
    print("已重建 %s" % (lib / "INDEX.md"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="复盘书 · 经验库工具")
    ap.add_argument("--lib")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lib", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, f):
        s = sub.add_parser(name, parents=[common])
        s.set_defaults(f=f)
        return s

    add("init", cmd_init).add_argument("path", nargs="?")
    add("where", cmd_where)
    add("next-id", cmd_next_id)
    add("trail", cmd_trail).add_argument("project")
    s = add("search", cmd_search)
    s.add_argument("query", nargs="+")
    s.add_argument("--tags")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--json", action="store_true")
    add("lint", cmd_lint).add_argument("--json", action="store_true")
    s = add("log", cmd_log)
    s.add_argument("id")
    s.add_argument("event")
    s.add_argument("--project")
    s.add_argument("--note")
    s.add_argument("--undo", action="store_true", help="撤回一次记录（计数 -1，留痕）")
    s = add("archive", cmd_archive)
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    add("index", cmd_index)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
