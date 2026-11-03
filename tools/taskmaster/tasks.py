#!/usr/bin/env python3
"""Task state helper for the task-driven-build skill (stdlib only).

Run from the repo root:  python tasks.py [--file PATH] [--tag TAG] <command> ...

Commands: init, status, list, next, show, set-status, log, add, validate
IDs are "3" for a task and "3.2" for subtask 2 of task 3.
"""
import argparse
import datetime
import json
import os
import sys

DEFAULT_FILE = ".taskmaster/tasks/tasks.json"
STATUSES = ["pending", "in-progress", "review", "done", "deferred", "cancelled"]
PRIORITY = {"high": 3, "medium": 2, "low": 1}
RUNNERS = ["kernel", "local", "model", "human"]  # who executes a task; missing means "model"
ICON = {"pending": "[ ]", "in-progress": "[~]", "review": "[?]", "done": "[x]",
        "deferred": "[-]", "cancelled": "[/]"}


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- storage ----------

def load(path, tag):
    if not os.path.exists(path):
        die(f"{path} not found. Run `tasks.py init` first.")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if tag not in data:
        die(f"tag '{tag}' not found (available: {', '.join(data) or 'none'})")
    return data


def save(path, data, tag):
    data[tag].setdefault("metadata", {})["updated"] = now()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


# ---------- id helpers ----------

def parse_id(s):
    s = str(s)
    parts = s.split(".")
    try:
        if len(parts) == 1:
            return int(parts[0]), None
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except ValueError:
        pass
    die(f"bad id '{s}' (use 3 or 3.2)")


def lookup(tasks, sid):
    """Return (item, parent_or_None), or (None, None) if the id does not exist."""
    tid, sub = parse_id(sid)
    task = next((t for t in tasks if t.get("id") == tid), None)
    if task is None:
        return None, None
    if sub is None:
        return task, None
    st = next((s for s in task.get("subtasks", []) if s.get("id") == sub), None)
    return (st, task) if st is not None else (None, None)


def find(tasks, sid):
    """Like lookup, but exits with an error if the id does not exist."""
    item, parent = lookup(tasks, sid)
    if item is None:
        die(f"{sid} not found")
    return item, parent


def full_id(item, parent):
    return f"{parent['id']}.{item['id']}" if parent else str(item["id"])


def norm_dep(dep, parent):
    """Normalise a dependency to an absolute id string.
    In a subtask, an int means a sibling subtask; a string is absolute."""
    if isinstance(dep, int) and parent is not None:
        return f"{parent['id']}.{dep}"
    return str(dep)


def status_of(item):
    return (item.get("status") or "pending").lower()


def done_ids(tasks):
    out = set()
    for t in tasks:
        if status_of(t) == "done":
            out.add(str(t["id"]))
        for s in t.get("subtasks", []):
            if status_of(s) == "done":
                out.add(f"{t['id']}.{s['id']}")
    return out


def deps_of(item, parent):
    return [norm_dep(d, parent) for d in item.get("dependencies", [])]


def unmet(item, parent, done):
    return [d for d in deps_of(item, parent) if d not in done]


def all_ids(tasks):
    ids = set()
    for t in tasks:
        ids.add(str(t["id"]))
        for s in t.get("subtasks", []):
            ids.add(f"{t['id']}.{s['id']}")
    return ids


def prio(item, parent=None):
    p = item.get("priority") or (parent or {}).get("priority") or "medium"
    return PRIORITY.get(p, 2)


# ---------- next-task selection ----------

def pick_next(tasks):
    """Resume in-progress work first, then highest priority with deps satisfied.
    Descends into subtasks so the result is the actual unit of work."""
    done = done_ids(tasks)
    live = ("pending", "in-progress")

    # 1. subtasks of an in-progress parent
    subs = []
    for t in tasks:
        if status_of(t) != "in-progress":
            continue
        for s in t.get("subtasks", []):
            if status_of(s) in live and not unmet(s, t, done):
                subs.append((t, s))
    if subs:
        subs.sort(key=lambda ts: (0 if status_of(ts[1]) == "in-progress" else 1,
                                  -prio(ts[1], ts[0]), len(ts[1].get("dependencies", [])),
                                  ts[0]["id"], ts[1]["id"]))
        return subs[0]

    # 2. top-level tasks
    elig = [t for t in tasks if status_of(t) in live and not unmet(t, None, done)]
    if not elig:
        return None
    elig.sort(key=lambda t: (0 if status_of(t) == "in-progress" else 1,
                             -prio(t), len(t.get("dependencies", [])), t["id"]))
    top = elig[0]
    # descend into first workable subtask, if any are left
    open_subs = [s for s in top.get("subtasks", []) if status_of(s) in live]
    for s in open_subs:
        if not unmet(s, top, done):
            return top, s
    return None, top


