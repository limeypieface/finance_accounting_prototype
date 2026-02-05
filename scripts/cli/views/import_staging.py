"""CLI views: Import & Staging (upload folder, stage, promote, remove)."""

import json
import textwrap
from pathlib import Path

from scripts.cli import config as cli_config

# Wrap validation messages so they stay on screen (indent 6 spaces, width 72)
_ERROR_WRAP_WIDTH = 72
_ERROR_INDENT = "      "


def _format_validation_errors(validation_errors, max_errors=5):
    """Format validation errors for readable display: one per line, wrapped."""
    if not validation_errors:
        return []
    out = []
    for i, e in enumerate(validation_errors):
        if i >= max_errors:
            out.append("      ...")
            break
        msg = getattr(e, "message", None) or getattr(e, "code", "")
        if not msg:
            continue
        code = getattr(e, "code", "")
        if code and code != msg:
            line = f"{code}: {msg}"
        else:
            line = str(msg)
        for wrapped in textwrap.wrap(line, width=_ERROR_WRAP_WIDTH, initial_indent=_ERROR_INDENT, subsequent_indent=_ERROR_INDENT):
            out.append(wrapped)
    return out


def _import_staging_services(session, clock, config):
    """Build ImportService, PromotionService, and mapping registry from active config."""
    from finance_config import get_active_config
    from finance_ingestion.promoters import default_promoter_registry
    from finance_ingestion.services import (
        ImportService,
        PromotionService,
        build_mapping_registry_from_defs,
    )
    from finance_kernel.services.auditor_service import AuditorService

    pack = get_active_config(cli_config.ENTITY, cli_config.EFFECTIVE) if config is None else config
    registry = build_mapping_registry_from_defs(pack.import_mappings)
    auditor = AuditorService(session, clock)
    import_svc = ImportService(
        session, clock=clock, mapping_registry=registry, auditor_service=auditor
    )
    promotion_svc = PromotionService(
        session,
        promoters=default_promoter_registry(),
        clock=clock,
        auditor_service=auditor,
    )
    return import_svc, promotion_svc, registry


