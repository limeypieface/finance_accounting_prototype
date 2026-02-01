# Documentation Coverage Report

**Generated:** 2026-01-31
**Baseline:** 3,614 tests passed, 0 regressions from documentation changes

## Summary by Layer

| Layer | Files | Headers | Hdr% | Classes | Doc'd | Cls% | Methods | Doc'd | Mth% | INV | Assert |
|-------|-------|---------|------|---------|-------|------|---------|-------|------|-----|--------|
| kernel/domain | 35 | 21 | 60% | 115 | 63 | 55% | 254 | 29 | 11% | 29 | 8 |
| kernel/services | 15 | 15 | 100% | 38 | 15 | 39% | 145 | 48 | 33% | 45 | 12 |
| kernel/models | 14 | 14 | 100% | 36 | 28 | 78% | 40 | 28 | 70% | 7 | 0 |
| kernel/db | 5 | 4 | 80% | 4 | 3 | 75% | 2 | 2 | 100% | 0 | 0 |
| kernel/selectors | 5 | 5 | 100% | 26 | 5 | 19% | 38 | 18 | 47% | 0 | 0 |
| engines | 14 | 14 | 100% | 94 | 23 | 24% | 122 | 13 | 11% | 13 | 2 |
| services | 17 | 17 | 100% | 24 | 14 | 58% | 68 | 15 | 22% | 12 | 1 |
| config | 10 | 4 | 40% | 31 | 8 | 26% | 4 | 1 | 25% | 2 | 2 |
| modules | 124 | 24 | 19% | 404 | 132 | 33% | 458 | 58 | 13% | 20 | 19 |
| **TOTAL** | **239** | **118** | **49%** | **772** | **291** | **38%** | **1131** | **212** | **19%** | **128** | **44** |

## Compliance Assessment

### Fully Compliant Layers (100% module headers)

- **kernel/services**: 15 files, 15/15 headers, 45 INVARIANT markers
- **kernel/models**: 14 files, 14/14 headers, 7 INVARIANT markers
- **kernel/selectors**: 5 files, 5/5 headers, 0 INVARIANT markers
- **engines**: 14 files, 14/14 headers, 13 INVARIANT markers
- **services**: 17 files, 17/17 headers, 12 INVARIANT markers

### Partially Compliant Layers

- **kernel/domain**: 60% headers (21/35 files)
- **kernel/db**: 80% headers (4/5 files)
- **config**: 40% headers (4/10 files)

### Non-Compliant Layer

- **modules**: 19% headers (24/124 files) — largest gap

## Non-Compliant Files (Risk Classification)

Files without module headers that contain classes or methods.

