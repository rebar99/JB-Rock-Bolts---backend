-- ============================================================
-- STEP 1: Cutting Oil Solcut ka item_id dhundho
-- ============================================================
SELECT id, name, stock_item, quantity, total_received_quantity,
       previous_quantity, current_month_quantity, rate
FROM store_purchase_items
WHERE stock_item LIKE '%Solcut%' OR stock_item LIKE '%cutting oil%'
   OR name LIKE '%Solcut%' OR name LIKE '%cutting oil%';

-- ============================================================
-- STEP 2: Us item ke saare transactions dekho
-- (upar se milne wala ID neeche 999 ki jagah daalo)
-- ============================================================
SELECT id, transaction_type, quantity, rate, amount,
       transaction_date, vendor_name, receipt_line_id
FROM store_purchase_stock_transactions
WHERE item_id = 999   -- <-- CHANGE THIS to actual item_id from Step 1
ORDER BY transaction_date;

-- ============================================================
-- STEP 3: Saare items me duplicate check karo
-- ============================================================
SELECT
  spi.id AS item_id,
  spi.name AS group_name,
  spi.stock_item,
  SUM(CASE WHEN t.receipt_line_id IS NULL THEN 1 ELSE 0 END) AS manual_count,
  SUM(CASE WHEN t.receipt_line_id IS NOT NULL THEN 1 ELSE 0 END) AS mr_count,
  SUM(CASE WHEN t.receipt_line_id IS NULL THEN t.quantity ELSE 0 END) AS manual_qty,
  SUM(CASE WHEN t.receipt_line_id IS NOT NULL THEN t.quantity ELSE 0 END) AS mr_qty
FROM store_purchase_stock_transactions t
JOIN store_purchase_items spi ON spi.id = t.item_id
WHERE t.transaction_type = 'RECEIPT'
GROUP BY t.item_id, spi.name, spi.stock_item
HAVING manual_count > 0 AND mr_count > 0
ORDER BY t.item_id;

-- ============================================================
-- STEP 4: Cutting Oil Solcut - Orphan (manual) TX delete karo
-- (Step 2 se confirm karo pehle — receipt_line_id IS NULL wala)
-- ============================================================
-- PREVIEW (run this first):
SELECT id, quantity, vendor_name, transaction_date, receipt_line_id
FROM store_purchase_stock_transactions
WHERE item_id = 999   -- <-- CHANGE THIS
  AND transaction_type = 'RECEIPT'
  AND receipt_line_id IS NULL;

-- DELETE (sirf preview confirm ke baad run karo):
-- DELETE FROM store_purchase_stock_transactions
-- WHERE item_id = 999   -- <-- CHANGE THIS
--   AND transaction_type = 'RECEIPT'
--   AND receipt_line_id IS NULL
--   AND quantity = 420
--   AND vendor_name LIKE '%Parbhu%';

-- ============================================================
-- STEP 5: Quantity fix karo (agar sirf 1 MR entry = 420 qty thi)
-- ============================================================
-- UPDATE store_purchase_items
-- SET total_received_quantity = 420,
--     quantity = 420,
--     previous_quantity = 420,
--     current_month_quantity = 0
-- WHERE id = 999;   -- <-- CHANGE THIS
