import html
import re
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Chat, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from aiogram.types import Message, MessageOriginChannel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Channel
from ..ui import ChCb

router = Router(name="channels")


class ChS(StatesGroup):
    add = State()


def not_command(m: Message) -> bool:
    return not (m.text or "").startswith("/")


def cb(text: str, act: str, cid: int = 0) -> Btn:
    return Btn(text=text, callback_data=ChCb(act=act, cid=cid).pack())


async def view(session: AsyncSession, note: str | None = None) -> tuple[str, Markup]:
    chans = (await session.scalars(select(Channel).order_by(Channel.title))).all()
    text = "📡 <b>Канали</b>\n" + ("Натисніть на канал, щоб видалити його зі списку." if chans
                                  else "Поки порожньо. Додайте перший канал 👇")
    if note:
        text += "\n\n" + note
    rows = [[cb(f"🗑 {c.title}"[:60], "del", c.id)] for c in chans]
    rows.append([cb("➕ Додати канал", "add")])
    return text, Markup(inline_keyboard=rows)


@router.message(Command("channels"))
async def cmd_channels(m: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    text, kb = await view(session)
    await m.answer(text, reply_markup=kb)


@router.callback_query(ChCb.filter(F.act == "list"))
async def cb_list(c: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    text, kb = await view(session)
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@router.callback_query(ChCb.filter(F.act == "add"))
async def cb_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(ChS.add)
    await state.update_data(chat=c.message.chat.id, mid=c.message.message_id)
    await c.message.edit_text(
        "➕ <b>Додавання каналу</b>\n\n"
        "1. Додайте бота в адміни каналу з правом <b>публікації повідомлень</b>\n"
        "2. <b>Перешліть сюди будь-який пост</b> з каналу — або надішліть @username / ID каналу",
        reply_markup=Markup(inline_keyboard=[[cb("❌ Скасувати", "list")]]))
    await c.answer()


@router.callback_query(ChCb.filter(F.act == "del"))
async def cb_del(c: CallbackQuery, callback_data: ChCb, session: AsyncSession):
    ch = await session.get(Channel, callback_data.cid)
    if not ch:
        return await c.answer("Вже видалено")
    await c.message.edit_text(
        f"Видалити канал <b>{html.escape(ch.title)}</b> зі списку?\n(У самому каналі нічого не зміниться.)",
        reply_markup=Markup(inline_keyboard=[[cb("✅ Так", "delok", ch.id), cb("❌ Ні", "list")]]))
    await c.answer()


@router.callback_query(ChCb.filter(F.act == "delok"))
async def cb_delok(c: CallbackQuery, callback_data: ChCb, session: AsyncSession):
    ch = await session.get(Channel, callback_data.cid)
    if ch:
        await session.delete(ch)
        await session.flush()
    text, kb = await view(session, "🗑 Канал видалено.")
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


async def check_channel(bot: Bot, chat: Chat) -> tuple[str | None, list[str]]:
    """-> (помилка | None, попередження)."""
    if chat.type != "channel":
        return "Це не канал.", []
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
    except TelegramAPIError:
        return "Не бачу цей канал. Додайте бота в адміни й спробуйте ще раз.", []
    if me.status != "administrator":
        return "Бот не є адміністратором каналу.", []
    if not getattr(me, "can_post_messages", False):
        return "У бота немає права <b>публікувати повідомлення</b>.", []
    warns = []
    if not getattr(me, "can_delete_messages", False):
        warns.append("⚠️ Немає права видаляти повідомлення — автовидалення не працюватиме.")
    if not getattr(me, "can_pin_messages", False):
        warns.append("⚠️ Немає права закріплювати — «Закріпити» не працюватиме.")
    return None, warns


@router.message(ChS.add, not_command)
async def on_channel_input(m: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    chat: Chat | None = None
    if isinstance(m.forward_origin, MessageOriginChannel):
        chat = m.forward_origin.chat
    elif m.text:
        raw = m.text.strip()
        raw = re.sub(r"^https?://t\.me/", "@", raw)
        ref = int(raw) if re.fullmatch(r"-?\d+", raw) else raw
        if isinstance(ref, str) and not ref.startswith("@"):
            ref = "@" + ref
        with suppress(TelegramAPIError):
            chat = await bot.get_chat(ref)
    with suppress(TelegramAPIError):
        await m.delete()

    note = None
    if chat is None:
        note = "⚠️ Не вдалось визначити канал. Перешліть пост з каналу або надішліть @username."
    else:
        err, warns = await check_channel(bot, chat)
        if err:
            note = f"❌ {err}"
        else:
            existing = await session.get(Channel, chat.id)
            if existing:
                existing.title, existing.username = chat.title or str(chat.id), chat.username
            else:
                session.add(Channel(id=chat.id, title=chat.title or str(chat.id),
                                    username=chat.username, added_by=m.from_user.id))
            await session.flush()
            await state.clear()
            note = f"✅ Канал <b>{html.escape(chat.title or '')}</b> додано." + ("\n" + "\n".join(warns) if warns else "")
    if note.startswith("✅"):
        text, kb = await view(session, note)
    else:
        text = "➕ <b>Додавання каналу</b>\n\n" + note
        kb = Markup(inline_keyboard=[[cb("❌ Скасувати", "list")]])
    with suppress(TelegramAPIError):
        await bot.edit_message_text(text, chat_id=data["chat"], message_id=data["mid"], reply_markup=kb)
