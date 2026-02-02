# ERP Gap Analysis -- Remaining Gaps

**Date:** 2026-02-01
**Scope:** Forward-looking assessment of remaining work against world-class ERP requirements
**Benchmark:** SAP S/4HANA, Oracle Cloud Financials, Workday Financials, Deltek Costpoint (GovCon)

---

## Executive Summary

The core accounting system is **production-grade across all foundational layers.** The kernel, engines, configuration, all 19 modules, and the batch processing system are complete.

**Current State:** 7 layers, 19 modules, 13 engines, 24 invariants (R1-R24), 11 approval invariants (AL-1 through AL-11), 15 ingestion invariants (IM-1 through IM-15), 10 batch invariants (BT-1 through BT-10). ~580 Python files across source + tests.

**What remains is infrastructure and ancillary tooling:**

| Category | Priority | Description |
|----------|----------|-------------|
| REST API + Auth/RBAC | P0 | External interface layer -- the only P0 blocker |
| Audit & Compliance Reporting | P1 | Regulatory output formats, SoD reports |
| Data Export | P1 | Excel import, export framework, audit extract |
| IFRS Policy Set | P2 | Second accounting standard via YAML config |
| Document Management | P2 | Blob storage, attachment model, retention |
| Notification System | P2 | Email, webhooks, alert rules |
| User Interface | P2 | Web SPA consuming the API |

**No new engines or modules are required.** All remaining work is infrastructure, config, or utilities.

---

## System Maturity

| Layer | Package | Status |
|-------|---------|--------|
| Kernel | `finance_kernel/` | Production-grade. 94 files. EngineDispatcher, PostingOrchestrator, mandatory guards, financial exception lifecycle, reversal system, all 16 architecture gaps (G1-G16) closed. |
| Engines | `finance_engines/` | Production-grade. 13 engines (allocation, matching, aging, tax, variance, valuation, reconciliation, correction, billing, ICE, allocation cascade, subledger, **approval**). All @traced_engine, dispatched via EngineDispatcher. |
| Config | `finance_config/` | Production-grade. 10 files. YAML-driven policies, compiled to frozen artifact. Authoring guide shipped. |
| Modules | `finance_modules/` | Production-grade. 19 modules, 144 files. All deepened with service methods, models, profiles, engine integration. |
| Services | `finance_services/` | Operational. 20 files. PostingOrchestrator, WorkflowExecutor, RetryService, engine dispatch, correction, reconciliation, subledger, valuation. |
| Ingestion | `finance_ingestion/` | Production-grade. 24 files. CSV/JSON adapters, staging ORM, mapping engine, validation pipeline, promotion service, entity promoters. 103 tests. |
| Batch | `finance_batch/` | Production-grade. 20 files. BatchExecutor (SAVEPOINT-per-item), BatchScheduler, TaskRegistry, 10 module tasks, BatchOrchestrator DI container. 289 tests. 10 invariants (BT-1 through BT-10). |
| Tests | `tests/` | 250+ files across 28 categories. |

---

## Remaining Gaps

### Priority Tiers
- **P0 -- Blocking:** Cannot go to production without these
- **P1 -- Core ERP:** Expected by any customer evaluating an ERP
- **P2 -- Competitive:** Needed to compete with SAP/Oracle

---

### GAP-01: REST/GraphQL API Layer [P0] [INFRASTRUCTURE]

**Current State:** No external API. System is only accessible via Python imports and CLI scripts. This is the **single P0 blocker** for production deployment.

**Why this can't be a module:** It's an external interface layer that sits above all modules.

| Component | Description |
|-----------|-------------|
| REST API Framework | FastAPI with OpenAPI spec |
| Authentication | OAuth 2.0 / JWT token-based auth |
| Authorization (RBAC) | Role-based access control with fine-grained permissions |
| Segregation of Duties | SoD conflict matrix and enforcement |
| Rate Limiting | Per-tenant, per-endpoint throttling |
| API Versioning | v1/v2 backwards compatibility |
| Webhook Publisher | Event-driven notifications for external systems |
| Bulk Operations | Batch posting, batch import |

