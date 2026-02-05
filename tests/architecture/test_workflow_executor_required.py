"""
Architecture: financial actions must pass through the workflow executor.

Invariant: If an action can change state, move money, or create a financial fact,
it must pass through the workflow executor (no bypass).

Enforcement (AST/source scan of finance_modules/*/service.py):
1. Any module service that has at least one "financial action" method must
   require workflow_executor in __init__ (no default).
2. Any method that performs a financial action must call execute_transition
   (method body contains execute_transition on the path before the state/money change).

Financial action indicators (method body contains):
- post_event(  — posts journal entry (creates financial fact)
- establish_link( — creates economic link (state)
- apply_payment( — applies payment (moves money)

This test is expected to fail until all modules and all financial-action methods
are wired: require workflow_executor and call execute_transition. See docs/archive/GUARD_WIRING_GAP.md.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _module_service_classes():
    """Yield (module_name, class_name, filepath, class_node, source_lines)."""
    modules_dir = Path("finance_modules")
    if not modules_dir.exists():
        return

    for svc_file in sorted(modules_dir.glob("*/service.py")):
        module_name = svc_file.parent.name
        try:
            source = svc_file.read_text()
            lines = source.splitlines()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Service"):
                yield (module_name, node.name, svc_file, node, lines)


def _method_body_source(method_node, lines: list[str]) -> str:
    """Return the source of a method body (lines)."""
    start = method_node.lineno - 1
    end = getattr(method_node, "end_lineno", None)
    if end is None:
        end = start + 1
        for i in range(start, len(lines)):
            if i > start and lines[i].strip().startswith("def "):
                end = i
                break
        else:
            end = len(lines)
    else:
        end = min(end, len(lines))
    return "\n".join(lines[start:end])


def _init_has_required_workflow_executor(class_node, lines: list[str]) -> bool:
    """True if __init__ has a required parameter workflow_executor (no default)."""
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            # Required params are args before * or **; default count is len(defaults)
            args = item.args
            names = [a.arg for a in args.args if a.arg != "self"]
            n_defaults = len(args.defaults)
            n_required = len(names) - n_defaults
            if "workflow_executor" not in names:
                return False
            idx = names.index("workflow_executor")
            # Required iff it's in the required segment (index < n_required)
            return idx < n_required
    return False


# Substrings that indicate a method performs a financial action
FINANCIAL_ACTION_MARKERS = (
    "post_event(",
    "establish_link(",
    "apply_payment(",
)

# Substrings that indicate the method passes through workflow executor (canonical wrapper or direct call)
WORKFLOW_EXECUTOR_MARKERS = ("execute_transition(", "run_workflow_guard(")


class TestFinancialActionsThroughWorkflowExecutor:
    """If an action can change state, move money, or create a financial fact,
    it must pass through the workflow executor."""

    def test_services_with_financial_actions_require_workflow_executor(self):
        """Any module service that has a financial-action method must require workflow_executor in __init__."""
        violations = []

        for module_name, class_name, svc_file, class_node, lines in _module_service_classes():
            has_financial_action_method = False
            for item in class_node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                if item.name.startswith("_"):
                    continue
                body_src = _method_body_source(item, lines)
                for marker in FINANCIAL_ACTION_MARKERS:
                    if marker in body_src:
                        has_financial_action_method = True
                        break

            if not has_financial_action_method:
                continue

            if not _init_has_required_workflow_executor(class_node, lines):
                violations.append(
                    f"  {svc_file}: {module_name}.{class_name} has financial action methods "
                    "but __init__ does not require workflow_executor (no default)"
                )

        assert not violations, (
            "Workflow executor required — any module service with financial action methods "
            "(post_event, establish_link, apply_payment) must require workflow_executor in __init__:\n"
            + "\n".join(violations)
        )

    def test_financial_action_methods_call_execute_transition(self):
        """Any method that performs a financial action must call execute_transition (workflow executor)."""
        violations = []

        for module_name, class_name, svc_file, class_node, lines in _module_service_classes():
            for item in class_node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                body_src = _method_body_source(item, lines)

                has_action = any(m in body_src for m in FINANCIAL_ACTION_MARKERS)
                if not has_action:
                    continue

                if not any(m in body_src for m in WORKFLOW_EXECUTOR_MARKERS):
                    violations.append(
                        f"  {svc_file}:{item.lineno} {module_name}.{class_name}.{item.name} "
                        "performs a financial action (post_event/establish_link/apply_payment) "
                        "but does not call execute_transition or run_workflow_guard"
                    )

        assert not violations, (
            "Workflow executor enforcement — any method that changes state, moves money, or "
            "creates a financial fact must call execute_transition:\n" + "\n".join(violations)
        )

    def test_every_post_calls_workflow_first(self):
        """In modules that require workflow_executor, every posting path must call execute_transition/run_workflow_guard before post_event()."""
        violations = []
        post_marker = "post_event("

        for module_name, class_name, svc_file, class_node, lines in _module_service_classes():
            # Only enforce order in services that already require workflow_executor (wired modules)
            if not _init_has_required_workflow_executor(class_node, lines):
                continue
            for item in class_node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                body_src = _method_body_source(item, lines)
                if post_marker not in body_src:
                    continue
                # Accept either execute_transition( or run_workflow_guard( (canonical wrapper)
                workflow_marker = next((m for m in WORKFLOW_EXECUTOR_MARKERS if m in body_src), None)
                if workflow_marker is None:
                    violations.append(
                        f"  {svc_file}:{item.lineno} {module_name}.{class_name}.{item.name} "
                        "calls post_event but does not call execute_transition or run_workflow_guard"
                    )
                    continue
                idx_workflow = body_src.index(workflow_marker)
                idx_post = body_src.index(post_marker)
                if idx_workflow >= idx_post:
                    violations.append(
                        f"  {svc_file}:{item.lineno} {module_name}.{class_name}.{item.name} "
                        "must call execute_transition/run_workflow_guard before post_event (governance before ledger)"
                    )

        assert not violations, (
            "Every posting path must call execute_transition() before post_event() (R29 workflow coverage):\n"
            + "\n".join(violations)
        )
