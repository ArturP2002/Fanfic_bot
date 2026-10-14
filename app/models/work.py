"""Модели общей базы произведений."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ConfirmationStatus, WorkStatus


class Work(Base):
    """Произведение в общей базе FicBrain."""

    __tablename__ = "works"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    russian_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author: Mapped[str | None] = mapped_column(String(500), nullable=True)
    normalized_title: Mapped[str] = mapped_column(String(500), index=True)
    normalized_author: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    fandom: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pairing: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default=WorkStatus.UNKNOWN.value,
    )
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    translation_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_status: Mapped[str] = mapped_column(
        String(32),
        default=ConfirmationStatus.CONFIRMED.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    aliases = relationship(
        "WorkAlias",
        back_populates="work",
        cascade="all, delete-orphan",
    )
    library_items = relationship("LibraryItem", back_populates="work")


class WorkAlias(Base):
    """Альтернативное название или сокращение произведения."""

    __tablename__ = "work_aliases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(500), index=True)
    alias_type: Mapped[str] = mapped_column(String(32), default="alt")  # alt | abbreviation

    work = relationship("Work", back_populates="aliases")
