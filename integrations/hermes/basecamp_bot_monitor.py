"""Hermes monitor_script: poll Basecamp and print the queue of items awaiting a draft.

Hermes hashes this output each tick and only wakes the drafting agent when it changes,
so polling every minute costs no model calls while Basecamp is quiet.
"""
import os
import sys
from pathlib import Path

repo = Path(os.path.realpath(__file__)).parents[2]
sys.path.insert(0, str(repo / "src"))

from bcbot.cli import main  # noqa: E402

main(["poll", "--monitor"])
