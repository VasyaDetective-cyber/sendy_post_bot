import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .config import settings
from .db import Delivery, Post, utcnow
from .services import publish_post
from .ui import after_kb, excerpt

log = logging.getLogger(__name__)


async def tick(bot: Bot, sm: async_sessionmaker) -> None:
    await _publish_due(bot, sm)
    await _delete_due(bot, sm)


async def _publish_due(bot: Bot, sm: async_sessionmaker) -> None:
    async with sm() as s:
        due = (await s.scalars(select(Post).where(Post.status == "scheduled", Post.publish_at <= utcnow()))).all()
        for p in due:
            p.status = "publishing"  # «захоплюємо», щоб не опублікувати двічі
        await s.commit()
        for p in due:
            try:
                ok, lines = await publish_post(bot, s, p)
                await s.commit()
            except Exception:
                log.exception("Помилка публікації поста %s", p.id)
                p.status = "draft"
                await s.commit()
                continue
            # сповіщення власнику — окремо: його збій не має скасовувати публікацію
            try:
                head = "⏰ <b>Відкладений пост опубліковано</b>" if ok else "⚠️ <b>Не вдалося опублікувати відкладений пост</b>"
                if ok and p.status == "scheduled":
                    head += "\n🔄 Наступна публікація за розкладом."
                await bot.send_message(p.owner_id, f"{head} #{p.id}\n<i>{excerpt(p.text, 50)}</i>\n\n" + "\n".join(lines),
                                       reply_markup=after_kb(p))
            except TelegramAPIError:
                log.warning("Не вдалось сповістити власника поста %s", p.id)


async def _delete_due(bot: Bot, sm: async_sessionmaker) -> None:
    async with sm() as s:
        rows = (await s.scalars(select(Delivery).where(
            Delivery.deleted.is_(False), Delivery.delete_at.is_not(None), Delivery.delete_at <= utcnow()))).all()
        for d in rows:
            try:
                await bot.delete_message(d.channel_id, d.message_id)
            except (TelegramRetryAfter, TelegramNetworkError):
                continue  # спробуємо наступного разу
            except TelegramAPIError as e:
                log.warning("Не вдалось видалити %s/%s: %s", d.channel_id, d.message_id, e)
            d.deleted = True
        await s.commit()


def setup_scheduler(bot: Bot, sm: async_sessionmaker) -> AsyncIOScheduler:
    sch = AsyncIOScheduler(timezone="UTC")
    sch.add_job(tick, "interval", seconds=settings.scheduler_interval_sec, args=[bot, sm],
                max_instances=1, coalesce=True, misfire_grace_time=60)
    return sch
