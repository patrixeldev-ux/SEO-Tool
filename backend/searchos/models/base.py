from __future__ import annotations

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4, server_default=sa.text("gen_random_uuid()")
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False
    )


def str_enum[E: enum.Enum](enum_cls: type[E], name: str) -> sa.Enum:
    """Store a Python enum as a short VARCHAR (no native PG enum type to migrate).

    The CHECK constraint is declared explicitly with `enum_check` so it gets one deterministic name.
    """
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=24,
        create_constraint=False,
        values_callable=lambda e: [member.value for member in e],
    )


def enum_check(column: str, enum_cls: type[enum.Enum], name: str) -> sa.CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return sa.CheckConstraint(f"{column} IN ({values})", name=name)
