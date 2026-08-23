#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from urllib.error import URLError
from urllib.request import urlopen


url = os.environ.get("HEALTHCHECK_URL", "http://127.0.0.1:18188/system_stats")
try:
    with urlopen(url, timeout=4) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
except (OSError, URLError, RuntimeError) as exc:
    print(f"healthcheck failed: {exc}", file=sys.stderr)
    raise SystemExit(1)

print("healthy")
