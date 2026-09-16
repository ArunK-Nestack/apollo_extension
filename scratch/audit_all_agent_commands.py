import json

transcript_path = r"C:\Users\test\.gemini\antigravity-ide\brain\6929fd81-f450-48c4-a2e6-cff56e7f9ab8\.system_generated\logs\transcript_full.jsonl"

print("--- ALL COMMANDS RUN BY ANTIGRAVITY IN THIS CONVERSATION ---")
with open(transcript_path, "r", encoding="utf-8") as f:
    for line in f:
        try:
            entry = json.loads(line)
            if entry.get("type") == "PLANNER_RESPONSE":
                calls = entry.get("tool_calls") or []
                for c in calls:
                    if c.get("name") == "run_command":
                        cmd = c.get("args", {}).get("CommandLine", "")
                        created_at = entry.get("created_at", "")
                        print(f"[{created_at}] {cmd}")
        except Exception:
            pass
