import json

path = r"C:\Users\test\.gemini\antigravity-ide\brain\3332c619-953b-4d94-956b-1fea6e59d8d3\.system_generated\logs\transcript.jsonl"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

for idx in range(710, 753):
    try:
        data = json.loads(lines[idx])
        print(f"Step {data.get('step_index')}: {data.get('source')} | {data.get('type')}")
        if data.get("source") == "USER_EXPLICIT":
            print(f"  USER: {data.get('content')}")
        if data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
            print(f"  PLANNER: {data.get('content')}")
    except Exception as e:
        pass
