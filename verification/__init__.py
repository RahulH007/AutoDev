"""Proving that generated code compiles and runs before anyone reviews it."""

from verification.runner import LocalSubprocessRunner, Runner, get_runner
from verification.static_gate import run_static_gate
from verification.test_runner import run_tests

__all__ = [
    "LocalSubprocessRunner",
    "Runner",
    "get_runner",
    "run_static_gate",
    "run_tests",
]
