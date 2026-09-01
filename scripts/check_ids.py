"""Cross-check: every DOM id referenced by static/app.js must exist in index.html."""
import re
import sys

# Windows consoles default to cp1252 and crash on the ✔ symbol; force UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

js = open("static/app.js", encoding="utf-8").read()
html = open("static/index.html", encoding="utf-8").read()

used = set(re.findall(r'getElementById\("([\w-]+)"\)', js))
used |= set(re.findall(r'\$\("([\w-]+)"\)', js))
have = set(re.findall(r'id="([\w-]+)"', html))

missing = sorted(used - have)
print(f"IDs referenced by app.js: {len(used)}")
print(f"IDs defined in index.html: {len(have)}")
if missing:
    print("MISSING:", missing)
    raise SystemExit(1)
print("All referenced IDs exist ✔")
