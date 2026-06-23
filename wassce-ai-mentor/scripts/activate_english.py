import json
from pathlib import Path

corpus_path = Path("data/corpus/wassce_qa.json")
data = json.loads(corpus_path.read_text(encoding="utf-8"))

# Count before
before_english_validated = sum(1 for e in data if e["subject"] == "english" and e["validated"])
before_english_total = sum(1 for e in data if e["subject"] == "english")

# Flip all English entries to validated:true
flipped = 0
for entry in data:
    if entry["subject"] == "english" and entry["validated"] is False:
        entry["validated"] = True
        flipped += 1

# Save back
corpus_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# Count after
after_english_validated = sum(1 for e in data if e["subject"] == "english" and e["validated"])

print(f"BEFORE: english validated={before_english_validated}/{before_english_total}")
print(f"FLIPPED: {flipped} entries")
print(f"AFTER:  english validated={after_english_validated}/{before_english_total}")
print(f"Total corpus size: {len(data)}")
