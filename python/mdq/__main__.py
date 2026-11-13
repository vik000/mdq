"""`python -m mdq` runs the command line (same as the `mdq` script)."""
import sys

from mdq.cli import main

if __name__ == "__main__":  # also importable (stubtest, tools) without running the CLI
    sys.exit(main())
