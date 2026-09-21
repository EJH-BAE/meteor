#!/usr/bin/env python3
"""GPU 단독 최적화 진입점 (내장/외장 무관).

  python amd_igpu.py
  python amd_igpu.py --once --sim
  python amd_igpu.py --overnight --sim
"""

from __future__ import annotations

import sys

from hybrid_gpu import main


if __name__ == "__main__":
    extra = [arg for arg in sys.argv[1:] if arg != "--amd"]
    raise SystemExit(main(["--amd", *extra]))
