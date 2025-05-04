#!/usr/bin/env python3
"""Generate Björnify version"""

import os

run_number = os.environ.get("GITHUB_RUN_NUMBER", "0")
VERSION = f"1.0.{run_number}"

with open("bjornify/version.py", mode="w", encoding="utf-8") as f:
    f.write(f'__version__ = "{VERSION}"\n')

print(f"Generated version: {VERSION}")
