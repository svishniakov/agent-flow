#!/usr/bin/env python3
"""Run the canonical Agent Flow script from skills/agent-flow/scripts."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "skills" / "agent-flow" / "scripts" / Path(__file__).name
sys.path.insert(0, str(TARGET.parent))
runpy.run_path(str(TARGET), run_name="__main__")
