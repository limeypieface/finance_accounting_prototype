"""
Interactive Accounting CLI — global CLI application for the finance system.

Use the system through the CLI: post events, view reports, import data,
close periods, trace decisions. Not a demo-only script; this is the primary
way to operate the system interactively.

Entry point: scripts/interactive.py or python -m scripts.cli
"""

from scripts.cli.main import main

__all__ = ["main"]
