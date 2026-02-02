"""
finance_ingestion -- Configuration-driven ERP data ingestion.

Provides staging, per-record validation, field mapping, and promotion to
live ORM tables. Used for migrations and bulk master-data loading.

Architecture:
    finance_ingestion/ is a top-level package. Nothing in kernel/,
    engines/, or modules/ imports from ingestion. See ERP_INGESTION_PLAN.md.
"""
