"""Allow `python -m oikbas_finance ...` invocation."""

from oikbas_finance.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
