-- Seed data. Entirely fictional carrier ("Meridian Mutual"), fictional people,
-- fictional policy and claim numbers. No real customer data, no credentials.
--
-- Caller profiles drive the distinct scenario shapes:
--   +14155550201 Dana    — active policy, open claim in inspection  -> claim status
--   +14155550202 Marcus  — active policy, balance due               -> payment link
--   +14155550203 Priya   — lapsed policy                            -> refuse + transfer
--   +14155550204 Elena   — active policy, no open claim             -> FNOL intake
--   +14155550205 Tobias  — active policy, denied claim              -> transfer (dispute)
--   anything else        — unrecognised number -> verify or transfer

INSERT INTO coverage_catalog (coverage_type, display_name, description) VALUES
  ('collision',     'Collision',              'Damage to your car from hitting another vehicle or object'),
  ('comprehensive', 'Comprehensive',          'Theft, weather, fire, vandalism and animal strikes'),
  ('glass',         'Glass',                  'Windshield and window repair or replacement'),
  ('rental',        'Rental Reimbursement',   'A rental car while your vehicle is being repaired'),
  ('roadside',      'Roadside Assistance',    'Towing, jump starts, lockouts and flat tyres'),
  ('liability',     'Bodily Injury Liability','Injury or property damage you cause to others');

INSERT INTO policyholders (policyholder_id, phone, first_name, last_name, date_of_birth, zip_code, email) VALUES
  ('ph_dana',   '+14155550201', 'Dana',   'Whitfield', '1986-04-12', '94110', 'dana@example.com'),
  ('ph_marcus', '+14155550202', 'Marcus', 'Ortega',    '1979-11-03', '94612', 'marcus@example.com'),
  ('ph_priya',  '+14155550203', 'Priya',  'Raman',     '1991-07-27', '95112', 'priya@example.com'),
  ('ph_elena',  '+14155550204', 'Elena',  'Fischer',   '1994-02-19', '94301', 'elena@example.com'),
  ('ph_tobias', '+14155550205', 'Tobias', 'Lindqvist', '1972-09-08', '94805', 'tobias@example.com');

INSERT INTO policies (policy_id, policyholder_id, policy_number, status, effective_date, renewal_date, premium_monthly, balance_due, payment_due_date, state_code) VALUES
  ('pol_dana',   'ph_dana',   'MM-4471902', 'active', '2026-01-15', '2027-01-15', 142.50,   0.00, NULL,         'CA'),
  ('pol_marcus', 'ph_marcus', 'MM-5590318', 'active', '2025-09-01', '2026-09-01', 168.75, 168.75, '2026-09-05', 'CA'),
  ('pol_priya',  'ph_priya',  'MM-6612447', 'lapsed', '2025-03-01', '2026-03-01', 121.00, 242.00, '2026-04-01', 'CA'),
  ('pol_elena',  'ph_elena',  'MM-7734125', 'active', '2026-05-20', '2027-05-20', 155.20,   0.00, NULL,         'CA'),
  ('pol_tobias', 'ph_tobias', 'MM-8846071', 'active', '2025-12-10', '2026-12-10', 198.40,   0.00, NULL,         'CA');

INSERT INTO vehicles (vehicle_id, policy_id, year, make, model, vin_last4) VALUES
  ('veh_dana_1',   'pol_dana',   2021, 'Toyota',    'RAV4',     '8842'),
  ('veh_dana_2',   'pol_dana',   2016, 'Honda',     'Civic',    '3317'),
  ('veh_marcus_1', 'pol_marcus', 2019, 'Ford',      'F-150',    '9021'),
  ('veh_priya_1',  'pol_priya',  2020, 'Hyundai',   'Elantra',  '4456'),
  ('veh_elena_1',  'pol_elena',  2023, 'Subaru',    'Outback',  '7719'),
  ('veh_tobias_1', 'pol_tobias', 2018, 'Volvo',     'XC60',     '2204');

INSERT INTO drivers (driver_id, policy_id, full_name, is_primary) VALUES
  ('drv_dana_1',   'pol_dana',   'Dana Whitfield',   TRUE),
  ('drv_dana_2',   'pol_dana',   'Sam Whitfield',    FALSE),
  ('drv_marcus_1', 'pol_marcus', 'Marcus Ortega',    TRUE),
  ('drv_priya_1',  'pol_priya',  'Priya Raman',      TRUE),
  ('drv_elena_1',  'pol_elena',  'Elena Fischer',    TRUE),
  ('drv_tobias_1', 'pol_tobias', 'Tobias Lindqvist', TRUE);

