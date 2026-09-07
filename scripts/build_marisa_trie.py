import os
import sys
import time
import marisa_trie  # type: ignore

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.api import _strip_tld, _clean_slug

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "domain_slugs_cache.txt")
MARISA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "domain_trie.marisa")

def build_marisa_trie():
    print(f"Reading domain slugs from {CACHE_FILE}...")
    t0 = time.perf_counter()

    if not os.path.exists(CACHE_FILE):
        print(f"Error: {CACHE_FILE} not found!")
        return

    entries = {}
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            raw_dom = line.strip().lower()
            if not raw_dom:
                continue
            slug = _strip_tld(raw_dom)
            cslug = _clean_slug(raw_dom)
            raw_bytes = raw_dom.encode("utf-8")
            if slug and len(slug) >= 3 and slug not in entries:
                entries[slug] = raw_bytes
            if cslug and cslug != slug and len(cslug) >= 3 and cslug not in entries:
                entries[cslug] = raw_bytes

    dur_read = time.perf_counter() - t0
    print(f"Collected {len(entries):,} unique slug entries in {dur_read:.2f}s. Compiling MARISA-Trie...")

    t1 = time.perf_counter()
    # Build BytesTrie
    trie = marisa_trie.BytesTrie(entries.items())
    trie.save(MARISA_FILE)
    dur_build = time.perf_counter() - t1

    file_size_mb = os.path.getsize(MARISA_FILE) / (1024 * 1024)
    print(f"Compiled and saved {MARISA_FILE} in {dur_build:.2f}s!")
    print(f"Binary file size: {file_size_mb:.2f} MB")

    # Test mmap load speed
    t2 = time.perf_counter()
    loaded_trie = marisa_trie.BytesTrie()
    loaded_trie.mmap(MARISA_FILE)
    dur_mmap = (time.perf_counter() - t2) * 1000
    print(f"Memory-map load test: {dur_mmap:.2f} ms! Total trie keys: {len(loaded_trie):,}")

if __name__ == "__main__":
    build_marisa_trie()
