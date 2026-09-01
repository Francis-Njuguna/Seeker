"""Grant or revoke the admin role. The only supported way to create an admin.

``UserRole.ADMIN`` is assigned out of band on purpose: an admin sees unredacted
identity documents for every record in the system (``routers/admin.py``), so no
HTTP route may ever grant it. Nothing an attacker reaches over the network can
escalate a role — the grant requires shell access to the deployment.

    docker compose exec -T backend python scripts/promote_admin.py list
    docker compose exec -T backend python scripts/promote_admin.py grant a@b.co
    docker compose exec -T backend python scripts/promote_admin.py revoke a@b.co

The user must already exist: register through the normal signup flow first, then
promote. This script deliberately cannot create an account, so a promotion can
never bring a user into being with a password nobody chose.

Exit codes: 0 success, 1 usage error, 2 user not found, 3 refused.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Running ``python scripts/promote_admin.py`` puts ``scripts/`` on sys.path, not
# the backend root, so ``import app`` fails even though the cwd is correct. Fix
# it here rather than via PYTHONPATH in compose: the script then works unchanged
# under docker exec, a bare shell, or cron, with no environment setup to get
# wrong — and getting it wrong is how you end up locked out of the admin role.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import User, UserRole

USAGE = __doc__


def _fail(message: str, code: int) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


async def _find(session, email: str) -> User | None:
    """Look a user up by email, case-insensitively.

    Registration does not normalize case, so an operator typing the address from
    memory would otherwise get "no such user" for an account that exists.
    """
    return await session.scalar(
        select(User).where(func.lower(User.email) == email.strip().lower())
    )


async def cmd_list() -> int:
    async with SessionLocal() as session:
        admins = (
            await session.scalars(
                select(User).where(User.role == UserRole.ADMIN).order_by(User.email)
            )
        ).all()
        total = await session.scalar(select(func.count()).select_from(User))

    if not admins:
        # Not an error, but worth shouting about: with no admin, every claim
        # requiring review and every refund under DECISIONS.md §3 is stuck.
        print(f"NO ADMINS. {total} user(s) total.")
        print("Nothing can work the review queue or settle a refund in this state.")
        return 0

    print(f"{len(admins)} admin(s) of {total} user(s):")
    for user in admins:
        verified = "phone-verified" if user.phone_verified_at else "phone UNVERIFIED"
        print(f"  {user.email}  ({user.display_name}, {verified})")
    return 0


async def cmd_grant(email: str) -> int:
    async with SessionLocal() as session:
        user = await _find(session, email)
        if user is None:
            return _fail(
                f"no user with email {email!r}. Register through the app first, "
                "then promote — this script cannot create accounts.",
                2,
            )
        if user.role == UserRole.ADMIN:
            print(f"{user.email} is already an admin. Nothing to do.")
            return 0

        previous = user.role
        user.role = UserRole.ADMIN
        await session.commit()

    print(f"granted admin: {user.email} ({user.display_name})")
    print(f"  previous role: {previous}")
    print("  this account can now read unredacted identity documents for every record.")
    return 0


async def cmd_revoke(email: str) -> int:
    async with SessionLocal() as session:
        user = await _find(session, email)
        if user is None:
            return _fail(f"no user with email {email!r}", 2)
        if user.role != UserRole.ADMIN:
            print(f"{user.email} is not an admin (role: {user.role}). Nothing to do.")
            return 0

        remaining = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.ADMIN)
            .where(User.id != user.id)
        )
        if remaining == 0:
            # Refuse rather than warn. Locking yourself out means the review
            # queue and every pending refund freeze until someone gets shell
            # access back, and the only recovery is manual SQL — exactly what
            # this script exists to avoid.
            return _fail(
                f"{user.email} is the only admin. Revoking would leave the "
                "deployment with nobody able to work the review queue or settle "
                "refunds. Grant admin to someone else first.",
                3,
            )

        user.role = UserRole.SEEKER
        await session.commit()

    print(f"revoked admin: {user.email} (now seeker). {remaining} admin(s) remain.")
    return 0


async def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0 if argv else 1

    command, *rest = argv

    if command == "list":
        return await cmd_list()

    if command in {"grant", "revoke"}:
        if len(rest) != 1:
            return _fail(f"{command} needs exactly one email address", 1)
        return await (cmd_grant if command == "grant" else cmd_revoke)(rest[0])

    return _fail(f"unknown command {command!r}. Expected list, grant or revoke.", 1)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
