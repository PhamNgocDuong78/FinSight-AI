"""Integration tests verifying Alembic schema tables and RBAC user seeding in PostgreSQL."""

from sqlalchemy import select, text

from app.db.postgres import get_session_factory
from app.db.seed import seed_default_users
from app.models.enums import UserRole
from app.models.user import User


async def test_database_tables_exist_in_postgres() -> None:
    """Verify that all 6 core tables exist in the live PostgreSQL database."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        )
        existing_tables = {row[0] for row in result.fetchall()}

    expected_tables = {
        "users",
        "documents",
        "parent_chunks",
        "financial_metrics",
        "chat_sessions",
        "chat_messages",
    }
    assert expected_tables.issubset(existing_tables), (
        f"Missing tables in PostgreSQL: {expected_tables - existing_tables}"
    )


async def test_seed_default_users_idempotent() -> None:
    """Verify seeding creates and maintains the 3 RBAC roles (admin, analyst, viewer)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        first_run = await seed_default_users(session)
        assert len(first_run) == 3

        second_run = await seed_default_users(session)
        assert len(second_run) == 3

        stmt = select(User).where(
            User.email.in_(
                [
                    "admin@finsight.ai",
                    "analyst@finsight.ai",
                    "viewer@finsight.ai",
                ]
            )
        )
        db_users = (await session.execute(stmt)).scalars().all()
        roles = {u.role for u in db_users}
        assert roles == {
            UserRole.ADMIN.value,
            UserRole.ANALYST.value,
            UserRole.VIEWER.value,
        }