| File | Layer | Risk | Classes (total/doc'd) | Methods (total/doc'd) |
|------|-------|------|-----------------------|-----------------------|
| `finance_config/guard_ast.py` | config | Low | 1/0 | 0/0 |
| `finance_config/integrity.py` | config | Low | 1/0 | 0/0 |
| `finance_config/lifecycle.py` | config | Low | 1/0 | 0/0 |
| `finance_config/schema.py` | config | High | 15/0 | 0/0 |
| `finance_config/validator.py` | config | Low | 1/0 | 3/0 |
| `finance_kernel/domain/schemas/base.py` | kernel/domain | Medium | 3/0 | 5/0 |
| `finance_kernel/domain/schemas/registry.py` | kernel/domain | Medium | 3/0 | 8/3 |
| `finance_kernel/domain/strategies/generic_strategy.py` | kernel/domain | Low | 1/0 | 2/0 |
| `finance_modules/ap/config.py` | modules | Medium | 3/2 | 2/2 |
| `finance_modules/ap/models.py` | modules | High | 13/5 | 0/0 |
| `finance_modules/ap/orm.py` | modules | High | 8/8 | 16/0 |
| `finance_modules/ap/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/ap/service.py` | modules | High | 1/0 | 15/13 |
| `finance_modules/ap/workflows.py` | modules | Medium | 3/3 | 0/0 |
| `finance_modules/ar/config.py` | modules | Medium | 2/0 | 2/0 |
| `finance_modules/ar/models.py` | modules | High | 13/0 | 0/0 |
| `finance_modules/ar/orm.py` | modules | High | 9/9 | 18/0 |
| `finance_modules/ar/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/ar/service.py` | modules | High | 1/0 | 17/0 |
| `finance_modules/ar/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/assets/config.py` | modules | Low | 1/0 | 2/0 |
| `finance_modules/assets/models.py` | modules | High | 10/0 | 0/0 |
| `finance_modules/assets/orm.py` | modules | High | 7/0 | 14/0 |
| `finance_modules/assets/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/assets/service.py` | modules | High | 1/0 | 11/0 |
| `finance_modules/assets/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/budget/config.py` | modules | Low | 1/0 | 1/0 |
| `finance_modules/budget/models.py` | modules | High | 8/0 | 0/0 |
| `finance_modules/budget/orm.py` | modules | High | 5/5 | 10/0 |
| `finance_modules/budget/service.py` | modules | High | 1/0 | 10/0 |
| `finance_modules/budget/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/cash/orm.py` | modules | High | 6/0 | 12/0 |
| `finance_modules/contracts/config.py` | modules | Low | 1/0 | 2/0 |
| `finance_modules/contracts/models.py` | modules | Medium | 4/0 | 0/0 |
| `finance_modules/contracts/orm.py` | modules | Medium | 4/4 | 8/0 |
| `finance_modules/contracts/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/contracts/service.py` | modules | High | 1/0 | 14/0 |
| `finance_modules/contracts/workflows.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/credit_loss/config.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/credit_loss/models.py` | modules | High | 5/0 | 0/0 |
| `finance_modules/credit_loss/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/credit_loss/service.py` | modules | Medium | 1/0 | 8/0 |
| `finance_modules/expense/config.py` | modules | Medium | 2/0 | 2/0 |
| `finance_modules/expense/models.py` | modules | High | 11/0 | 0/0 |
| `finance_modules/expense/orm.py` | modules | Medium | 4/4 | 8/0 |
| `finance_modules/expense/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/expense/service.py` | modules | High | 1/0 | 14/0 |
| `finance_modules/expense/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/gl/config.py` | modules | Low | 1/0 | 2/0 |
| `finance_modules/gl/models.py` | modules | High | 16/0 | 0/0 |
| `finance_modules/gl/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/gl/service.py` | modules | High | 1/0 | 18/0 |
| `finance_modules/gl/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/intercompany/config.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/intercompany/models.py` | modules | High | 5/0 | 0/0 |
| `finance_modules/intercompany/orm.py` | modules | Medium | 3/3 | 6/0 |
| `finance_modules/intercompany/service.py` | modules | Medium | 1/0 | 7/0 |
| `finance_modules/inventory/config.py` | modules | Medium | 2/0 | 2/0 |
| `finance_modules/inventory/models.py` | modules | High | 16/11 | 0/0 |
| `finance_modules/inventory/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/inventory/service.py` | modules | High | 1/0 | 15/15 |
| `finance_modules/inventory/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/lease/config.py` | modules | Low | 1/0 | 1/0 |
| `finance_modules/lease/models.py` | modules | High | 8/0 | 0/0 |
| `finance_modules/lease/service.py` | modules | High | 1/0 | 10/0 |
| `finance_modules/lease/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/payroll/config.py` | modules | Medium | 2/0 | 2/0 |
| `finance_modules/payroll/models.py` | modules | High | 13/0 | 0/0 |
| `finance_modules/payroll/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/payroll/service.py` | modules | High | 1/0 | 14/0 |
| `finance_modules/payroll/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/procurement/config.py` | modules | Medium | 2/0 | 2/0 |
| `finance_modules/procurement/models.py` | modules | High | 11/0 | 0/0 |
| `finance_modules/procurement/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/procurement/service.py` | modules | High | 1/0 | 11/0 |
| `finance_modules/procurement/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/project/config.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/project/models.py` | modules | High | 5/0 | 0/0 |
| `finance_modules/project/orm.py` | modules | Medium | 3/3 | 6/0 |
| `finance_modules/project/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/project/service.py` | modules | High | 1/0 | 10/0 |
| `finance_modules/reporting/config.py` | modules | Medium | 2/0 | 3/0 |
| `finance_modules/reporting/models.py` | modules | High | 18/0 | 0/0 |
| `finance_modules/reporting/service.py` | modules | Medium | 1/0 | 8/0 |
| `finance_modules/reporting/statements.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/tax/workflows.py` | modules | Medium | 3/0 | 0/0 |
| `finance_modules/wip/config.py` | modules | Low | 1/0 | 2/0 |
| `finance_modules/wip/models.py` | modules | High | 10/0 | 0/0 |
| `finance_modules/wip/profiles.py` | modules | Low | 1/0 | 0/0 |
| `finance_modules/wip/service.py` | modules | High | 1/0 | 12/0 |
| `finance_modules/wip/workflows.py` | modules | Medium | 3/0 | 0/0 |

## Risk Summary

- **High risk**: 34 files (5+ classes or 10+ methods without module header)
- **Medium risk**: 29 files (2+ classes or 5+ methods without module header)
- **Low risk**: 28 files (1 class or <5 methods without module header)
- **Compliant**: 148 files

## Invariant Coverage

- **Total INVARIANT markers**: 128 across 239 files
- **Total runtime assertions**: 44 across 239 files
- **Rules referenced**: R1-R25, L1-L5, P1/P7/P10/P11/P12/P15, SL-G1/G3/G4/G5/G6/G9

## Regression Validation

Full test suite executed after documentation enforcement:

```
3,614 passed, 11 skipped, 13 xfailed, 10 xpassed, 0 failures, 0 errors
```

Zero regressions introduced by documentation changes.
