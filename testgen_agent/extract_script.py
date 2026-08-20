"""
Extract k6 script from Lambda console response.

Usage:
  1. Copy Lambda response from console (Cmd+C)
  2. Run: pbpaste | python3 extract_script.py

Or:
  python3 extract_script.py < response.json
"""
import json
import sys

raw = sys.stdin.read()
response = json.loads(raw)
body = json.loads(response["body"])
script = body["script"]
config = body.get("config", {})

output_file = "/Users/srishwad/Desktop/perfSage/console_test.js"
with open(output_file, "w") as f:
    f.write(script)

print(f"Script saved to: {output_file}")
print(f"Lines: {len(script.splitlines())}")
print()
if config:
    print(f"Config:")
    print(f"  test_type: {config.get('test_type')}")
    print(f"  executor:  {config.get('executor', {}).get('type')}")
    print(f"  auth:      {config.get('auth_type')}")
print()
print(f"Now run:")
print(f"  k6 run --vus 5 --iterations 5 {output_file}")
