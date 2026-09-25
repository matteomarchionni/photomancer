"""PhotoMancer launcher for a source checkout (no installation needed).

    pythonw PhotoMancer.pyw                 the control panel (shortcuts point here)
    python  PhotoMancer.pyw <command> ...   the command line, e.g. `run`, `check`
    pythonw PhotoMancer.pyw scheduled ...   what the weekly / monthly task runs

It only puts src/ on the import path; installed copies use `photomancer` /
`python -m photomancer` instead, frozen builds their own executable.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from photomancer.cli import main  # noqa: E402

sys.exit(main())
