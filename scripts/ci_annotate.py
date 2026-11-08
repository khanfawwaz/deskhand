"""CI helper: turn a pytest junit report into GitHub annotations (readable without access to the job log)."""
import sys
import xml.etree.ElementTree as ET

try:
    root = ET.parse(sys.argv[1] if len(sys.argv) > 1 else "junit.xml").getroot()
except (OSError, ET.ParseError) as e:
    print(f"::error::no test report was written ({e})")
    raise SystemExit(0)
n = 0
for case in root.iter("testcase"):
    for bad in list(case.findall("failure")) + list(case.findall("error")):
        n += 1
        if n <= 10:                                   # GitHub shows at most 10 annotations per step
            msg = (bad.get("message") or bad.text or "").strip().replace("\n", " | ")[:400]
            print(f"::error title={case.get('classname')}::{case.get('name')} -> {msg}")
print(f"{n} failing test(s)")
