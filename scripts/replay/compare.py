#!/usr/bin/env python3
"""Compare two replay variants case by case. Read-only. Usage: compare.py baseline both"""
import glob, json, os, re, sys

a, b = sys.argv[1], sys.argv[2]
R = "/tmp/hreplay/out"


def load(v):
    return {os.path.basename(p)[:-5]: json.load(open(p)) for p in glob.glob(f"{R}/{v}/*.json")}


def macros(d):
    """Sum kcal/protein of the meals this run added (new runs record them; older ones only have the reply)."""
    k = p = n = 0
    for secs in d.get("new_meals", {}).values():
        for s in secs:
            m = re.search(r"\*\*Macros:\*\*\s*([\d,]+)\s*kcal\s*·\s*([\d.]+)g protein", s)
            if m:
                k += int(m.group(1).replace(",", "")); p += float(m.group(2)); n += 1
    return n, k, round(p)


A, B = load(a), load(b)
ta = tb = 0
for c in sorted(set(A) & set(B)):
    x, y = A[c], B[c]
    tx, ty = x["tokens"], y["tokens"]
    ta += tx["cost"]; tb += ty["cost"]
    print(f"\n### {c}")
    print(f"  {a:9s} calls={x['api_calls']:2d} out={tx['out']:6d} cw={tx['cw']:6d} cr={tx['cr']:7d} ${tx['cost']:.3f} model_s={x['api_latency_s']:.0f} rc={[t['rc'] for t in x['turns']]}")
    print(f"  {b:9s} calls={y['api_calls']:2d} out={ty['out']:6d} cw={ty['cw']:6d} cr={ty['cr']:7d} ${ty['cost']:.3f} model_s={y['api_latency_s']:.0f} rc={[t['rc'] for t in y['turns']]} meals+kcal+protein={macros(y)}")
    tools = [n for n, _ in y.get("commands", [])]
    print(f"  {b} tool steps: {tools}")
    for p, secs in y.get("new_meals", {}).items():
        for s in secs:
            head = s.split("\n")[0]
            items = re.findall(r"^- (.+)$", s, re.M)
            mac = re.search(r"\*\*Macros:\*\*.*", s)
            print(f"    {os.path.basename(p)} {head} | {mac.group(0)[11:] if mac else ''} | {items}"[:330])
print(f"\nTOTAL {a}: ${ta:.2f}   {b}: ${tb:.2f}   change: {100*(tb-ta)/ta:+.0f}%")
