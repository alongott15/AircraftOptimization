"""Create .env from .env.example with freshly generated local secrets (used by `make setup`).

Stdlib only, so it runs before any dependency is installed. Never overwrites an existing .env.
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = {
    "APP_DEMO_PASSWORD": lambda: secrets.token_urlsafe(12),
    "APP_JWT_SECRET": lambda: secrets.token_urlsafe(48),
}


def main() -> int:
    target, template = ROOT / ".env", ROOT / ".env.example"
    if target.exists():
        print(".env already exists — left unchanged")
        return 0
    text = template.read_text(encoding="utf-8")
    for key, make in GENERATED.items():
        text = re.sub(rf"^{key}=\s*$", f"{key}={make()}", text, count=1, flags=re.MULTILINE)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("Created .env with generated APP_DEMO_PASSWORD and APP_JWT_SECRET (see the file for the demo password)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
