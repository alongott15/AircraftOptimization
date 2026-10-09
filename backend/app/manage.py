"""Local account management for the POC (APP_AUTH_MODE=local).

    python -m app.manage create-user [--username U] [--display-name D] [--role ADMIN]
    python -m app.manage set-password [--username U]

Passwords are read with a hidden prompt (never from arguments, which end up in shell history) and never logged.
Accounts live in the app store; `make demo` recreates the POC database, so re-run this after reseeding.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence

from app.clock import SystemClock
from app.config import get_settings
from app.db.session import session_factory
from app.security.rbac import ROLES
from app.services import admin
from app.services.errors import ServiceError

Reader = Callable[[str], str]


def _ask(value: str | None, prompt: str, read: Reader) -> str:
    return value if value else read(prompt).strip()


def _new_password(read_secret: Reader) -> str:
    first = read_secret(f"Password (min {admin.MIN_PASSWORD_LENGTH} characters): ")
    if read_secret("Repeat password: ") != first:
        raise ServiceError(422, "VALIDATION_ERROR", "passwords do not match")
    return first


def main(argv: Sequence[str] | None = None, *, read: Reader = input, read_secret: Reader = getpass.getpass,
         url: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage", description="Local account management (POC)")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="create a local user")
    create.add_argument("--username")
    create.add_argument("--display-name")
    create.add_argument("--role", choices=ROLES, default="ADMIN")
    reset = sub.add_parser("set-password", help="set a new password for a local user")
    reset.add_argument("--username")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.auth_mode != "local":
        print("APP_AUTH_MODE is not 'local': users sign in through SSO, local accounts are disabled.", file=sys.stderr)
        return 1
    now = SystemClock().now()
    try:
        with session_factory(url or settings.database_url)() as s:
            username = _ask(args.username, "Username: ", read).lower()
            if args.command == "create-user":
                display = _ask(args.display_name, "Display name (optional): ", read) or username
                admin.create_local_user(s, username, display, args.role, _new_password(read_secret), now)
                message = f"Created {args.role} user '{username}'."
            else:
                admin.set_local_password(s, username, _new_password(read_secret), now)
                message = f"Password updated for '{username}'."
            s.commit()
    except ServiceError as exc:
        print(f"Error: {exc.detail}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
