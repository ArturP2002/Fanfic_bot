"""Личная библиотека пользователя."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ReadingStatus


class LibraryItem(Base):
    """Связь пользователь — произведение со статусом чтения."""

    __tablename__ = "library_items"
    __table_args__ = (
        UniqueConstraint("user_id", "work_id", name="uq_library_user_work"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), index=True)
    reading_status: Mapped[str] = mapped_column(
        default=ReadingStatus.WANT.value,
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

    user = relationship("User", back_populates="library_items")
    work = relationship("Work", back_populates="library_items")
