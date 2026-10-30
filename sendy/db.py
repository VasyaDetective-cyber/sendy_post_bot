from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Наївний UTC (усі дати в БД зберігаються в UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    role: Mapped[str] = mapped_column(String(16), default="admin")  # superadmin | admin
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # draft | scheduled | publishing | published
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)  # HTML (premium-емодзі як <tg-emoji>)
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    buttons: Mapped[list] = mapped_column(JSON, default=list)  # [[{"text","url"}, ...], ...]
    silent: Mapped[bool] = mapped_column(Boolean, default=False)
    pin: Mapped[bool] = mapped_column(Boolean, default=False)
    protect: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ad: Mapped[bool] = mapped_column(Boolean, default=False)
    delete_after_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reply_to: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"chat": "...", "msg": 1}
    repeat_every_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repeat_left: Mapped[int] = mapped_column(Integer, default=0)
    channel_ids: Mapped[list] = mapped_column(JSON, default=list)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    panel_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    panel_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Delivery(Base):
    """Кожна фактична публікація в канал (потрібна для автовидалення)."""
    __tablename__ = "deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(Integer)
    delete_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


async def init_db(engine: AsyncEngine, sm: async_sessionmaker, super_admin_id: int) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sm() as s:
        user = await s.get(User, super_admin_id)
        if user is None:
            s.add(User(id=super_admin_id, role="superadmin"))
        else:
            user.role = "superadmin"
        # після аварійної зупинки: «публікується» -> знову в чергу
        for p in (await s.scalars(select(Post).where(Post.status == "publishing"))).all():
            p.status = "scheduled"
        await s.commit()
