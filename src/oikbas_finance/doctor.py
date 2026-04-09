"""Channel health check command."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.table import Table

from oikbas_finance.channels import REGISTRY, list_channels


def run_doctor() -> int:
    """Probe every registered channel and print a status table.

    Returns 0 if all channels are healthy/skipped, 1 if any are degraded/down.
    """
    console = Console()
    table = Table(title="oikbas-finance channel health")
    table.add_column("Channel", style="cyan")
    table.add_column("Status")
    table.add_column("Message", overflow="fold")

    names = list_channels()
    if not names:
        console.print("[yellow]No channels registered.[/yellow]")
        return 0

    any_bad = False
    for name in names:
        cls = REGISTRY[name]
        try:
            instance = cls()
            status, msg = instance.check()
        except Exception as exc:  # pragma: no cover — surface errors at boundary
            status, msg = "down", f"check() raised: {exc}"

        color = {
            "healthy": "green",
            "skipped": "dim",
            "degraded": "yellow",
            "down": "red",
        }.get(status, "white")
        table.add_row(name, f"[{color}]{status}[/{color}]", msg)

        if status in ("degraded", "down"):
            any_bad = True

    console.print(table)
    return 1 if any_bad else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_doctor())
