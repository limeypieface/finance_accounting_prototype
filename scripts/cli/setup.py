"""CLI setup: DB helpers, account creation from config, full_setup, resume_setup, simple pipeline."""

import time
from uuid import uuid4, uuid5

from sqlalchemy import create_engine, text

from scripts.cli import config as cli_config


def kill_orphaned():
    """Kill orphaned connections to the interactive DB so drop_tables can run."""
    admin_url = cli_config.DB_URL.rsplit("/", 1)[0] + "/postgres"
    try:
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with eng.connect() as conn:
            conn.execute(text("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = 'finance_kernel_test'
                  AND pid <> pg_backend_pid()
            """))
        eng.dispose()
    except Exception:
        pass


def tables_exist(session) -> bool:
    """Return True if public.accounts exists."""
    try:
        r = session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='accounts')"
        ))
        return r.scalar()
    except Exception:
        session.rollback()
        return False


def has_accounts(session) -> bool:
    """Return True if any accounts exist."""
    from finance_kernel.models.account import Account
    try:
        return session.query(Account).count() > 0
    except Exception:
        session.rollback()
        return False


def _account_type_for_code(code: str):
    from finance_kernel.models.account import AccountType
    if code.startswith("SL-"):
        return AccountType.ASSET
    prefix = int(code[0]) if code[0].isdigit() else 0
    if prefix == 1:
        return AccountType.ASSET
    elif prefix == 2:
        return AccountType.LIABILITY
    elif prefix == 3:
        return AccountType.EQUITY
    elif prefix == 4:
        return AccountType.REVENUE
    elif prefix in (5, 6):
        return AccountType.EXPENSE
    return AccountType.EXPENSE


def _normal_balance_for_type(atype):
    from finance_kernel.models.account import AccountType, NormalBalance
    if atype in (AccountType.ASSET, AccountType.EXPENSE):
        return NormalBalance.DEBIT
    return NormalBalance.CREDIT


def _create_accounts_from_config(session, config, actor_id):
    from finance_kernel.models.account import Account
    created = 0
    seen_codes = set()
    for binding in config.role_bindings:
        code = binding.account_code
        if code in seen_codes:
            continue
        seen_codes.add(code)
        acct_id = uuid5(cli_config.COA_UUID_NS, code)
        atype = _account_type_for_code(code)
        nbal = _normal_balance_for_type(atype)
        tags = ["rounding"] if binding.role == "ROUNDING" else None
        acct = Account(
            id=acct_id, code=code, name=f"{binding.role} ({code})",
            account_type=atype, normal_balance=nbal,
            is_active=True, tags=tags, created_by_id=actor_id,
        )
        session.add(acct)
        created += 1
    session.flush()
    return created


def _build_simple_pipeline(session, role_resolver, orchestrator, actor_id):
    """Build a post() function for simple debit/credit events."""
    from decimal import Decimal
    from finance_kernel.domain.accounting_intent import (
        AccountingIntent,
        AccountingIntentSnapshot,
        IntentLine,
        LedgerIntent,
    )
    from finance_kernel.domain.meaning_builder import (
        EconomicEventData,
        MeaningBuilderResult,
    )
    from finance_kernel.models.event import Event
    from finance_kernel.utils.hashing import hash_payload

    coordinator = orchestrator.interpretation_coordinator
    clock = orchestrator.clock

    def post(debit_role: str, credit_role: str, amount: Decimal, memo: str = ""):
        source_event_id = uuid4()
        effective = clock.now().date()
        payload = {"memo": memo, "amount": str(amount)}
        evt = Event(
            event_id=source_event_id, event_type="interactive.posting",
            occurred_at=clock.now(), effective_date=effective,
            actor_id=actor_id, producer="interactive",
            payload=payload, payload_hash=hash_payload(payload),
            schema_version=1, ingested_at=clock.now(),
        )
        session.add(evt)
        session.flush()
        econ_data = EconomicEventData(
            source_event_id=source_event_id, economic_type="interactive.posting",
            effective_date=effective, profile_id="InteractiveProfile",
            profile_version=1, profile_hash=None, quantity=amount,
        )
        intent = AccountingIntent(
            econ_event_id=uuid4(), source_event_id=source_event_id,
            profile_id="InteractiveProfile", profile_version=1,
            effective_date=effective,
            ledger_intents=(
                LedgerIntent(ledger_id="GL", lines=(
                    IntentLine.debit(debit_role, amount, "USD"),
                    IntentLine.credit(credit_role, amount, "USD"),
                )),
            ),
            snapshot=AccountingIntentSnapshot(coa_version=1, dimension_schema_version=1),
        )
        result = coordinator.interpret_and_post(
            meaning_result=MeaningBuilderResult.ok(econ_data),
            accounting_intent=intent,
            actor_id=actor_id,
        )
        session.flush()
        return result
    return post


def full_setup(session, clock):
    """Create tables, config-based COA, fiscal period, wire pipelines. Returns 6-tuple."""
    from finance_kernel.db.engine import drop_tables, get_session
    from finance_kernel.db.immutability import register_immutability_listeners
    from finance_modules._orm_registry import create_all_tables
    from finance_config import get_active_config
    from finance_config.bridges import build_role_resolver
    from finance_kernel.models.fiscal_period import FiscalPeriod, PeriodStatus
    from finance_kernel.models.party import Party, PartyStatus, PartyType
    from finance_kernel.services.module_posting_service import ModulePostingService
    from finance_modules import register_all_modules
    from finance_services.invokers import register_standard_engines
    from finance_services.posting_orchestrator import PostingOrchestrator

    kill_orphaned()
    time.sleep(0.3)
    try:
        drop_tables()
    except Exception:
        kill_orphaned()
        time.sleep(0.5)
        try:
            drop_tables()
        except Exception:
            pass
    create_all_tables(install_triggers=True)
    register_immutability_listeners()
    new_session = get_session()
    config = get_active_config(legal_entity="*", as_of_date=cli_config.EFFECTIVE)
    actor_id = uuid4()
    _create_accounts_from_config(new_session, config, actor_id)
    period = FiscalPeriod(
        period_code="FY2026", name="Fiscal Year 2026",
        start_date=cli_config.FY_START, end_date=cli_config.FY_END,
        status=PeriodStatus.OPEN, created_by_id=actor_id,
    )
    new_session.add(period)
    actor_party = Party(
        id=actor_id, party_code="SYSTEM-DEMO",
        party_type=PartyType.EMPLOYEE, name="Demo System Actor",
        status=PartyStatus.ACTIVE, is_active=True,
        created_by_id=actor_id,
    )
    new_session.add(actor_party)
    new_session.flush()
    register_all_modules()
    role_resolver = build_role_resolver(config)
    orchestrator = PostingOrchestrator(
        session=new_session, compiled_pack=config,
        role_resolver=role_resolver, clock=clock,
    )
    register_standard_engines(orchestrator.engine_dispatcher)
    engine_service = ModulePostingService.from_orchestrator(orchestrator, auto_commit=False)
    post_simple = _build_simple_pipeline(new_session, role_resolver, orchestrator, actor_id)
    new_session.commit()
    return new_session, post_simple, engine_service, actor_id, config, orchestrator


def resume_setup(clock):
    """Reconnect to existing DB, rebuild wiring. Returns same 6-tuple as full_setup()."""
    from finance_config import get_active_config
    from finance_config.bridges import build_role_resolver
    from finance_kernel.db.engine import get_session
    from finance_kernel.db.immutability import register_immutability_listeners
    from finance_kernel.models.party import Party, PartyStatus, PartyType
    from finance_kernel.services.module_posting_service import ModulePostingService
    from finance_modules import register_all_modules
    from finance_services.invokers import register_standard_engines
    from finance_services.posting_orchestrator import PostingOrchestrator

    register_immutability_listeners()
    session = get_session()
    config = get_active_config(legal_entity="*", as_of_date=cli_config.EFFECTIVE)
    actor_party = session.query(Party).filter_by(party_code="SYSTEM-DEMO").first()
    if actor_party is None:
        # DB may have been reset by Ironflow (only SYSTEM exists). Create SYSTEM-DEMO so Resume works.
        actor_id = uuid4()
        creator_id = session.query(Party).filter_by(party_code="SYSTEM").first()
        creator_id = creator_id.id if creator_id else actor_id
        actor_party = Party(
            id=actor_id,
            party_code="SYSTEM-DEMO",
            party_type=PartyType.EMPLOYEE,
            name="Demo System Actor",
            status=PartyStatus.ACTIVE,
            is_active=True,
            created_by_id=creator_id,
        )
        session.add(actor_party)
        session.commit()
    else:
        actor_id = actor_party.id
    register_all_modules()
    role_resolver = build_role_resolver(config)
    orchestrator = PostingOrchestrator(
        session=session, compiled_pack=config,
        role_resolver=role_resolver, clock=clock,
    )
    register_standard_engines(orchestrator.engine_dispatcher)
    engine_service = ModulePostingService.from_orchestrator(orchestrator, auto_commit=False)
    post_simple = _build_simple_pipeline(session, role_resolver, orchestrator, actor_id)
    return session, post_simple, engine_service, actor_id, config, orchestrator
