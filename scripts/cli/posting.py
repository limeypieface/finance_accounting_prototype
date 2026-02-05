"""CLI posting: engine scenario, AR workflow scenario, subledger scenario."""

from uuid import uuid4

from scripts.cli import config as cli_config


def post_engine_scenario(engine_service, scenario, actor_id):
    """Post a single engine/module scenario via ModulePostingService."""
    event_id = uuid4()
    result = engine_service.post_event(
        event_type=scenario["event_type"],
        payload=scenario["payload"],
        effective_date=cli_config.EFFECTIVE,
        actor_id=actor_id,
        amount=scenario["amount"],
        currency="USD",
        producer=scenario["event_type"].split(".")[0],
        event_id=event_id,
    )
    return result, event_id


def post_ar_invoice_workflow_scenario(session, orchestrator, scenario, actor_id):
    """Post an AR invoice via ARService so workflow transition appears in decision_log."""
    from finance_modules.ar.service import ARService

    ar_service = ARService(
        session=session,
        role_resolver=orchestrator.role_resolver,
        workflow_executor=orchestrator.workflow_executor,
        clock=orchestrator.clock,
    )
    invoice_id = uuid4()
    result = ar_service.record_invoice(
        invoice_id=invoice_id,
        customer_id=actor_id,
        amount=scenario["amount"],
        effective_date=cli_config.EFFECTIVE,
        actor_id=actor_id,
        currency="USD",
        invoice_number=f"INV-WF-{invoice_id.hex[:8]}",
    )
    return result, result.event_id


def post_subledger_scenario(session, post_simple, orchestrator, scenario, actor_id, clock):
    """Post a subledger scenario: GL entry + subledger entry."""
    from finance_engines.subledger import SubledgerEntry
    from finance_kernel.domain.subledger_control import SubledgerType
    from finance_kernel.domain.values import Money

    result = post_simple(
        scenario["gl_debit"], scenario["gl_credit"],
        scenario["amount"], scenario["memo"],
    )
    if not result.success:
        return result, None
    sl_type = SubledgerType(scenario["sl_type"])
    service = orchestrator.subledger_services.get(sl_type)
    if service is None:
        return result, None
    je_id = result.outcome.journal_entry_ids[0] if result.outcome and result.outcome.journal_entry_ids else None
    money = Money.of(scenario["amount"], "USD")
    credit_normal = sl_type in (SubledgerType.AP, SubledgerType.PAYROLL)
    if scenario["doc_type"] in ("INVOICE", "RECEIPT", "DEPOSIT"):
        debit, credit = (None, money) if credit_normal else (money, None)
    else:
        debit, credit = (money, None) if credit_normal else (None, money)
    entry = SubledgerEntry(
        subledger_type=sl_type.value,
        entity_id=scenario["entity_id"],
        source_document_type=scenario["doc_type"],
        source_document_id=str(uuid4()),
        source_line_id="0",
        debit=debit,
        credit=credit,
        effective_date=clock.now().date(),
        memo=scenario["memo"],
        dimensions={},
    )
    service.post(entry, gl_entry_id=je_id, actor_id=actor_id)
    session.flush()
    return result, sl_type