# ---------- validation ----------

def check_runner(item, fid, is_subtask, errors):
    """Rules for the optional `runner` / `verify` fields (see DETERMINISTIC.md).
    Unknown keys stay allowed; only these keys are checked."""
    runner = item.get("runner")
    verify = item.get("verify")
    if "runner" in item and runner not in RUNNERS:
        errors.append(f"{fid}: runner '{runner}' is not one of {', '.join(RUNNERS)}")
    if "verify" in item and (not isinstance(verify, str) or not verify.strip()):
        errors.append(f"{fid}: verify must be a non-empty string (a command, or 'manual')")
    for key, kind in (("verifyTimeoutSeconds", "positive int"), ("verifyExpectedExit", "int"),
                      ("verifyCwd", "non-empty string")):
        if key not in item:
            continue
        v = item[key]
        if kind == "positive int":
            ok = isinstance(v, int) and not isinstance(v, bool) and v > 0
        elif kind == "int":
            ok = isinstance(v, int) and not isinstance(v, bool)
        else:
            ok = isinstance(v, str) and bool(v.strip())
        if not ok:
            errors.append(f"{fid}: {key} must be a {kind}")
    if runner == "kernel":
        if is_subtask:
            errors.append(f"{fid}: runner 'kernel' is supported on top-level tasks only")
        if not isinstance(verify, str) or not verify.strip():
            errors.append(f"{fid}: runner 'kernel' needs a non-empty verify command")
        elif verify.strip().lower() == "manual":
            errors.append(f"{fid}: runner 'kernel' cannot use verify 'manual' (it needs a command)")


def validate(tasks):
    errors, warns = [], []
    ids = all_ids(tasks)
    seen = set()
    for t in tasks:
        if t.get("id") in seen:
            errors.append(f"duplicate task id {t.get('id')}")
        seen.add(t.get("id"))
        sub_seen = set()
        for s in t.get("subtasks", []):
            if s.get("id") in sub_seen:
                errors.append(f"duplicate subtask id {t['id']}.{s.get('id')}")
            sub_seen.add(s.get("id"))

    graph = {}
    for t in tasks:
        items = [(t, None)] + [(s, t) for s in t.get("subtasks", [])]
        for item, parent in items:
            fid = full_id(item, parent)
            st = status_of(item)
            if st not in STATUSES:
                errors.append(f"{fid}: unknown status '{st}'")
            check_runner(item, fid, parent is not None, errors)
            deps = deps_of(item, parent)
            graph[fid] = deps
            for d in deps:
                if d not in ids:
                    errors.append(f"{fid}: depends on missing id {d}")
                elif d == fid:
                    errors.append(f"{fid}: depends on itself")
                elif parent is None and "." not in d and int(d) > t["id"]:
                    errors.append(f"{fid}: depends on later task {d} (tasks may depend only on lower ids)")
            if not item.get("testStrategy") and st not in ("cancelled", "deferred"):
                warns.append(f"{fid}: no testStrategy")
            if st in ("pending", "in-progress"):
                for d in deps:
                    dt, _ = lookup(tasks, d)
                    if dt is not None and status_of(dt) in ("cancelled", "deferred"):
                        warns.append(f"{fid}: blocked by {status_of(dt)} dependency {d}")
        if status_of(t) == "done":
            for s in t.get("subtasks", []):
                if status_of(s) in ("pending", "in-progress", "review"):
                    errors.append(f"{t['id']} is done but subtask {t['id']}.{s['id']} is {status_of(s)}")
        if (t.get("complexity") or 0) >= 7 and not t.get("subtasks") and status_of(t) in ("pending", "in-progress"):
            warns.append(f"{t['id']}: complexity {t['complexity']} but no subtasks (expand it before starting)")

    # cycle detection (DFS)
    state = {}

    def visit(n, stack):
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            errors.append("dependency cycle: " + " -> ".join(stack[stack.index(n):] + [n]))
            return
        state[n] = 1
        for d in graph.get(n, []):
            if d in graph:
                visit(d, stack + [n])
        state[n] = 2

    for n in graph:
        visit(n, [])

    inprog = [str(t["id"]) for t in tasks if status_of(t) == "in-progress"]
    if len(inprog) > 1:
        warns.append(f"{len(inprog)} tasks in progress ({', '.join(inprog)}): finish or defer before starting more")
    return errors, warns


# ---------- display ----------

