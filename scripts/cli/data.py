"""CLI event and scenario data: simple bookkeeping, engine scenarios, subledger."""

from decimal import Decimal

# Simple bookkeeping: (description, debit_role, credit_role, amount, dr_label, cr_label)
SIMPLE_EVENTS = [
    ("Owner investment", "CASH", "RETAINED_EARNINGS", Decimal("500000.00"), "Cash", "Retained Earnings"),
    ("Inventory purchase (on acct)", "INVENTORY", "ACCOUNTS_PAYABLE", Decimal("100000.00"), "Inventory", "Accounts Payable"),
    ("Cash sale", "CASH", "REVENUE", Decimal("150000.00"), "Cash", "Sales Revenue"),
    ("Credit sale (on account)", "ACCOUNTS_RECEIVABLE", "REVENUE", Decimal("75000.00"), "Accounts Receivable", "Sales Revenue"),
    ("Cost of goods sold", "COGS", "INVENTORY", Decimal("60000.00"), "COGS", "Inventory"),
    ("Pay salaries", "SALARY_EXPENSE", "CASH", Decimal("45000.00"), "Salary Expense", "Cash"),
    ("Collect receivable", "CASH", "ACCOUNTS_RECEIVABLE", Decimal("25000.00"), "Cash", "Accounts Receivable"),
    ("Buy equipment", "FIXED_ASSET", "CASH", Decimal("80000.00"), "Equipment", "Cash"),
    ("Pay accounts payable", "ACCOUNTS_PAYABLE", "CASH", Decimal("30000.00"), "Accounts Payable", "Cash"),
    ("Record depreciation", "DEPRECIATION_EXPENSE", "ACCUMULATED_DEPRECIATION", Decimal("10000.00"), "Depreciation", "Accum. Depreciation"),
]

ENGINE_SCENARIOS = [
    {"id": "V1", "label": "Inventory Receipt with PPV", "engine": "variance", "event_type": "inventory.receipt", "amount": Decimal("10500.00"),
     "payload": {"quantity": 1000, "has_variance": True, "standard_price": "10.00", "actual_price": "10.50", "standard_total": "10000.00", "variance_amount": "500.00", "variance_type": "price", "expected_price": "10.00"},
     "business": "PO $10/unit, invoice $10.50/unit, 1000u -> $500 PPV"},
    {"id": "V2", "label": "WIP Labor Efficiency Variance", "engine": "variance", "event_type": "wip.labor_variance", "amount": Decimal("450.00"),
     "payload": {"quantity": 160, "standard_hours": 150, "actual_hours": 160, "standard_rate": "45.00", "actual_rate": "45.00", "variance_type": "quantity", "expected_quantity": "150", "actual_quantity": "160", "standard_price": "45.00"},
     "business": "Std 150hrs, actual 160hrs at $45/hr -> $450 unfavorable"},
    {"id": "V3", "label": "WIP Material Usage Variance", "engine": "variance", "event_type": "wip.material_variance", "amount": Decimal("3750.00"),
     "payload": {"quantity": 1150, "standard_quantity": 1000, "actual_quantity": 1150, "standard_price": "25.00", "variance_type": "quantity", "expected_quantity": "1000"},
     "business": "Std 1000u, used 1150 at $25 -> $3,750 unfavorable"},
    {"id": "V4", "label": "WIP Overhead Variance", "engine": "variance", "event_type": "wip.overhead_variance", "amount": Decimal("4500.00"),
     "payload": {"quantity": 1, "applied_overhead": "67500.00", "actual_overhead": "72000.00", "variance_type": "standard_cost", "standard_cost": "67500.00", "actual_cost": "72000.00"},
     "business": "Applied $67,500, actual $72,000 -> $4,500 under-applied"},
    {"id": "T1", "label": "Use Tax Self-Assessment (CA 8%)", "engine": "tax", "event_type": "tax.use_tax_accrued", "amount": Decimal("3200.00"),
     "payload": {"amount": "3200.00", "jurisdiction": "CA", "purchase_amount": "40000.00", "use_tax_rate": "0.08"},
     "business": "$40K equipment from out-of-state, CA use tax 8%"},
    {"id": "M1", "label": "AP Invoice PO-Matched (Three-Way)", "engine": "matching", "event_type": "ap.invoice_received", "amount": Decimal("49500.00"),
     "payload": {"po_number": "PO-2026-0042", "gross_amount": "49500.00", "vendor_id": "V-100", "po_amount": "50000.00", "receipt_amount": "49000.00", "receipt_quantity": 490, "po_quantity": 500, "invoice_quantity": 495, "match_operation": "create_match", "match_type": "three_way",
      "match_documents": [{"document_id": "PO-2026-0042", "document_type": "purchase_order", "amount": "50000.00", "quantity": 500}, {"document_id": "RCV-2026-0088", "document_type": "receipt", "amount": "49000.00", "quantity": 490}, {"document_id": "INV-2026-1234", "document_type": "invoice", "amount": "49500.00", "quantity": 495}]},
     "business": "PO $50K/500u, Receipt 490u/$49K, Invoice $49,500/495u"},
    {"id": "B1", "label": "Govt Contract Billing CPFF", "engine": "billing", "event_type": "contract.billing_provisional", "amount": Decimal("285000.00"),
     "payload": {"billing_type": "COST_REIMBURSEMENT", "total_billing": "285000.00", "cost_billing": "263889.00", "fee_amount": "21111.00", "contract_number": "FA8750-21-C-0001", "contract_type": "CPFF", "fee_rate": "0.08",
      "billing_input": {"contract_type": "CPFF", "cost_breakdown": {"direct_labor": "150000.00", "direct_material": "50000.00", "subcontract": "0.00", "travel": "5000.00", "odc": "2000.00"}, "indirect_rates": {"fringe": "0.35", "overhead": "0.45", "ga": "0.10"}, "fee_rate": "0.08", "currency": "USD"}},
     "business": "CPFF: $263,889 costs + $21,111 fee (8%) = $285,000"},
]

