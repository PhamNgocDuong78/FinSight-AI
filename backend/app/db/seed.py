"""Idempotent database seed script for default RBAC accounts (admin, analyst, viewer)."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, setup_logging
from app.db.postgres import close_postgres_engine, get_session_factory
from app.models.enums import UserRole
from app.models.user import User

logger = get_logger(__name__)

DEFAULT_USERS: list[dict[str, str]] = [
    {
        "email": "admin@finsight.ai",
        "full_name": "FinSight System Admin",
        "role": UserRole.ADMIN.value,
    },
    {
        "email": "analyst@finsight.ai",
        "full_name": "FinSight Financial Analyst",
        "role": UserRole.ANALYST.value,
    },
    {
        "email": "viewer@finsight.ai",
        "full_name": "FinSight Guest Viewer",
        "role": UserRole.VIEWER.value,
    },
]


async def seed_default_users(session: AsyncSession) -> list[User]:
    """Ensure the 3 default RBAC users exist in PostgreSQL idempotently."""
    seeded_users: list[User] = []

    for user_data in DEFAULT_USERS:
        stmt = select(User).where(User.email == user_data["email"])
        result = await session.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if existing_user is None:
            new_user = User(
                email=user_data["email"],
                full_name=user_data["full_name"],
                role=user_data["role"],
            )
            session.add(new_user)
            seeded_users.append(new_user)
            logger.info(
                "seeded_user_created",
                email=user_data["email"],
                role=user_data["role"],
            )
        else:
            existing_user.full_name = user_data["full_name"]
            existing_user.role = user_data["role"]
            seeded_users.append(existing_user)
            logger.info(
                "seeded_user_verified",
                email=user_data["email"],
                role=user_data["role"],
            )

    await session.commit()
    for user in seeded_users:
        await session.refresh(user)

    return seeded_users


async def run_seed() -> None:
    """CLI entrypoint to seed default RBAC accounts."""
    setup_logging(debug=True)
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            users = await seed_default_users(session)
            logger.info("seed_completed", total_users=len(users))
    finally:
        await close_postgres_engine()


if __name__ == "__main__":
    asyncio.run(run_seed())
