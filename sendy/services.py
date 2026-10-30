"""Відправка постів, публікація, пошук/парсинг."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import Channel, Delivery, Post, utcnow

log = logging.getLogger(__name__)

MEDIA = {"photo": ("send_photo", "photo"), "video": ("send_video", "video"),
         "animation": ("send_animation", "animation"), "document": ("send_document", "document"),
         "audio": ("send_audio", "audio")}


# ───────────── контент повідомлення ─────────────
def extract_content(m: Message) -> tuple[str, str | None, str | None]:
    """-> (html_text, media_type, file_id). `html_text` сам перетворює custom_emoji на <tg-emoji>."""
    text = m.html_text or ""
    if m.animation: return text, "animation", m.animation.file_id
    if m.photo: return text, "photo", m.photo[-1].file_id
    if m.video: return text, "video", m.video.file_id
    if m.document: return text, "document", m.document.file_id
    if m.audio: return text, "audio", m.audio.file_id
    return text, None, None


# ───────────── відправка ─────────────
def post_text(p: Post) -> str:
    t = p.text or ""
    if p.is_ad:
        t += "\n\n<i>Реклама</i>"
    return t


def post_markup(p: Post) -> InlineKeyboardMarkup | None:
    if not p.buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=x["text"], url=x["url"]) for x in row] for row in p.buttons])


async def send_post(bot: Bot, chat_id: int, p: Post, reply_to: int | None = None) -> Message:
    kw = dict(chat_id=chat_id, parse_mode="HTML", disable_notification=p.silent,
              protect_content=p.protect, reply_markup=post_markup(p))
    if reply_to:
        kw["reply_parameters"] = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
    text = post_text(p)
    if p.media_type in MEDIA:
        method, arg = MEDIA[p.media_type]
        kw[arg] = p.media_id
        kw["caption"] = text or None
        return await getattr(bot, method)(**kw)
    return await bot.send_message(text=text, **kw)


def resolve_reply(reply_to: dict | None, ch: Channel) -> int | None:
    if not reply_to:
        return None
    key = str(reply_to["chat"]).lower()
    if key == str(ch.id) or (ch.username and key == ch.username.lower()):
        return int(reply_to["msg"])
    return None


async def publish_post(bot: Bot, session: AsyncSession, p: Post) -> tuple[int, list[str]]:
    """Публікує пост у всі обрані канали. Повертає (кількість успіхів, рядки звіту)."""
    chans = (await session.scalars(select(Channel).where(Channel.id.in_(p.channel_ids or [])))).all()
    ok, lines = 0, []
    for ch in chans:
        try:
            msg = await send_post(bot, ch.id, p, resolve_reply(p.reply_to, ch))
        except TelegramAPIError as e:
            lines.append(f"❌ {html.escape(ch.title)} — {html.escape(str(e).split(':', 1)[-1].strip()[:120])}")
            continue
        ok += 1
        warn = ""
        if p.pin:
            try:
                await bot.pin_chat_message(ch.id, msg.message_id, disable_notification=p.silent)
            except TelegramAPIError:
                warn = " (⚠️ не вдалось закріпити)"
        delete_at = utcnow() + timedelta(minutes=p.delete_after_min) if p.delete_after_min else None
        session.add(Delivery(post_id=p.id, channel_id=ch.id, message_id=msg.message_id, delete_at=delete_at))
        lines.append(f"✅ {html.escape(ch.title)}{warn}")
    if ok and p.repeat_every_min and p.repeat_left > 0:
        p.repeat_left -= 1
        p.publish_at = utcnow() + timedelta(minutes=p.repeat_every_min)
        p.status = "scheduled"
    elif ok:
        p.status = "published"
    else:
        p.status = "draft"
    return ok, lines


# ───────────── парсинг введення ─────────────
def parse_buttons(raw: str) -> list[list[dict]]:
    """Рядок = ряд кнопок. Кілька в ряд: `Текст - url | Текст2 - url2`."""
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = []
        for part in line.split("|"):
            if " - " not in part and " — " not in part:
                raise ValueError(f"Не бачу « - » у: {part.strip()[:40]}")
            sep = " - " if " - " in part else " — "
            text, url = part.rsplit(sep, 1)
            text, url = text.strip(), url.strip()
            if not text or not url:
                raise ValueError(f"Порожній текст або посилання: {part.strip()[:40]}")
            if not re.match(r"^(https?|tg)://", url):
                url = "https://" + url.lstrip("/")
            if "." not in url and not url.startswith("tg://"):
                raise ValueError(f"Схоже, це не посилання: {url[:40]}")
            row.append({"text": text[:64], "url": url})
        rows.append(row[:8])
    if not rows:
        raise ValueError("Порожньо")
    return rows[:20]


def find_links(text: str | None) -> list[str]:
    seen = []
    for raw in re.findall(r'href="([^"]+)"', text or ""):
        u = html.unescape(raw)
        if u not in seen:
            seen.append(u)
    return seen


def replace_links(p: Post, old: str, new: str) -> int:
    """Замінює посилання у тексті та кнопках. Повертає кількість замін."""
    count = 0
    if p.text:
        for a, bb in ((old, new), (html.escape(old, quote=False), html.escape(new, quote=False)),
                      (html.escape(old), html.escape(new))):
            if a in p.text:
                count += p.text.count(a)
                p.text = p.text.replace(a, bb)
    new_rows = []
    for row in p.buttons or []:
        new_row = []
        for x in row:
            if x["url"] == old:
                x = {**x, "url": new}
                count += 1
            new_row.append(x)
        new_rows.append(new_row)
    p.buttons = new_rows
    return count


def parse_delay(raw: str) -> int | None:
    m = re.fullmatch(r"(\d+)\s*([a-zа-яіїє]*)", raw.strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    mult = {"": 60, "h": 60, "г": 60, "год": 60, "d": 1440, "д": 1440, "дн": 1440, "m": 1, "хв": 1, "min": 1}.get(unit)
    if mult is None or n <= 0:
        return None
    minutes = n * mult
    return minutes if minutes <= 30 * 1440 else None


def parse_local_dt(raw: str) -> datetime | None:
    try:
        dt = datetime.strptime(raw.strip(), "%d.%m.%Y %H:%M").replace(tzinfo=settings.tz)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def parse_msg_link(raw: str) -> dict | None:
    raw = raw.strip()
    if m := re.match(r"https?://t\.me/c/(\d+)/(?:\d+/)?(\d+)", raw):
        return {"chat": f"-100{m.group(1)}", "msg": int(m.group(2))}
    if m := re.match(r"https?://t\.me/([A-Za-z0-9_]{4,})/(\d+)", raw):
        return {"chat": m.group(1).lower(), "msg": int(m.group(2))}
    return None
