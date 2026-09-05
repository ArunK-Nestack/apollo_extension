import sys
import os
sys.path.append(os.getcwd())
from backend.api import classify_novel_titles_compact_llm

try:
    titles = ['Chief Everything Officer', 'Floor Sweeper', 'VP of Random Stuff']
    results, stats = classify_novel_titles_compact_llm(titles)
    print('LLM Response Success!')
    for k, v in results.items():
        print(f"{k}: {v.get('status')}")
    print('Stats:', stats)
except Exception as e:
    print('LLM Call Failed:', e)
