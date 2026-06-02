#!/bin/bash
set -euo pipefail
PGPASSWORD=localdev psql -h localhost -p 5432 -U agentops -d kestral <<'SQL'
BEGIN;

INSERT INTO users (id, full_name, email, phone, language_pref, segment, created_at)
SELECT gen_random_uuid(), 'Athithya', 'athithya651@gmail.com', '+919876543210', 'en', 'premium', NOW()
WHERE NOT EXISTS (SELECT 1 FROM users WHERE email = 'athithya651@gmail.com');

-- Order 1: Samsung phone
INSERT INTO orders (id, user_id, product_id, status, quantity, amount, discount_amount, shipping_amount, cod_fee, payment_method, shipping_address, pincode, city, order_date, delivery_date, promised_delivery_date, is_delayed, delivery_attempts, tracking_number, notes)
SELECT gen_random_uuid(), u.id, p.id, 'delivered', 1, p.price, 0.00, 0.00, 0.00, 'upi', '{"full_name":"Athithya","address_line1":"123 Main St","city":"Chennai","state":"Tamil Nadu","pincode":"600001","phone":"+919876543210"}', '600001', 'Chennai', '2026-05-20 10:00:00+00', '2026-05-25 14:00:00+00', '2026-05-24 18:00:00+00', true, 1, 'KST-BLR-9901', 'Delayed delivery'
FROM users u, products p WHERE u.email = 'athithya651@gmail.com' AND p.name = 'Samsung Galaxy S25 Ultra 5G' AND NOT EXISTS (SELECT 1 FROM orders WHERE tracking_number = 'KST-BLR-9901');

INSERT INTO billing (id, order_id, user_id, transaction_type, amount, status, refund_eligible, payment_gateway, gateway_transaction_id, transaction_date, completed_date)
SELECT gen_random_uuid(), o.id, o.user_id, 'payment', o.amount, 'completed', true, 'razorpay', 'TXN-' || o.tracking_number, o.order_date, o.delivery_date
FROM orders o WHERE o.tracking_number = 'KST-BLR-9901' AND NOT EXISTS (SELECT 1 FROM billing WHERE order_id = o.id AND transaction_type = 'payment');

-- Order 2: Sony headphones
INSERT INTO orders (id, user_id, product_id, status, quantity, amount, discount_amount, shipping_amount, cod_fee, payment_method, shipping_address, pincode, city, order_date, delivery_date, promised_delivery_date, is_delayed, delivery_attempts, tracking_number, notes)
SELECT gen_random_uuid(), u.id, p.id, 'shipped', 1, p.price, 0.00, 0.00, 0.00, 'upi', '{"full_name":"Athithya","address_line1":"123 Main St","city":"Chennai","state":"Tamil Nadu","pincode":"600001","phone":"+919876543210"}', '600001', 'Chennai', '2026-05-28 10:00:00+00', NULL, '2026-05-30 18:00:00+00', true, 0, 'KST-BLR-9902', 'Order delayed.'
FROM users u, products p WHERE u.email = 'athithya651@gmail.com' AND p.name = 'Sony WH-1000XM6 Wireless Headphones' AND NOT EXISTS (SELECT 1 FROM orders WHERE tracking_number = 'KST-BLR-9902');

INSERT INTO billing (id, order_id, user_id, transaction_type, amount, status, refund_eligible, payment_gateway, gateway_transaction_id, transaction_date, completed_date)
SELECT gen_random_uuid(), o.id, o.user_id, 'payment', o.amount, 'completed', true, 'razorpay', 'TXN-' || o.tracking_number, o.order_date, o.order_date
FROM orders o WHERE o.tracking_number = 'KST-BLR-9902' AND NOT EXISTS (SELECT 1 FROM billing WHERE order_id = o.id AND transaction_type = 'payment');

-- Order 3: Nike shoes (outside return window)
INSERT INTO orders (id, user_id, product_id, status, quantity, amount, discount_amount, shipping_amount, cod_fee, payment_method, shipping_address, pincode, city, order_date, delivery_date, promised_delivery_date, is_delayed, delivery_attempts, tracking_number, notes)
SELECT gen_random_uuid(), u.id, p.id, 'delivered', 1, p.price, 0.00, 0.00, 0.00, 'upi', '{"full_name":"Athithya","address_line1":"123 Main St","city":"Chennai","state":"Tamil Nadu","pincode":"600001","phone":"+919876543210"}', '600001', 'Chennai', '2026-04-01 10:00:00+00', '2026-04-05 14:00:00+00', '2026-04-04 18:00:00+00', false, 1, 'KST-BLR-9903', 'Return window expired.'
FROM users u, products p WHERE u.email = 'athithya651@gmail.com' AND p.name = 'Nike Air Zoom Running Shoes' AND NOT EXISTS (SELECT 1 FROM orders WHERE tracking_number = 'KST-BLR-9903');

INSERT INTO billing (id, order_id, user_id, transaction_type, amount, status, refund_eligible, payment_gateway, gateway_transaction_id, transaction_date, completed_date)
SELECT gen_random_uuid(), o.id, o.user_id, 'payment', o.amount, 'completed', true, 'razorpay', 'TXN-' || o.tracking_number, o.order_date, o.delivery_date
FROM orders o WHERE o.tracking_number = 'KST-BLR-9903' AND NOT EXISTS (SELECT 1 FROM billing WHERE order_id = o.id AND transaction_type = 'payment');

COMMIT;
SQL
echo "Seed data complete."