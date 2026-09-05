import re

with open("extensions/content.js", "r", encoding="utf-8") as f:
    content_code = f.read()

with open("extensions/background.js", "r", encoding="utf-8") as f:
    bg_code = f.read()

print(f"content.js size: {len(content_code)} chars, {content_code.count(chr(10))} lines")
print(f"background.js size: {len(bg_code)} chars, {bg_code.count(chr(10))} lines")

# Find all sendMessage calls in content.js
send_matches = re.findall(r'chrome\.runtime\.sendMessage\(\s*\{([^}]+)\}', content_code)
print(f"\nFound {len(send_matches)} sendMessage occurrences in content.js:")
types_sent = set()
for sm in send_matches:
    m = re.search(r'type:\s*["\']([^"\']+)["\']', sm)
    if m:
        types_sent.add(m.group(1))
        print(f"  Sent type: {m.group(1)}")

# Find all message types handled in background.js
handled_matches = re.findall(r'message\.type === ["\']([^"\']+)["\']', bg_code)
print(f"\nHandled types in background.js: {handled_matches}")

unhandled = types_sent - set(handled_matches)
print(f"\nUnhandled message types sent from content.js: {unhandled}")
unused_handlers = set(handled_matches) - types_sent
print(f"Handled in background.js but never sent: {unused_handlers}")