def show_import_staging(session, clock, actor_id, config):
    """
    Import & Staging: put CSV/JSON in project upload/ folder, select to stage,
    review batches, fix issues, remove from staging, promote when ready.
    """
    from finance_ingestion.domain.types import ImportBatchStatus

    W = 80
    UPLOAD_EXTENSIONS = (".csv", ".json", ".xlsx")
    UPLOAD_DIR = cli_config.UPLOAD_DIR
    import_svc, promotion_svc, registry = _import_staging_services(session, clock, config)
    mapping_names = sorted(registry.keys()) if isinstance(registry, dict) else []

    while True:
        batches = import_svc.list_batches()
        staged_filenames = {b.source_filename for b in batches}
        upload_files = []
        if UPLOAD_DIR.is_dir():
            for p in sorted(UPLOAD_DIR.iterdir()):
                if p.is_file() and p.suffix.lower() in UPLOAD_EXTENSIONS:
                    if p.name not in staged_filenames:
                        upload_files.append(p)

        batch_preflight = {}
        ready_batch_ids = []
        batches_with_issues = 0
        for b in batches:
            if b.status == ImportBatchStatus.VALIDATED and b.valid_records > 0:
                graph = promotion_svc.compute_preflight_graph(b.batch_id)
                batch_preflight[b.batch_id] = graph
                if graph.blocked_count == 0:
                    ready_batch_ids.append(b.batch_id)
            if b.invalid_records > 0:
                batches_with_issues += 1

        print()
        print("=" * W)
        print("  IMPORT & STAGING".center(W))
        print("=" * W)
        print()
        print(f"  Upload folder: {UPLOAD_DIR}")
        if not upload_files:
            print("  No files ready to stage (put CSV/JSON in upload/ or remove existing batch to re-upload).")
        else:
            print(f"  Files ready to stage ({len(upload_files)}):")
            for i, p in enumerate(upload_files, 1):
                print(f"    {i}. {p.name}")
        print()

        if batches:
            n_ready = len(ready_batch_ids)
            if batches_with_issues > 0 and n_ready == 0:
                print("  Status: Some batches have validation errors. Fix issues (V #) or remove (D #) to re-upload.")
            elif batches_with_issues > 0 and n_ready > 0:
                print(f"  Status: {n_ready} batch(es) ready to promote. {batches_with_issues} batch(es) have validation issues.")
            elif n_ready == len([b for b in batches if b.status == ImportBatchStatus.VALIDATED and b.valid_records > 0]):
                if n_ready > 0:
                    print("  Status: All staged data is validated and ready for promotion.")
                else:
                    print("  Status: Batches staged; fix issues (V #) or promote when ready.")
            else:
                print(f"  Status: {n_ready} batch(es) ready to promote.")
            print()
            print(f"  {'#':<3} {'Mapping':<18} {'Entity':<14} {'File':<20} {'Status':<10} {'Total':>6} {'Valid':>6} {'Inv':>4}  Ready")
            print("  " + "-" * 76)
            for i, b in enumerate(batches, 1):
                ready = "—"
                if b.status == ImportBatchStatus.COMPLETED:
                    ready = "Done"
                elif b.status == ImportBatchStatus.VALIDATED and b.valid_records > 0:
                    graph = batch_preflight.get(b.batch_id)
                    ready = "Blocked" if graph and graph.blocked_count > 0 else "Yes"
                status_short = b.status.value[:10] if b.status else "—"
                file_short = (b.source_filename or "")[:20]
                print(f"  {i:<3} {(b.mapping_name or '')[:18]:<18} {(b.entity_type or '')[:14]:<14} {file_short:<20} {status_short:<10} {b.total_records:>6} {b.valid_records:>6} {b.invalid_records:>4}  {ready}")
            print()
        else:
            print("  No staged batches. Use U to upload a file from the upload folder.")
            print()

        print("  U = Upload   A # = Auto-assign   P = Promote   V # = View issues   O # N = Open Import row N   E # N = Edit row   # = Promote   D # = Remove   R = Refresh   Enter = Back")
        try:
            choice = input("  Action: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "":
            return
        if choice.upper() == "R":
            continue

        if choice.upper() == "U":
            if not upload_files:
                print("  No files in upload folder to stage (add CSV/JSON to upload/ or remove a batch with D #).")
                continue
            if not mapping_names:
                print("  No import mappings in config. Add import_mappings to your config set.")
                continue
            print("  Select file by number:")
            for i, p in enumerate(upload_files, 1):
                print(f"    {i}. {p.name}")
            try:
                file_choice = input("  File number: ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not file_choice.isdigit():
                continue
            fi = int(file_choice)
            if fi < 1 or fi > len(upload_files):
                continue
            source_path = upload_files[fi - 1]
            print("  Select mapping:")
            for i, name in enumerate(mapping_names, 1):
                print(f"    {i}. {name}")
            try:
                map_choice = input("  Mapping number: ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not map_choice.isdigit():
                continue
            mi = int(map_choice)
            if mi < 1 or mi > len(mapping_names):
                continue
            mapping_name = mapping_names[mi - 1]
            mapping = registry.get(mapping_name)
            if not mapping:
                print(f"  Mapping {mapping_name!r} not found.")
                continue
            try:
                batch = import_svc.load_batch(source_path, mapping, actor_id)
                session.flush()
                validated = import_svc.validate_batch(batch.batch_id, actor_id=actor_id)
                session.commit()
                print(f"  Staged: {validated.total_records} records, Valid: {validated.valid_records}, Invalid: {validated.invalid_records}")
                if validated.invalid_records > 0 and validated.valid_records == 0:
                    errs = import_svc.get_batch_errors(batch.batch_id)
                    if errs:
                        first = errs[0]
                        print(f"  Sample errors (Import row {first.source_row}):")
                        for line in _format_validation_errors(first.validation_errors, max_errors=5):
                            print(line)
                        if mapping_name == "qbo_chart_of_accounts":
                            print('  Tip: Use M to define a mapping that matches your CSV columns, or fix headers.')
                        # Offer auto-assign when missing code is a likely cause
                        try:
                            auto = input("  Auto-assign missing account/codes for this batch? [y/N]: ").strip().lower()
                            if auto == "y":
                                n = import_svc.auto_assign_codes(batch.batch_id)
                                if n > 0:
                                    validated = import_svc.validate_batch(batch.batch_id, actor_id=actor_id)
                                    session.commit()
                                    print(f"  Auto-assigned {n} code(s). Re-validated: Valid {validated.valid_records}, Invalid {validated.invalid_records}")
                        except (EOFError, KeyboardInterrupt):
                            pass
            except Exception as e:
                session.rollback()
                print(f"  Error: {e}")
            continue

        if choice.upper().startswith("A"):
            rest = choice[1:].strip().lstrip("#").strip()
            if rest.isdigit() and batches:
                idx = int(rest)
                if 1 <= idx <= len(batches):
                    b = batches[idx - 1]
                    try:
                        n = import_svc.auto_assign_codes(b.batch_id)
                        if n > 0:
                            import_svc.validate_batch(b.batch_id, actor_id=actor_id)
                            session.commit()
                            print(f"  Auto-assigned {n} code(s) for batch {idx}. Re-validate with R to refresh.")
                        else:
                            print(f"  Batch {idx}: no records with missing code (nothing to assign).")
                    except Exception as e:
                        session.rollback()
                        print(f"  Error: {e}")
                continue
            else:
                print("  Use A # with a batch number (e.g. A 1).")
                continue

        if choice.upper().startswith("D"):
            rest = choice[1:].strip().lstrip("#")
            if rest.isdigit() and batches:
                idx = int(rest)
                if 1 <= idx <= len(batches):
                    b = batches[idx - 1]
                    try:
                        import_svc.delete_batch(b.batch_id)
                        session.commit()
                        print(f"  Removed batch {idx} ({b.source_filename}) from staging. You can re-upload it with U.")
                    except Exception as e:
                        session.rollback()
                        print(f"  Error: {e}")
                continue
            else:
                print("  Use D # with a batch number (e.g. D 1).")
                continue

        if choice.upper() == "P":
            if not batches:
                continue
            if not ready_batch_ids:
                print("  No batches are ready to promote (validate first or fix issues).")
                continue
            promoted_total = 0
            failed_total = 0
            for batch_id in ready_batch_ids:
                result = promotion_svc.promote_batch(batch_id, actor_id)
                session.commit()
                promoted_total += result.promoted
                failed_total += result.failed
                if result.errors:
                    for err in result.errors[:3]:
                        print(f"    Batch {batch_id}: Import row {err.source_row} — {err.message}")
            print(f"  Promoted: {promoted_total}  Failed: {failed_total}")
            continue

        if choice.upper().startswith("V"):
            rest = choice[1:].strip().lstrip("#")
            if rest.isdigit() and batches:
                idx = int(rest)
                if 1 <= idx <= len(batches):
                    b = batches[idx - 1]
                    errors = import_svc.get_batch_errors(b.batch_id)
                    if not errors:
                        print(f"  Batch {idx} ({b.mapping_name}): no invalid records.")
                    else:
                        print(f"  Batch {idx} ({b.mapping_name}) — {len(errors)} invalid record(s):")
                        for rec in errors[:15]:
                            print(f"  Import row {rec.source_row}:")
                            for line in _format_validation_errors(rec.validation_errors, max_errors=5):
                                print(line)
                        if len(errors) > 15:
                            print(f"  ... and {len(errors) - 15} more invalid record(s).")
                        # If many rows report missing date/lines, staging may be from an older file
                        missing_date_lines = sum(
                            1 for r in errors
                            if any(
                                (e.get("code") or "").startswith("MISSING_") or "date" in str(e.get("message", "")).lower() or "lines" in str(e.get("message", "")).lower()
                                for e in (r.validation_errors or []) if isinstance(e, dict)
                            )
                        )
                        if missing_date_lines >= 5 and b.entity_type == "journal":
                            print("  Tip: If those Import rows have data in your JSON, delete this batch (D #) and re-upload (U) so staging matches the current file.")
                    input("  Enter to continue: ")
                continue

        # O # N = Open batch #, Import row N — show full record (raw, mapped, errors)
        if choice.upper().startswith("O "):
            parts = choice.split()
            if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit() and batches:
                bi, rn = int(parts[1]), int(parts[2])
                if 1 <= bi <= len(batches) and rn >= 1:
                    b = batches[bi - 1]
                    try:
                        rec = import_svc.get_record_by_batch_and_row(b.batch_id, rn)
                        print()
                        print(f"  — Import row {rec.source_row} (batch {bi}: {b.mapping_name}) —")
                        print(f"  (In the source JSON/file, search for \"_import_row\": {rec.source_row} to find this record.)")
                        print(f"  Status: {rec.status}")
                        if rec.raw_data:
                            print("  Raw data:")
                            for line in textwrap.wrap(json.dumps(rec.raw_data, indent=2, default=str), width=76, initial_indent="    ", subsequent_indent="    "):
                                print(line)
                        if rec.mapped_data:
                            print("  Mapped data:")
                            for line in textwrap.wrap(json.dumps(rec.mapped_data, indent=2, default=str), width=76, initial_indent="    ", subsequent_indent="    "):
                                print(line)
                        if rec.validation_errors:
                            print("  Validation errors:")
                            for line in _format_validation_errors(rec.validation_errors, max_errors=99):
                                print(line)
                        else:
                            print("  Validation errors: none")
                        print()
                        input("  Enter to continue: ")
                    except ValueError as e:
                        print(f"  {e}")
                continue
            else:
                print("  Use O # N (e.g. O 1 738) to open Import row N in batch #.")
                continue

        # E # N = Edit batch #, Import row N — provide JSON file with corrected raw_data, re-validate
        if choice.upper().startswith("E "):
            parts = choice.split()
            if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit() and batches:
                bi, rn = int(parts[1]), int(parts[2])
                if 1 <= bi <= len(batches) and rn >= 1:
                    b = batches[bi - 1]
                    try:
                        rec = import_svc.get_record_by_batch_and_row(b.batch_id, rn)
                        print(f"  Import row {rn}: provide path to a JSON file containing the corrected raw_data for this record.")
                        print(f"  Current record has {len(rec.raw_data or {})} raw keys.")
                        try:
                            path_str = input("  Path to JSON file (or Enter to cancel): ").strip()
                        except (EOFError, KeyboardInterrupt):
                            path_str = ""
                        if path_str:
                            path = Path(path_str).expanduser()
                            if not path.is_file():
                                print(f"  File not found: {path}")
                            else:
                                corrected = json.loads(path.read_text(encoding="utf-8"))
                                if not isinstance(corrected, dict):
                                    print("  File must contain a single JSON object (the raw_data record).")
                                else:
                                    updated = import_svc.retry_record(rec.record_id, corrected)
                                    session.flush()
                                    session.commit()
                                    status = updated.status
                                    err_count = len(updated.validation_errors or [])
                                    print(f"  Updated Import row {rn}. Status: {status}. Errors: {err_count}")
                                    if err_count > 0:
                                        for line in _format_validation_errors(updated.validation_errors, max_errors=8):
                                            print(line)
                    except ValueError as e:
                        print(f"  {e}")
                    except json.JSONDecodeError as e:
                        print(f"  Invalid JSON: {e}")
                continue
            else:
                print("  Use E # N (e.g. E 1 738) then enter path to JSON file with corrected raw_data.")
                continue

        if choice.isdigit() and batches:
            idx = int(choice)
            if 1 <= idx <= len(batches):
                b = batches[idx - 1]
                if b.status == ImportBatchStatus.COMPLETED:
                    print(f"  Batch {idx} is already promoted (completed). Use R to refresh.")
                    continue
                if b.batch_id not in ready_batch_ids:
                    if b.status != ImportBatchStatus.VALIDATED:
                        print(f"  Batch {idx} is not validated yet.")
                    elif b.valid_records == 0:
                        print(f"  Batch {idx} has no valid records. Fix validation issues (V {idx}).")
                    else:
                        print(f"  Batch {idx} is blocked (dependencies). Promote referenced data first.")
                    continue
                result = promotion_svc.promote_batch(b.batch_id, actor_id)
                session.commit()
                print(f"  Promoted: {result.promoted}  Failed: {result.failed}")
                if result.errors:
                    for err in result.errors[:5]:
                        print(f"    Import row {err.source_row}: {err.message}")
            continue
        print("  Unknown action. Use U, A #, P, V #, O # N, E # N, #, D #, R, or Enter.")
