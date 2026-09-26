"""Package execution entry point for python -m cwt."""
import sys

from cwt.cli import main

if __name__ == "__main__":
    sys.exit(main())
