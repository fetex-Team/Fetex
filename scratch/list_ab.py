import os, json, datetime

items = sorted(os.listdir("ab_results"))
print(f"총 {len(items)}개")
for name in items[:20]:
    p = os.path.join("ab_results", name)
    mtime = os.path.getmtime(p)
    dt = datetime.datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
    kind = "DIR" if os.path.isdir(p) else "FILE"
    print(f"{dt} | {kind} | {name}")
