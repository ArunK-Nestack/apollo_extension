with open("extensions/content.js", "r", encoding="utf-8") as f:
    content = f.read()

print("Analyzing content.js...")

# 1. Check CSV Export function
csv_pos = content.find("exportToCsv")
if csv_pos != -1:
    print("\n--- CSV Export Section ---")
    print(content[csv_pos:csv_pos+1500])

# 2. Check storage save/load
save_pos = content.find("saveRequiredContactsNow")
if save_pos != -1:
    print("\n--- Storage Save Section ---")
    print(content[save_pos:save_pos+1200])

load_pos = content.find("loadStoredData")
if load_pos != -1:
    print("\n--- Storage Load Section ---")
    print(content[load_pos:load_pos+1200])
