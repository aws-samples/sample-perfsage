"""
Generate a Lambda console test event from a spec file + prompt.

Usage:
    python3 make_test_event.py <spec_file> "<your prompt>"

Example:
    python3 make_test_event.py tests/fixtures/simple/dummyjson.yaml "Stress test 100 users for 5 minutes"

Output: Prints the JSON test event — copy-paste into Lambda console Test tab.
"""
import json
import sys

if len(sys.argv) < 3:
    print("Usage: python3 make_test_event.py <spec_file> \"<your prompt>\"")
    print()
    print("Example:")
    print('  python3 make_test_event.py tests/fixtures/simple/dummyjson.yaml "Stress test 100 users"')
    sys.exit(1)

spec_file = sys.argv[1]
prompt = sys.argv[2]

# Detect format
fmt = "json" if spec_file.endswith(".json") else "yaml"

# Read spec
with open(spec_file) as f:
    spec_content = f.read()

# Build the event
body = json.dumps({
    "spec": spec_content,
    "prompt": prompt,
    "format": fmt,
})

event = json.dumps({"body": body}, indent=2)

print(event)
print()
print(f"--- Copy the above JSON into Lambda console Test tab ---")
print(f"Spec: {spec_file} ({len(spec_content)} chars)")
print(f"Prompt: {prompt}")
print(f"Format: {fmt}")
