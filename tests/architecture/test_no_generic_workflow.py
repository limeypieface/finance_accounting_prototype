"""
Architecture: no generic workflows in module services.

Directive: Generic or catch-all workflows (e.g. AR_OTHER_WORKFLOW, GL_OTHER_WORKFLOW)
are forbidden. Every financial action must bind to a specific lifecycle workflow
that reflects its economic meaning and risk profile.

See docs/WORKFLOW_DIRECTIVE.md.
"""

from pathlib import Path


def test_no_generic_workflow_in_module_services():
    """No module service may reference a generic/OTHER workflow."""
    modules_dir = Path("finance_modules")
    if not modules_dir.exists():
        return

    violations = []
    for svc_file in sorted(modules_dir.glob("*/service.py")):
        module_name = svc_file.parent.name
        try:
            src = svc_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if "OTHER_WORKFLOW" in src or "_OTHER_" in src:
            violations.append(f"  {svc_file}: uses generic/OTHER workflow (forbidden)")

    assert not violations, (
        "Generic workflows are forbidden. Each financial action must use an "
        "action-specific lifecycle workflow. See docs/WORKFLOW_DIRECTIVE.md.\n"
        + "\n".join(violations)
    )
