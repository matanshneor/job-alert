"""Railway entrypoint (Railpack runs `python main.py` by default). Same as `python -m jobalert.run`."""
import sys

from jobalert.run import main

if __name__ == "__main__":
    sys.exit(main())
