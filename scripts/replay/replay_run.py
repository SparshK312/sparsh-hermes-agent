#!/usr/bin/env python3
"""Replay the case set in the sandbox. Run as root: replay_run.py <variant> [case-prefix ...]

Per case: reset the sandbox from the pristine copies, apply the variant (optional
variants/<variant>/apply.sh and variants/<variant>/toolsets), send each turn through
`hermes chat -q`, then record tokens, model calls, cost, model time and what was written
to the sandbox vault. Nothing here touches the live vault or the live ~/.hermes.
"""
import glob, hashlib, json, os, re, sqlite3, subprocess, sys, time

R = "/tmp/hreplay"
variant = sys.argv[1]
only = sys.argv[2:]
DEFAULT_TOOLSETS = "clarify,code_execution,cronjob,file,kanban,memory,session_search,skills,terminal,todo,tts,vision,web"
PRICE = {"in": 3.0, "out": 15.0, "cr": 0.30, "cw": 3.75}   # claude-sonnet-4-6, USD per million tokens
SKIP = ("07_",)   # photo case: the command line cannot attach several images, so it is not comparable

vdir = f"{R}/variants/{variant}"
toolsets = open(f"{vdir}/toolsets").read().strip() if os.path.exists(f"{vdir}/toolsets") else DEFAULT_TOOLSETS
out = f"{R}/out/{variant}"
os.makedirs(out, exist_ok=True)


def sh(cmd, **k):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **k)


def tree(d):
    h = {}
    for root, _, fs in os.walk(d):
        for f in fs:
            p = os.path.join(root, f)
            try:
                h[os.path.relpath(p, d)] = hashlib.md5(open(p, "rb").read()).hexdigest()
            except OSError:
                pass
    return h


def meal_sections(path):
    try:
        txt = open(path, errors="ignore").read()
    except OSError:
        return []
    return [s.strip() for s in re.split(r"\n(?=## )", txt) if s.startswith("## ")]


cases = sorted({os.path.basename(p).split(".t")[0] for p in glob.glob(f"{R}/cases/*.txt")})
for c in cases:
    if c.startswith(SKIP) or (only and not any(c.startswith(o) for o in only)):
        continue
    r = sh(f"rsync -a --delete {R}/pristine/vault/ {R}/work/vault/ && "
           f"rsync -a --delete {R}/pristine/repo_skills/ {R}/work/repo_skills/ && "
           f"rsync -a --delete --exclude hermes-agent --exclude venvs --exclude scripts --exclude bin "
           f"{R}/pristine/dot/ {R}/work/dot/ && chown -R hermes:hermes {R}/work")
    assert r.returncode == 0, r.stderr
    if os.path.exists(f"{vdir}/apply.sh"):
        r = sh(f"bash {vdir}/apply.sh")
        assert r.returncode == 0, r.stderr
    before = tree(f"{R}/work/vault")
    old_meals = {p: meal_sections(f"{R}/work/vault/{p}") for p in before if "Food Log/" in p and p.endswith(".md")}
    turns, sid, t0 = [], None, time.time()
    for k, tf in enumerate(sorted(glob.glob(f"{R}/cases/{c}.t*.txt")), 1):
        resume = f"--resume {sid} " if (k > 1 and sid) else ""
        ts = time.time()
        r = sh(f"{R}/replay_ns.sh hermes chat -Q {resume}--query-file {tf} -t {toolsets} --source telegram --yolo --accept-hooks",
               timeout=900)
        m = re.search(r"session_id:\s*(\S+)", r.stderr + r.stdout)
        sid = m.group(1) if m else sid
        turns.append({"turn": k, "seconds": round(time.time() - ts, 1), "rc": r.returncode,
                      "reply": r.stdout[-2500:], "stderr": r.stderr[-300:]})
    after = tree(f"{R}/work/vault")
    changed = sorted(p for p in after if before.get(p) != after[p])
    res = {"case": c, "variant": variant, "toolsets": toolsets, "wall_seconds": round(time.time() - t0, 1),
           "turns": turns, "files_changed": changed}
    res["new_meals"] = {p: [s for s in meal_sections(f"{R}/work/vault/{p}") if s not in old_meals.get(p, [])]
                        for p in changed if "Food Log/" in p and p.endswith(".md")}
    dbp = f"{R}/work/dot/state.db"
    if os.path.exists(dbp):
        db = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
        tot = dict(inp=0, out=0, cr=0, cw=0)
        for inp, o, cr, cw in db.execute(
                "select coalesce(input_tokens,0),coalesce(output_tokens,0),coalesce(cache_read_tokens,0),"
                "coalesce(cache_write_tokens,0) from sessions"):
            tot["inp"] += inp; tot["out"] += o; tot["cr"] += cr; tot["cw"] += cw
        tot["cost"] = round((tot["inp"] * PRICE["in"] + tot["out"] * PRICE["out"] + tot["cr"] * PRICE["cr"]
                             + tot["cw"] * PRICE["cw"]) / 1e6, 4)
        res["tokens"] = tot
        res["commands"] = []
        for (tc,) in db.execute("select tool_calls from messages where role='assistant' and tool_calls is not null order by rowid"):
            for call in json.loads(tc):
                f = call.get("function", {})
                a = f.get("arguments") or ""
                try:
                    a = json.loads(a)
                except Exception:
                    a = {}
                res["commands"].append((f.get("name"), str(a.get("command") or a.get("name") or a.get("path") or a)[:400]))
    lg = ""
    for p in glob.glob(f"{R}/work/dot/logs/agent.log*"):
        lg += open(p, errors="ignore").read()
    calls = re.findall(r"API call #\d+: .*?in=(\d+) out=(\d+) .*?latency=([\d.]+)s cache=(\d+)/(\d+)", lg)
    res["api_calls"] = len(calls)
    res["api_latency_s"] = round(sum(float(x[2]) for x in calls), 1)
    res["first_call_input"] = int(calls[0][0]) if calls else None
    res["max_input"] = max((int(x[0]) for x in calls), default=None)
    # cost if the prefix had not been cached by the previous case (how a real first message is billed)
    tk = res.get("tokens", {})
    if tk and res["first_call_input"]:
        res["cold_cost"] = round(tk["cost"] + res["first_call_input"] * (PRICE["cw"] - PRICE["cr"]) / 1e6, 4)
    json.dump(res, open(f"{out}/{c}.json", "w"), indent=1)
    print(f"{c:20s} calls={res['api_calls']:2d} in1={res['first_call_input']} max={res['max_input']} "
          f"out={tk.get('out')} cw={tk.get('cw')} cr={tk.get('cr')} ${tk.get('cost')} model_s={res['api_latency_s']} "
          f"rc={[t['rc'] for t in turns]} meals={sum(len(v) for v in res['new_meals'].values())}", flush=True)
