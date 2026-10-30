from __future__ import annotations

from datetime import UTC, datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Photo(Base):
    __tablename__ = "photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    stable_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(512))
    capture_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    camera: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lens: Mapped[str | None] = mapped_column(String(256), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="REVIEW")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), onupdate=lambda: datetime.now(UTC).replace(tzinfo=None))
    assets: Mapped[list["Asset"]] = relationship(back_populates="photo", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("source_path"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    size: Mapped[int] = mapped_column(Integer)
    modified_ns: Mapped[int] = mapped_column(Integer)
    photo: Mapped[Photo] = relationship(back_populates="assets")
