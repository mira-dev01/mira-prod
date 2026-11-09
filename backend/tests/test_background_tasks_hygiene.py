"""Static guard: no bare, result-discarding asyncio.create_task() in app/.

The event loop only weakly references tasks, so a detached task whose handle
nobody keeps can be garbage collected before it finishes -- silently losing
an escalation email, a WhatsApp, the guest-memory update, a busy-recovery
message. Detached work goes through app.utils.background.spawn()."""

import ast
import pathlib

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
_SPAWNERS = {"create_task", "ensure_future"}


def _discarded_task_calls(path: pathlib.Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        # An expression statement whose value is the call = result thrown away.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _SPAWNERS
                and isinstance(func.value, ast.Name)
                and func.value.id == "asyncio"
            ):
                yield node.lineno


def test_no_fire_and_forget_task_is_left_unreferenced():
    offenders = [
        f"{path.relative_to(APP_DIR.parent)}:{line}"
        for path in sorted(APP_DIR.rglob("*.py"))
        for line in _discarded_task_calls(path)
    ]
    assert offenders == [], (
        "Use app.utils.background.spawn() instead of a bare asyncio.create_task()/ensure_future() "
        f"whose result is discarded: {offenders}"
    )
