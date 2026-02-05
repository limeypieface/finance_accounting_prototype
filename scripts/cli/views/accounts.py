"""CLI views: chart of accounts, vendors and customers."""

from scripts.cli.util import fmt_amount


def show_accounts(session):
    """List chart of accounts (e.g. from QBO import or config setup)."""
    from finance_kernel.models.account import Account

    accounts = session.query(Account).order_by(Account.code).all()
    if not accounts:
        print("\n  No accounts in the chart of accounts.\n")
        return
    W = 72
    print()
    print("=" * W)
    print("  CHART OF ACCOUNTS".center(W))
    print("=" * W)
    print(f"  {'Code':<12} {'Name':<32} {'Type':<12} {'Normal'}")
    print(f"  {'-'*12} {'-'*32} {'-'*12} {'-'*6}")
    for a in accounts:
        acct_type = getattr(a.account_type, "value", a.account_type)
        normal = getattr(a.normal_balance, "value", a.normal_balance)
        print(f"  {a.code:<12} {(a.name or '')[:32]:<32} {str(acct_type):<12} {str(normal)}")
    print(f"\n  Total: {len(accounts)} accounts")
    print()


def show_vendors_and_customers(session):
    """List vendors and customers (e.g. from QBO import)."""
    from finance_modules.ap.orm import VendorProfileModel
    from finance_modules.ar.orm import CustomerProfileModel

    vendors = session.query(VendorProfileModel).order_by(VendorProfileModel.code).all()
    customers = session.query(CustomerProfileModel).order_by(CustomerProfileModel.code).all()
    W = 72
    print()
    print("=" * W)
    print("  VENDORS & CUSTOMERS".center(W))
    print("=" * W)
    print("\n  --- Vendors ---")
    if not vendors:
        print("    (none)")
    else:
        print(f"    {'Code':<24} {'Name':<40}")
        print(f"    {'-'*24} {'-'*40}")
        for v in vendors:
            print(f"    {(v.code or '')[:24]:<24} {(v.name or '')[:40]:<40}")
        print(f"    Total: {len(vendors)} vendors")
    print("\n  --- Customers ---")
    if not customers:
        print("    (none)")
    else:
        print(f"    {'Code':<24} {'Name':<40}")
        print(f"    {'-'*24} {'-'*40}")
        for c in customers:
            print(f"    {(c.code or '')[:24]:<24} {(c.name or '')[:40]:<40}")
        print(f"    Total: {len(customers)} customers")
    print()