**Proposed architecture:**
```
finance_api/
    app.py                 # FastAPI application
    auth/                  # Authentication & authorization
        models.py          # User, Role, Permission
        rbac.py            # Permission evaluation
        sod.py             # Segregation of duties matrix
    routes/                # One file per module
    middleware/             # Logging, correlation IDs, error handling
    serializers/           # Pydantic request/response schemas
```

**Dependencies:** None. All kernel services, modules, and engines are ready to be exposed. The approval engine provides pre-action authorization. The ingestion system provides structured data import. The WorkflowExecutor provides state transition enforcement.

---

### GAP-24: IFRS Policy Set [P2] [CONFIG]

**Current State:** System is US GAAP only. IFRS capability exists architecturally (the YAML policy system was designed for multiple accounting standards) but no IFRS policies have been written.

**Why it's config only:** Different accounting standards are different policy sets applied to the same kernel. IFRS support means writing a `finance_config/sets/IFRS-2026-v1/` directory.

| Component | Description |
|-----------|-------------|
| IFRS Policy Set | Complete IFRS policies in YAML |
| IAS 21 (Foreign Currency) | Translation policies (multi-currency module already supports the mechanics) |
| IAS 36 (Impairment) | Impairment policies (assets module has `test_impairment()`) |
| IFRS 15 (Revenue) | Revenue policies (ASC 606 module provides the five-step model) |
| IFRS 16 (Leases) | Lease policies (ASC 842 module provides ROU/liability mechanics) |
| Dual-GAAP Reporting | Parallel US GAAP + IFRS posting via two config sets, same kernel |

---

### GAP-25: Data Export & Migration [P1] [UTILITY]

**Current State:** The ERP data ingestion system handles **inbound** data (CSV/JSON import with staging, validation, promotion). What's missing is **outbound** data and Excel support.

| Component | Description | Status |
|-----------|-------------|--------|
| CSV/JSON Import | Batch import with staging + validation | **Done** (finance_ingestion) |
| Bank Statement Parsing | MT940, BAI2, CAMT.053 | **Done** (cash module helpers) |
| Excel Import | .xlsx parsing for journal entries, COA, opening balances | Not started |
| Data Migration Tool | Initial load orchestration from legacy systems | Not started |
| Export Framework | Configurable data extracts for analytics | Not started |
| Audit Export | Structured export for external auditors (SAF-T, FEC) | Not started |
| Report Renderers | PDF/Excel/CSV/XBRL output from reporting module | Not started |

---

### GAP-26: Audit & Compliance Reporting [P1] [MODULE]

**Current State:** The audit chain exists (hash-chained `AuditEvent` model, `AuditorService`). What's missing is structured reporting on top of it.

| Component | Description | Existing Primitive |
|-----------|-------------|-------------------|
| SoD Matrix Report | Segregation of duties conflicts | Requires RBAC (GAP-01) |
| Access Review Report | Periodic access certification | Requires RBAC (GAP-01) |
| Change Log Report | Config and master data change history | Audit chain (exists) |
| Regulatory Filing | SAF-T, FEC, SII output formats | Queries + formatting |
| Audit Trail Export | Structured export for external auditors | Audit events (exists) |

**Note:** SoD and access review reports depend on GAP-01 (RBAC) being built first. Change log and regulatory filing can proceed independently.

---

### GAP-27: Document Management [P2] [UTILITY]

| Component | Description |
|-----------|-------------|
| Document Store | S3/blob storage integration |
| Attachment Model | Link documents to any entity (invoices, receipts, contracts) |
| Document Retention | Policy-based retention and purge |

---

### GAP-28: Notification System [P2] [UTILITY]

