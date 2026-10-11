import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


async def test_models_match_migrations(test_env: dict[str, str]) -> None:
    """Fails if someone changes a model without generating a migration (`alembic check`)."""
    await asyncio.to_thread(command.check, alembic_config())


async def test_downgrade_to_base_and_back(test_env: dict[str, str]) -> None:
    cfg = alembic_config()
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")
