"""Single source of truth for the service's version string.

Read from installed package metadata (which reads pyproject.toml). When the package
isn't installed (running directly from source without `pip install -e .`), fall
back to a sentinel that makes the situation obvious.
"""

from __future__ import annotations

import importlib.metadata

try:
    VERSION = importlib.metadata.version("ai-native-kitchen")
except importlib.metadata.PackageNotFoundError:
    VERSION = "0.0.0+local"
