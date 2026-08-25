import csv
import io
import os
import sys
import time
from dotenv import load_dotenv
from openai import OpenAI

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(r"c:\Users\test\Desktop\projects\apollo_extension\.env")

INPUT_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "novel_titles_needs_llm_1000.csv"
)

OUTPUT_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "novel_titles_classified_output.csv"
)

MODEL_NAME = os.getenv("OPENAI_DOMAIN_MODEL", "gpt-4o-mini")

BLOCKER_KEYWORDS = ("compliance", "legal", "regulatory", "procurement", "privacy", "gdpr", "grc", "trade", "ethics", "audit")

# CAVEMAN + PONYTAIL COMPACT SYSTEM PROMPT (Zero fluff, maximum token density)
SYSTEM_PROMPT = """Classify 50 B2B job titles into enterprise sales segments.

SEGMENTS:
Req (r=1): A1_Signer (C-suite/board), A2_Budget_Holder (VP/SVP/EVP), A3_Approver (Director), B1_Champion / B1_Champion_Technical (Manager/Lead/Architect), B2_Champion_Commercial (Commercial/Sales/RevOps Lead), B3_Technical_Evaluator (Sr Engineer/Dev), B4_Process_Owner (PMO/Prog Mgr), C1_User (Analyst/IC/Specialist), D1_Door_Opener (CoS/EA), D2_Regional_Leader (Regional/Country Dir).
NotReq (r=0): X1_Procurement, X2_Security_Privacy, X3_Compliance_Quality, C2_Entry (Intern/Junior/Assoc), A0_Board, X_Blocker.

CONFIDENCE & ROUTING:
- c=H (High: clear seniority + single segment) -> a=Auto-Accept
- c=M (Medium: clear seniority, generic func) -> a=Review-Queue
- c=L (Low: fringe/no seniority) -> a=Unclassified-Exclude
- Blocker/compliance function word without exact rule -> a=Hard-Stop-Manual-Review, r=0, c=L

OUTPUT FORMAT:
Output plain CSV text without markdown fences, one line per title:
index,segment,is_required(1|0),confidence(H|M|L),routing_action"""


def classify_batch_with_llm(client: OpenAI, titles_batch: list[str], model: str = MODEL_NAME) -> tuple[dict, dict]:
    """
    Call OpenAI with compact numbered list and raw CSV response format.
    """
    user_prompt = "Classify these 50 titles:\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles_batch))

    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        max_tokens=1500,
        temperature=0.0
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    usage = response.usage
    tokens_info = {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "latency_ms": round(latency_ms, 1)
    }

    raw_text = response.choices[0].message.content or ""
    # Strip optional markdown fences if model outputs ```csv
    raw_text = raw_text.replace("```csv", "").replace("```", "").strip()

    parsed_results = {}
    csv_reader = csv.reader(io.StringIO(raw_text))
    for row in csv_reader:
        if not row or len(row) < 4:
            continue
        try:
            idx_str = row[0].strip().rstrip(".")
            if not idx_str.isdigit():
                continue
            idx = int(idx_str)
            seg = row[1].strip()
            r_val = int(row[2].strip()) if row[2].strip().isdigit() else 0
            conf_val = row[3].strip().upper()
            action_val = row[4].strip() if len(row) > 4 else ("Auto-Accept" if conf_val == "H" else "Review-Queue")
            parsed_results[idx] = {
                "s": seg,
                "r": r_val,
                "c": conf_val,
                "a": action_val
            }
        except Exception:
            continue

    return parsed_results, tokens_info