NON_ENGINE_SCENARIOS = [
    {"id": "N1", "label": "Standard Inventory Receipt", "event_type": "inventory.receipt", "amount": Decimal("25000.00"), "payload": {"quantity": 500, "has_variance": False}, "business": "500 units at $50/unit, standard cost receipt"},
    {"id": "N2", "label": "Inventory Issue to Production", "event_type": "inventory.issue", "amount": Decimal("10000.00"), "payload": {"issue_type": "PRODUCTION", "quantity": 200}, "business": "200 units raw material issued to production WIP"},
    {"id": "N3", "label": "Inventory Issue for Sale (COGS)", "event_type": "inventory.issue", "amount": Decimal("15000.00"), "payload": {"issue_type": "SALE", "quantity": 300}, "business": "300 units shipped for sale, COGS recognized"},
    {"id": "N4", "label": "AP Direct Expense Invoice", "event_type": "ap.invoice_received", "amount": Decimal("8500.00"), "payload": {"po_number": None, "gross_amount": "8500.00", "vendor_id": "V-200"}, "business": "Direct expense invoice for consulting services"},
    {"id": "N5", "label": "Payroll Accrual", "event_type": "payroll.accrual", "amount": Decimal("125000.00"), "payload": {"gross_amount": "125000.00", "federal_tax_amount": "25000.00", "state_tax_amount": "8750.00", "fica_amount": "9562.50", "benefits_amount": "6250.00", "net_pay_amount": "75437.50"}, "business": "Monthly payroll: $125K gross, $43.3K withholdings, $75.4K net"},
    {"id": "WF1", "label": "AR Invoice (workflow trace)", "event_type": "ar.invoice", "amount": Decimal("5000.00"), "use_workflow_path": True, "payload": {}, "business": "Customer invoice — use T to see workflow + interpretation in trace"},
]

ALL_PIPELINE_SCENARIOS = ENGINE_SCENARIOS + NON_ENGINE_SCENARIOS

SUBLEDGER_SCENARIOS = [
    {"id": "SL1", "label": "AP Invoice — Vendor V-100", "gl_debit": "EXPENSE", "gl_credit": "ACCOUNTS_PAYABLE", "sl_type": "AP", "entity_id": "V-100", "doc_type": "INVOICE", "amount": Decimal("15000.00"), "memo": "AP Invoice from Vendor V-100"},
    {"id": "SL2", "label": "AP Payment — Vendor V-100", "gl_debit": "ACCOUNTS_PAYABLE", "gl_credit": "CASH", "sl_type": "AP", "entity_id": "V-100", "doc_type": "PAYMENT", "amount": Decimal("15000.00"), "memo": "AP Payment to Vendor V-100"},
    {"id": "SL3", "label": "AR Invoice — Customer C-200", "gl_debit": "ACCOUNTS_RECEIVABLE", "gl_credit": "REVENUE", "sl_type": "AR", "entity_id": "C-200", "doc_type": "INVOICE", "amount": Decimal("25000.00"), "memo": "AR Invoice to Customer C-200"},
    {"id": "SL4", "label": "AR Payment — Customer C-200", "gl_debit": "CASH", "gl_credit": "ACCOUNTS_RECEIVABLE", "sl_type": "AR", "entity_id": "C-200", "doc_type": "PAYMENT", "amount": Decimal("25000.00"), "memo": "AR Payment from Customer C-200"},
    {"id": "SL5", "label": "Inventory Receipt — SKU-A", "gl_debit": "INVENTORY", "gl_credit": "ACCOUNTS_PAYABLE", "sl_type": "INVENTORY", "entity_id": "SKU-A", "doc_type": "RECEIPT", "amount": Decimal("8000.00"), "memo": "Inventory receipt 400u @ $20"},
    {"id": "SL6", "label": "Inventory Issue (COGS) — SKU-A", "gl_debit": "COGS", "gl_credit": "INVENTORY", "sl_type": "INVENTORY", "entity_id": "SKU-A", "doc_type": "ISSUE", "amount": Decimal("3000.00"), "memo": "Issue 150u @ $20 for sale"},
    {"id": "SL7", "label": "Bank Deposit", "gl_debit": "CASH", "gl_credit": "REVENUE", "sl_type": "BANK", "entity_id": "ACCT-001", "doc_type": "DEPOSIT", "amount": Decimal("50000.00"), "memo": "Bank deposit from daily sales"},
]
