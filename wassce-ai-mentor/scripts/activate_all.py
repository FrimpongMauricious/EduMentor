
# activation 
import json
from pathlib import Path
from collections import Counter

corpus_path = Path("data/corpus/wassce_qa.json")
data = json.loads(corpus_path.read_text(encoding="utf-8"))

before = Counter()
for e in data:
    key = f"{e['subject']}_{'valid' if e['validated'] else 'pending'}"
    before[key] += 1

print("BEFORE:")
for k, v in sorted(before.items()):
    print(f"  {k}: {v}")

flipped = 0
for entry in data:
    if entry["validated"] is False:
        entry["validated"] = True
        flipped += 1

corpus_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"\nFLIPPED: {flipped} entries")
print(f"Total corpus: {len(data)}")
print(f"All validated: {all(e['validated'] for e in data)}")
