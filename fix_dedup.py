with open('app/routers/store_purchase.py', 'r', encoding='utf-8') as f:
    content = f.read()

marker_start = "\n# ═══════════════════════════════════════════════════════════════════════════\n# TEMPORARY ADMIN ENDPOINT"
marker_end = "    return {\n        \"status\": \"done\",\n        \"affected_items\": len(affected_item_ids),\n        \"deleted_transactions\": len(deleted_txs),\n        \"details\": deleted_txs,\n        \"qty_corrections\": qty_corrections,\n    }\n"

start_idx = content.find(marker_start)
end_idx = content.find(marker_end)

if start_idx != -1 and end_idx != -1:
    end_idx = end_idx + len(marker_end)
    new_content = content[:start_idx] + "\n"
    with open('app/routers/store_purchase.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"Removed temp endpoint (chars {start_idx} to {end_idx})")
    print(f"File now has {len(new_content)} chars")
else:
    print(f"marker_start found: {start_idx != -1}")
    print(f"marker_end found: {end_idx != -1}")