def line(item, parent=None, indent=""):
    fid = full_id(item, parent)
    extra = ""
    if item.get("priority") and not parent:
        extra += f" ({item['priority']})"
    if item.get("complexity"):
        extra += f" c{item['complexity']}"
    return f"{indent}{ICON.get(status_of(item), '[ ]')} {fid} {item.get('title', '')}{extra}"


def cmd_init(a):
    if os.path.exists(a.file):
        die(f"{a.file} already exists")
    os.makedirs(os.path.dirname(a.file) or ".", exist_ok=True)
    data = {a.tag: {"tasks": [], "metadata": {"created": now(), "updated": now()}}}
    save(a.file, data, a.tag)
    docs = os.path.join(os.path.dirname(os.path.dirname(a.file)), "docs")
    os.makedirs(docs, exist_ok=True)
    pl = os.path.join(docs, "parking-lot.md")
    if not os.path.exists(pl):
        with open(pl, "w", encoding="utf-8") as f:
            f.write("# Parking lot\n\nIdeas that are not part of the current plan. "
                    "Review at the end of each phase: promote, defer or drop.\n\n")
    print(f"created {a.file} and {pl}")


def cmd_status(a):
    data = load(a.file, a.tag)
    tasks = data[a.tag]["tasks"]
    counts = {s: 0 for s in STATUSES}
    for t in tasks:
        counts[status_of(t)] = counts.get(status_of(t), 0) + 1
    total = len(tasks)
    pct = round(100 * counts["done"] / total) if total else 0
    print(f"[{a.tag}] {total} tasks, {pct}% done: " +
          ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    for t in tasks:
        if status_of(t) == "in-progress":
            print("in progress: " + line(t))
            for s in t.get("subtasks", []):
                if status_of(s) in ("in-progress", "review"):
                    print(line(s, t, "    "))
    nxt = pick_next(tasks)
    if nxt:
        parent, item = nxt
        print("next: " + line(item, parent))
    elif total and counts["done"] + counts["cancelled"] + counts["deferred"] == total:
        print("next: nothing left. Review the PRD success criteria and the parking lot.")
    elif total:
        print("next: nothing is workable (everything left is blocked). Check dependencies.")
    errors, warns = validate(tasks)
    for e in errors:
        print("ERROR: " + e)
    for w in warns:
        print("warn: " + w)


def cmd_list(a):
    data = load(a.file, a.tag)
    for t in data[a.tag]["tasks"]:
        if a.status and status_of(t) != a.status and not any(
                status_of(s) == a.status for s in t.get("subtasks", [])):
            continue
        print(line(t))
        if a.subtasks or a.status:
            for s in t.get("subtasks", []):
                if not a.status or status_of(s) == a.status:
                    print(line(s, t, "    "))


def cmd_next(a):
    data = load(a.file, a.tag)
    tasks = data[a.tag]["tasks"]
    nxt = pick_next(tasks)
    if not nxt:
        print("nothing workable: all tasks are done, or what's left is blocked")
        sys.exit(1)
    parent, item = nxt
    print(line(item, parent))
    if parent:
        print(f"  part of task {parent['id']}: {parent.get('title', '')}")
    if not parent and (item.get("complexity") or 0) >= 7 and not item.get("subtasks"):
        print(f"  note: complexity {item['complexity']} with no subtasks. Expand before starting.")
    print(f"  show details: tasks.py show {full_id(item, parent)}")


def cmd_show(a):
    data = load(a.file, a.tag)
    tasks = data[a.tag]["tasks"]
    item, parent = find(tasks, a.id)
    done = done_ids(tasks)
    print(line(item, parent))
    print(f"status: {status_of(item)}")
    if item.get("description"):
        print(f"description: {item['description']}")
    deps = deps_of(item, parent)
    if deps:
        print("depends on: " + ", ".join(f"{d} {'(done)' if d in done else '(NOT done)'}" for d in deps))
    if item.get("details"):
        print("\ndetails:\n" + item["details"])
    if item.get("testStrategy"):
        print("\ntest strategy: " + item["testStrategy"])
    if item.get("subtasks"):
        print("\nsubtasks:")
        for s in item["subtasks"]:
            print(line(s, item, "  "))


def cmd_set_status(a):
    if a.status not in STATUSES:
        die(f"status must be one of: {', '.join(STATUSES)}")
    data = load(a.file, a.tag)
    tasks = data[a.tag]["tasks"]
    item, parent = find(tasks, a.id)
    done = done_ids(tasks)
    if a.status in ("in-progress", "done") and not a.force:
        miss = unmet(item, parent, done)
        if miss:
            die(f"{a.id} has unmet dependencies: {', '.join(miss)} (use --force to override)")
    if a.status == "done" and not a.force and not parent:
        open_subs = [full_id(s, item) for s in item.get("subtasks", [])
                     if status_of(s) in ("pending", "in-progress", "review")]
        if open_subs:
            die(f"{a.id} still has open subtasks: {', '.join(open_subs)} (use --force to override)")
    item["status"] = a.status
    msgs = []
    if parent and a.status == "in-progress" and status_of(parent) == "pending":
        parent["status"] = "in-progress"
        msgs.append(f"task {parent['id']} also set to in-progress")
    if parent and a.status == "done":
        left = [s for s in parent.get("subtasks", [])
                if status_of(s) in ("pending", "in-progress", "review")]
        if not left:
            msgs.append(f"all subtasks of {parent['id']} are done: run its test strategy, then mark {parent['id']} done")
    save(a.file, data, a.tag)
    print(f"{a.id} -> {a.status}")
    for m in msgs:
        print(m)
    if a.status == "in-progress":
        others = [str(t["id"]) for t in tasks
                  if status_of(t) == "in-progress" and t is not (parent or item)]
        if others:
            print(f"warn: other tasks already in progress: {', '.join(others)}")


def cmd_log(a):
    data = load(a.file, a.tag)
    item, parent = find(data[a.tag]["tasks"], a.id)
    entry = f"[{now()}] {a.text}"
    item["details"] = (item.get("details", "").rstrip() + "\n\n" + entry).lstrip("\n")
    save(a.file, data, a.tag)
    print(f"logged to {a.id}")


def cmd_add(a):
    data = load(a.file, a.tag)
    tasks = data[a.tag]["tasks"]
    deps = []
    for d in (a.deps.split(",") if a.deps else []):
        d = d.strip()
        if d.startswith("top:"):
            d = d[4:]
            deps.append(d if a.parent is not None else int(d))
        elif d:
            deps.append(int(d) if d.isdigit() else d)
    new = {
        "title": a.title,
        "description": a.description or "",
        "status": "pending",
        "priority": a.priority,
        "dependencies": deps,
        "details": a.details or "",
        "testStrategy": a.test or "",
    }
    if a.complexity:
        new["complexity"] = a.complexity
    if a.parent is not None:
        parent, _ = find(tasks, str(a.parent))
        new["id"] = max([s["id"] for s in parent.get("subtasks", [])] + [0]) + 1
        new.pop("priority")
        parent.setdefault("subtasks", []).append(new)
        fid = f"{parent['id']}.{new['id']}"
    else:
        new["id"] = max([t["id"] for t in tasks] + [0]) + 1
        new["subtasks"] = []
        tasks.append(new)
        fid = str(new["id"])
    errors, _ = validate(tasks)
    if errors:
        print("warning: the new item introduces problems:")
        for e in errors:
            print("  " + e)
    save(a.file, data, a.tag)
    print(f"added {fid}: {a.title}")


def cmd_validate(a):
    data = load(a.file, a.tag)
    errors, warns = validate(data[a.tag]["tasks"])
    for e in errors:
        print("ERROR: " + e)
    for w in warns:
        print("warn: " + w)
    if not errors and not warns:
        print("ok: no problems found")
    sys.exit(1 if errors else 0)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", default=DEFAULT_FILE)
    p.add_argument("--tag", default="master")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sp = sub.add_parser("list")
    sp.add_argument("--status")
    sp.add_argument("--subtasks", action="store_true")
    sp.set_defaults(fn=cmd_list)
    sub.add_parser("next").set_defaults(fn=cmd_next)
    sp = sub.add_parser("show")
    sp.add_argument("id")
    sp.set_defaults(fn=cmd_show)
    sp = sub.add_parser("set-status")
    sp.add_argument("id")
    sp.add_argument("status")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_set_status)
    sp = sub.add_parser("log")
    sp.add_argument("id")
    sp.add_argument("text")
    sp.set_defaults(fn=cmd_log)
    sp = sub.add_parser("add")
    sp.add_argument("--title", required=True)
    sp.add_argument("--description")
    sp.add_argument("--details")
    sp.add_argument("--test", help="test strategy")
    sp.add_argument("--priority", default="medium", choices=list(PRIORITY))
    sp.add_argument("--deps", help="comma-separated. Top-level task: 1,2 or 2.1. "
                    "Subtask (--parent): a bare number is a sibling subtask, 3.1 is an "
                    "absolute subtask, top:3 is top-level task 3")
    sp.add_argument("--parent", type=int, help="add as a subtask of this task id")
    sp.add_argument("--complexity", type=int)
    sp.set_defaults(fn=cmd_add)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
