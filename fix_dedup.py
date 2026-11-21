with open('app/routers/store_purchase.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find all blocks containing "Deduplication guard"
guard_positions = []
for i, line in enumerate(lines):
    if 'Deduplication guard' in line:
        guard_positions.append(i)

print(f"Found 'Deduplication guard' at lines: {[p+1 for p in guard_positions]}")

# For each block, find its extent
def find_block_end(lines, start):
    """Find the end of dedup block starting at 'start'"""
    for j in range(start+1, min(start+30, len(lines))):
        if 'db.delete(orphan)' in lines[j]:
            # End is next two lines (closing comment + blank)
            return j + 2
    return start + 25

# Remove blocks at index 1 and 2 (update_item and issue_item), keep index 0
blocks_to_remove = guard_positions[1:]  # skip first (the correct one)
print(f"Will remove blocks at lines: {[p+1 for p in blocks_to_remove]}")

# Remove from last to first to preserve line numbers
for start in reversed(blocks_to_remove):
    end = find_block_end(lines, start)
    print(f"Removing lines {start+1} to {end+1}")
    for j in range(start, min(end+1, len(lines))):
        print(f"  DELETE: {lines[j][:80]!r}")
    del lines[start:end+1]

with open('app/routers/store_purchase.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Done!")
