"""Metric collectors.

Each collector has two layers:

- a **parser** function that takes a raw command-output string and returns a
  dict of numbers — pure, deterministic, unit-testable against fixtures with
  no device needed.
- a **sample** function that actually talks to the device, runs the command,
  and feeds the output through the parser.

Keeping these split is what lets `tests/test_parsers.py` run on CI without any
Android hardware attached.
"""

from . import activity, battery, cpu, fps, memory, network, thermal  # noqa: F401
