import os
import sqlite3
import shutil
import tempfile

temp_dir = tempfile.gettempdir()

def check_history(profile_name, history_path):
    if not os.path.exists(history_path):
        return
    tmp_copy = os.path.join(temp_dir, f"hist_{profile_name}.sqlite")
    try:
        shutil.copy2(history_path, tmp_copy)
    except Exception as e:
        return
    
    try:
        conn = sqlite3.connect(tmp_copy)
        cur = conn.cursor()
        cur.execute("SELECT url, title, datetime(last_visit_time/1000000-11644473600, 'unixepoch') FROM urls WHERE url LIKE '%apollo.io%' ORDER BY last_visit_time DESC")
        rows = cur.fetchall()
        print(f"\n==========================================")
        print(f"Profile: {profile_name} - Found {len(rows)} Apollo URLs")
        print(f"==========================================")
        titles = set()
        for u, t, d in rows:
            if t and t not in titles and "Apollo" not in t:
                titles.add(t)
                print(f"  Title: {t}")
            # Check if url has view or search param
            if "finder_view" in u or "view_id" in u or "saved_search" in u:
                print(f"  View URL: {u[:150]}")
        # Also print sample titles even if they have Apollo
        sample_titles = set(t for u, t, d in rows if t)
        print(f"  All unique titles ({len(sample_titles)}): {list(sample_titles)[:10]}")
        conn.close()
    except Exception as e:
        print(f"Error reading {profile_name}: {e}")

chrome_dir = r"C:\Users\test\AppData\Local\Google\Chrome\User Data"
for p in os.listdir(chrome_dir):
    hp = os.path.join(chrome_dir, p, "History")
    if os.path.exists(hp):
        check_history(f"Chrome {p}", hp)

edge_dir = r"C:\Users\test\AppData\Local\Microsoft\Edge\User Data"
if os.path.exists(edge_dir):
    for p in os.listdir(edge_dir):
        hp = os.path.join(edge_dir, p, "History")
        if os.path.exists(hp):
            check_history(f"Edge {p}", hp)
