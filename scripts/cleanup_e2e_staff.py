"""Soft-delete synthetic E2E smoke staff accounts and remove E2E message data.

The E2E smoke script creates temporary Nurse accounts named ``E2E Smoke`` and
sends messages with subjects prefixed ``e2e-`` (including attachment rows). They
are test artifacts, not demo content. This utility keeps Staff Management and
the demo inboxes clean
without hard-deleting staff rows (audit and foreign-key history stay intact)
and without touching any other account or real message.

Usage:
    cd backend && uv run python ../scripts/cleanup_e2e_staff.py
    docker compose run --rm app python /app/scripts/cleanup_e2e_staff.py
    docker compose exec -T app python /app/scripts/cleanup_e2e_staff.py --email e2e-smoke-123@example.com

The default pattern is ``e2e-smoke-%@example.com``. Pass ``--email`` to remove a
single exact test account. ``--dry-run`` reports the matching rows without
changing anything.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import load_settings
from app.models import Message, MessageAttachment, StaffUser, UserSession

DEFAULT_PATTERN = "e2e-smoke-%@example.com"
E2E_MESSAGE_SUBJECT_PATTERN = "e2e-%"


async def cleanup(*, email: str | None, pattern: str, dry_run: bool) -> int:
    settings = load_settings()
    engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            query = select(StaffUser).where(StaffUser.deleted_at.is_(None))
            if email:
                query = query.where(StaffUser.email == email.strip().lower())
            else:
                query = query.where(StaffUser.email.like(pattern))
            users = list((await session.scalars(query.order_by(StaffUser.email))).all())
            message_ids = select(Message.id).where(
                Message.subject.like(E2E_MESSAGE_SUBJECT_PATTERN)
            )
            message_query = select(func.count()).select_from(Message).where(
                Message.subject.like(E2E_MESSAGE_SUBJECT_PATTERN)
            )
            message_count = int(await session.scalar(message_query) or 0)
            attachment_query = select(func.count()).select_from(MessageAttachment).where(
                MessageAttachment.message_id.in_(message_ids)
            )
            attachment_count = int(await session.scalar(attachment_query) or 0)
            if not users and message_count == 0:
                print("No matching E2E smoke staff accounts or messages.")
                return 0

            if users:
                print(f"Matched {len(users)} E2E smoke staff account(s):")
                for user in users:
                    print(f"  {user.email} (id={user.id}, active={user.is_active})")
            if message_count:
                print(f"Matched {message_count} E2E smoke message(s).")
            if attachment_count:
                print(f"Matched {attachment_count} E2E smoke attachment(s).")
            if dry_run:
                print("Dry run: no rows changed.")
                return 0

            now = datetime.now(timezone.utc)
            revoked = 0
            if users:
                user_ids = [user.id for user in users]
                revoked_result = await session.execute(
                    update(UserSession)
                    .where(UserSession.user_id.in_(user_ids), UserSession.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
                revoked = revoked_result.rowcount or 0
                for user in users:
                    user.is_active = False
                    user.deleted_at = now
                    user.updated_at = now
            if message_count:
                # Attachment rows use a RESTRICT foreign key: remove only the
                # E2E child rows first, then their parent messages.
                await session.execute(
                    delete(MessageAttachment).where(
                        MessageAttachment.message_id.in_(message_ids)
                    )
                )
                await session.execute(
                    delete(Message).where(Message.subject.like(E2E_MESSAGE_SUBJECT_PATTERN))
                )
            await session.commit()
            print(
                f"Soft-deleted {len(users)} account(s); "
                f"revoked {revoked} active session(s); "
                f"deleted {message_count} E2E message(s) "
                f"and {attachment_count} attachment(s)."
            )
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="Exact E2E staff email to soft-delete.")
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"SQL LIKE pattern for E2E staff emails (default: {DEFAULT_PATTERN}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report matches without changing rows.")
    args = parser.parse_args()
    try:
        return asyncio.run(cleanup(email=args.email, pattern=args.pattern, dry_run=args.dry_run))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"Cleanup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