| Component | Description |
|-----------|-------------|
| Email Notifications | Templated emails for approvals, alerts, dunning |
| Webhook Publisher | Event-driven external notifications |
| Alert Rules | Configurable threshold-based alerts (credit limit, budget overrun, aging) |

**Note:** The approval engine already models escalation timeouts (`escalation_timeout_hours`, `auto_action`). The notification system would be the delivery mechanism for those escalations.

---

### GAP-29: User Interface [P2] [SEPARATE APPLICATION]

| Component | Description |
|-----------|-------------|
| Web Application | React/Vue SPA consuming the API (GAP-01) |
| GL Workbench | Journal entry, trial balance, account inquiries |
| AP/AR Workbenches | Invoice, payment, aging views |
| Approval Inbox | Pending approvals, delegation management |
| Period Close Cockpit | Close task management dashboard |
| Admin Console | Configuration, user management, SoD |

The UI is a separate application. It has no impact on the kernel, engines, modules, or services.

---

## Module Polish Items

All 19 modules are functionally complete. These are lower-priority enhancements within existing modules:

| Module | Item | Priority |
|--------|------|----------|
| **Reporting** | PDF/Excel/CSV/XBRL export renderers | P1 |
| **Reporting** | Multi-entity consolidation with elimination entries | P2 |
| **Reporting** | Direct method cash flow statement | P2 |
| **Inventory** | Lot tracking, serial number management | P2 |
| **Inventory** | Min/max reorder alerts | P2 |
| **Cash** | Positive pay file generation, check printing | P2 |
| **WIP** | Advanced routing, rework tracking | P2 |
| **Assets** | Lease-asset integration (cross-module with lease) | P2 |
| **Assets** | Barcode/asset tag scanning | P2 |
| **Tax** | Tax return filing integration (external service) | P2 |
| **Expense** | PDF receipt parsing (OCR integration) | P2 |
| **Intercompany** | Transfer pricing automation | P2 |
| **Project** | Gantt/scheduling integration | P2 |
| **Credit Loss** | Stress testing scenarios | P2 |
| **Approval (migration)** | Migrate AR, Expense, Procurement, Budget, Contracts workflows to approval-gated transitions (AP done) | P1 |
| **Ingestion (promoters)** | Complete stub promoters: InventoryItem, InventoryLocation, OpeningBalance | P1 |

---

## Reversal System Hardening

The reversal system is functional. These are optional hardening items:

| Item | Description | Priority |
|------|-------------|----------|
| Module-level void workflows | AP `void_invoice`, AR `void_payment` composing on `ReversalService` | Medium |
| Partial reversals | Reverse selected lines only (full reversals work today) | Low |
| Concurrent reversal serialization | `SELECT ... FOR UPDATE` on original entry before unique constraint check | Low |
| Policy snapshot on reversals | Snapshot `posting_policy_version`/`posting_policy_hash` on reversal entry | Low |
| Link graph uniqueness constraint | DB unique on `(parent_id, link_type)` for `REVERSED_BY` | Low |
| Effective date monotonicity | Enforce `effective_date >= original.effective_date` | Low |
| Concurrency tests | Race two reversals, assert exactly one succeeds | Low |
| Integration E2E tests | Multi-ledger reversal, post-close-reverse flow | Low |

---

## Prioritized Build Roadmap

### Phase 1: Production Foundation [P0]

| # | Item | Type | Depends On |
|---|------|------|------------|
| 1 | REST API Layer + Auth/RBAC (GAP-01) | INFRASTRUCTURE | None |

### Phase 2: Operational Completeness [P1]

| # | Item | Type | Depends On |
|---|------|------|------------|
| 2 | Audit & Compliance Reporting (GAP-26) | MODULE | GAP-01 (SoD/access reports need RBAC) |
| 3 | Data Export Framework (GAP-25) | UTILITY | None |
| 4 | Report Renderers (PDF/Excel) | UTILITY | None |
| 5 | Approval workflow migration (remaining modules) | MODULE | None |
| 6 | Ingestion promoter completion (stubs) | MODULE | None |