-- Coverage varies per policy so "is this covered?" has real, different answers.
INSERT INTO coverages (coverage_id, policy_id, coverage_type, is_included, deductible, limit_amount) VALUES
  ('cov_dana_col',   'pol_dana',   'collision',     TRUE,   500.00,  50000.00),
  ('cov_dana_com',   'pol_dana',   'comprehensive', TRUE,   250.00,  50000.00),
  ('cov_dana_gls',   'pol_dana',   'glass',         TRUE,     0.00,   1500.00),
  ('cov_dana_rnt',   'pol_dana',   'rental',        TRUE,     0.00,    900.00),
  ('cov_dana_rds',   'pol_dana',   'roadside',      TRUE,     0.00,    150.00),
  ('cov_dana_lia',   'pol_dana',   'liability',     TRUE,     0.00, 100000.00),

  ('cov_marcus_col', 'pol_marcus', 'collision',     TRUE,  1000.00,  40000.00),
  ('cov_marcus_com', 'pol_marcus', 'comprehensive', TRUE,   500.00,  40000.00),
  ('cov_marcus_gls', 'pol_marcus', 'glass',         FALSE,  NULL,       NULL),
  ('cov_marcus_rnt', 'pol_marcus', 'rental',        FALSE,  NULL,       NULL),
  ('cov_marcus_rds', 'pol_marcus', 'roadside',      TRUE,     0.00,    100.00),
  ('cov_marcus_lia', 'pol_marcus', 'liability',     TRUE,     0.00,  50000.00),

  ('cov_priya_col',  'pol_priya',  'collision',     TRUE,   500.00,  30000.00),
  ('cov_priya_lia',  'pol_priya',  'liability',     TRUE,     0.00,  30000.00),

  ('cov_elena_col',  'pol_elena',  'collision',     TRUE,   250.00,  60000.00),
  ('cov_elena_com',  'pol_elena',  'comprehensive', TRUE,   250.00,  60000.00),
  ('cov_elena_gls',  'pol_elena',  'glass',         TRUE,     0.00,   1500.00),
  ('cov_elena_rnt',  'pol_elena',  'rental',        TRUE,     0.00,   1200.00),
  ('cov_elena_rds',  'pol_elena',  'roadside',      TRUE,     0.00,    150.00),
  ('cov_elena_lia',  'pol_elena',  'liability',     TRUE,     0.00, 250000.00),

  ('cov_tobias_col', 'pol_tobias', 'collision',     TRUE,   750.00,  55000.00),
  ('cov_tobias_com', 'pol_tobias', 'comprehensive', TRUE,   750.00,  55000.00),
  ('cov_tobias_rds', 'pol_tobias', 'roadside',      TRUE,     0.00,    150.00),
  ('cov_tobias_lia', 'pol_tobias', 'liability',     TRUE,     0.00, 100000.00);

INSERT INTO claims (claim_id, claim_ref, policy_id, loss_type, loss_date, loss_location, description, other_party, status, adjuster_name, adjuster_phone, settlement_amount, deductible_applied) VALUES
  ('clm_dana_1',   'CLM-88213', 'pol_dana',   'collision',     '2026-08-14', 'Cesar Chavez Street, San Francisco',
     'Rear-ended at a stop light', 'Blue Mazda 3, driver exchanged details', 'inspection_scheduled',
     'Ruth Alvarez', '+18005550111', NULL, 500.00),
  ('clm_tobias_1', 'CLM-90554', 'pol_tobias', 'comprehensive', '2026-07-02', 'Home driveway, Richmond',
     'Hail damage to roof and bonnet', NULL, 'denied',
     'Peter Nkemelu', '+18005550112', 0.00, 750.00),
  ('clm_dana_2',   'CLM-87001', 'pol_dana',   'glass',         '2026-06-21', 'Interstate 80 near Berkeley',
     'Stone chip spread across the windscreen', NULL, 'paid',
     'Ruth Alvarez', '+18005550111', 620.00, 0.00);

INSERT INTO claim_events (event_id, claim_id, note) VALUES
  ('evt_1', 'clm_dana_1',   'Claim submitted by phone'),
  ('evt_2', 'clm_dana_1',   'Assigned to adjuster Ruth Alvarez'),
  ('evt_3', 'clm_dana_1',   'Inspection scheduled for 2026-09-03 at Bayview Auto Body'),
  ('evt_4', 'clm_tobias_1', 'Claim submitted online'),
  ('evt_5', 'clm_tobias_1', 'Denied: damage predates the policy effective date'),
  ('evt_6', 'clm_dana_2',   'Payment issued to Bayview Glass');

INSERT INTO payments (payment_id, policy_id, amount, method, status) VALUES
  ('pay_dana_1',   'pol_dana',   142.50, 'autopay', 'posted'),
  ('pay_elena_1',  'pol_elena',  155.20, 'autopay', 'posted'),
  ('pay_tobias_1', 'pol_tobias', 198.40, 'autopay', 'posted');