def process_novel_titles(limit_batches: int = 1, batch_size: int = 50, model: str = MODEL_NAME):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not found in .env")
        return

    client = OpenAI(api_key=api_key)

    if not os.path.exists(INPUT_CSV):
        print(f"Error: Input file not found: {INPUT_CSV}")
        return

    all_titles = []
    with open(INPUT_CSV, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("title") or "").strip()
            if t:
                all_titles.append(t)

    print("=" * 80)
    print(f">>> [CAVEMAN + PONYTAIL LLM CLASSIFIER] Model: {model} | Batch Size: {batch_size}")
    print(f"    Source: {INPUT_CSV} (Total Available: {len(all_titles)} titles)")
    print(f"    Batches to process: {limit_batches} ({limit_batches * batch_size} titles total)")
    print("=" * 80)

    total_stats = {
        "processed": 0,
        "required": 0,
        "not_required": 0,
        "high_confidence": 0,
        "medium_confidence": 0,
        "low_confidence": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    all_classified_results = []

    for b_idx in range(limit_batches):
        start_idx = b_idx * batch_size
        end_idx = min(start_idx + batch_size, len(all_titles))
        current_batch = all_titles[start_idx:end_idx]

        if not current_batch:
            break

        print(f"\n[BATCH #{b_idx+1}] Processing {len(current_batch)} titles (Items {start_idx+1} to {end_idx})...")
        parsed_map, tokens_info = classify_batch_with_llm(client, current_batch, model=model)

        total_stats["prompt_tokens"] += tokens_info["prompt_tokens"]
        total_stats["completion_tokens"] += tokens_info["completion_tokens"]
        total_stats["total_tokens"] += tokens_info["total_tokens"]

        print(f"   |-- Tokens: {tokens_info['total_tokens']} (Prompt: {tokens_info['prompt_tokens']}, Comp: {tokens_info['completion_tokens']}) | Latency: {tokens_info['latency_ms']}ms")

        for idx, raw_title in enumerate(current_batch, 1):
            norm_key = raw_title.strip().lower()
            item = parsed_map.get(idx, {})

            seg = item.get("s", "Unknown")
            is_req = bool(item.get("r", 0))
            c_val = str(item.get("c", "M")).upper()
            conf = "High" if c_val == "H" else ("Medium" if c_val == "M" else "Low")
            route = item.get("a", "Review-Queue")
            reason = f"Classified as {seg}"

            # Hard stop condition for blocker functions
            has_blocker_word = any(w in norm_key for w in BLOCKER_KEYWORDS)
            if has_blocker_word and not seg.startswith("X"):
                route = "Hard-Stop-Manual-Review"
                conf = "Low"
                is_req = False
                reason = "Hard Stop: Blocker/compliance function detected without explicit rule."

            if len(raw_title.split()) < 3 and conf == "Low":
                route = "Hard-Stop-Manual-Review"
                reason = "Hard Stop: Under 3 words with insufficient seniority signal."

            total_stats["processed"] += 1
            if is_req:
                total_stats["required"] += 1
            else:
                total_stats["not_required"] += 1

            if conf == "High":
                total_stats["high_confidence"] += 1
            elif conf == "Medium":
                total_stats["medium_confidence"] += 1
            else:
                total_stats["low_confidence"] += 1

            rec = {
                "title": raw_title,
                "segment": seg,
                "is_required": is_req,
                "confidence": conf,
                "routing_action": route,
                "reason": reason
            }
            all_classified_results.append(rec)

            status_icon = "🟢 REQUIRED" if is_req else "⚪ NOT REQUIRED"
            print(f"   [{status_icon}] {raw_title:<45} | Seg: {seg:<22} | Conf: {conf:<6} | {route}")

    # Write output CSV
    print(f"\nWriting {len(all_classified_results)} classified titles to: {OUTPUT_CSV}")
    with open(OUTPUT_CSV, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "segment", "is_required", "confidence", "routing_action", "reason"])
        writer.writeheader()
        for r in all_classified_results:
            writer.writerow({
                "title": r["title"],
                "segment": r["segment"],
                "is_required": "Yes" if r["is_required"] else "No",
                "confidence": r["confidence"],
                "routing_action": r["routing_action"],
                "reason": r["reason"]
            })

    # Token Consumption Metrics
    cost = (total_stats["prompt_tokens"] * 0.00000015) + (total_stats["completion_tokens"] * 0.00000060)

    print("\n" + "=" * 80)
    print(">>> TOKEN CONSUMPTION & EFFICIENCY REPORT (Ponytail + Caveman)")
    print("=" * 80)
    print(f"Total Titles Processed : {total_stats['processed']}")
    print(f"  ├── 🟢 Required Leads : {total_stats['required']} ({total_stats['required']/max(1, total_stats['processed'])*100:.1f}%)")
    print(f"  └── ⚪ Not Required   : {total_stats['not_required']} ({total_stats['not_required']/max(1, total_stats['processed'])*100:.1f}%)")
    print("\nConfidence Breakdown:")
    print(f"  ├── High Confidence   : {total_stats['high_confidence']} (Auto-Accept)")
    print(f"  ├── Medium Confidence : {total_stats['medium_confidence']} (Review Queue)")
    print(f"  └── Low Confidence    : {total_stats['low_confidence']} (Exclude / Hard Stop)")
    print("\nToken Consumption Metrics:")
    print(f"  ├── Prompt Tokens     : {total_stats['prompt_tokens']} tokens")
    print(f"  ├── Completion Tokens : {total_stats['completion_tokens']} tokens")
    print(f"  ├── Total Tokens      : {total_stats['total_tokens']} tokens")
    print(f"  ├── Tokens / Title    : {total_stats['total_tokens'] / max(1, total_stats['processed']):.1f} tokens/title")
    print(f"  └── Total Cost        : ${cost:.6f} USD")
    print("=" * 80)


if __name__ == "__main__":
    batches = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    process_novel_titles(limit_batches=batches, batch_size=50)
