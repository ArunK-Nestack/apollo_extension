import json

transcript_path = r"C:\Users\test\.gemini\antigravity-ide\brain\6929fd81-f450-48c4-a2e6-cff56e7f9ab8\.system_generated\logs\transcript_full.jsonl"

with open(transcript_path, "r", encoding="utf-8") as f:
    for line in f:
        try:
            entry = json.loads(line)
            created_at = entry.get("created_at", "")
            # Check timestamps around 06:45Z - 07:15Z (12:15 - 12:45 IST)
            if "2026-09-15T06:" in created_at or "2026-09-15T07:0" in created_at:
                ttype = entry.get("type", "")
                if ttype in ("RUN_COMMAND", "USER_INPUT"):
                    print(f"[{created_at}] Type: {ttype}")
                    if ttype == "USER_INPUT":
                        print("  USER:", entry.get("content", "")[:100])
                    elif ttype == "RUN_COMMAND":
                        content = entry.get("content", "")
                        print("  CMD:", content[:150].replace("\n", " "))
        except Exception:
            pass