### Phase 3: Competitive Differentiation [P2]

| # | Item | Type | Depends On |
|---|------|------|------------|
| 7 | IFRS Policy Set (GAP-24) | CONFIG | None |
| 8 | Document Management (GAP-27) | UTILITY | GAP-01 |
| 9 | Notification System (GAP-28) | UTILITY | GAP-01 |
| 10 | User Interface (GAP-29) | SEPARATE APP | GAP-01 |
| 11 | Module polish items (see table above) | MODULE | Varies |

---

## Comparison Matrix vs SAP S/4HANA

| Feature Area | This System | SAP S/4HANA | Remaining Gap |
|-------------|-------------|-------------|---------------|
| **General Ledger** | Production-grade | Complete | Period close orchestrator |
| **Accounts Payable** | Production-grade (approval-gated) | Complete | -- |
| **Accounts Receivable** | Production-grade | Complete | Approval migration |
| **Asset Accounting** | Production-grade | Complete | Lease-asset integration |
| **Cost Accounting** | Production-grade (WIP + project + EVM) | CO module | Gantt/scheduling |
| **Inventory** | Production-grade | Complete (MM) | Lot/serial tracking |
| **Revenue Recognition** | ASC 606 five-step model | RAR | IFRS 15 |
| **Lease Accounting** | ASC 842 complete | RE-FX | IFRS 16 |
| **Treasury** | Bank recon, forecasting, NACHA | Full TRM | Positive pay |
| **Financial Reporting** | 6 reports, accounting invariants verified | BW/Fiori | Export renderers |
| **Consolidation** | IC transactions, elimination | Group Reporting | Transfer pricing |
| **Tax** | Deferred tax, multi-jurisdiction, ASC 740 | Vertex | Tax return integration |
| **Budget** | Encumbrance, forecast, variance | BPC / SAC | Approval migration |
| **Multi-Currency** | Translation, CTA, revaluation | Complete | -- |
| **Approval/Workflow** | Modular approval engine (188 tests) | SAP WF | Module migration (4 remaining) |
| **Data Ingestion** | CSV/JSON, staging, validation, promotion | LSMW/BODS | Excel, legacy migration |
| **Reversals** | Full reversal with economic links | Storno | Partial reversals |
| **API** | CLI scripts only | OData, BAPIs | **Critical -- GAP-01** |
| **Security** | Actor ID only | Full RBAC | **Critical -- GAP-01** |
| **Batch Processing** | Production-grade (289 tests, 10 invariants) | Background jobs | -- |
| | | | |
| **Architecture Quality** | **Superior** | Legacy monolith | *Advantage* |
| **Audit Trail** | **Superior** (hash-chained) | Change documents | *Advantage* |
| **Immutability** | **Superior** (ORM + 26 DB triggers) | App-level only | *Advantage* |
| **Gov Contracting** | **Competitive** (DCAA, ICE, CPFF/T&M/FFP) | Requires Deltek | *Advantage* |
| **Event Sourcing** | **Yes** | No | *Advantage* |
| **Replay Determinism** | **Yes** | No | *Advantage* |
| **Exception Lifecycle** | **Yes** (FAILED->RETRY->POSTED) | Manual correction | *Advantage* |

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| API layer introduces security surface | High | Security-first design, penetration testing, RBAC + SoD |
| IFRS dual-reporting doubles policy maintenance | Medium | Shared modules, separate YAML policy sets |
| Scope creep -- building all gaps at once | Critical | Phase strictly; ship API layer before anything else |
| Architecture degradation under pressure | High | Architecture tests enforce boundaries automatically |

---

*Revised 2026-02-01. Remaining work: API layer (P0), audit reporting (P1), data export (P1), IFRS config (P2), and ancillary utilities.*
