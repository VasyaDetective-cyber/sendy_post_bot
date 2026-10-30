import html
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup as Markup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import services as svc
from ..config import settings
from ..db import Channel, Post, utcnow
from ..ui import (PostCb, after_kb, autodel_kb, b, cancel_kb, channels_kb, excerpt, fmt_local, fmt_min,
                  panel_kb, panel_text, repeat_count_kb, repeat_kb)

router = Router(name="post")

CONTENT = {ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION,
           ContentType.DOCUMENT, ContentType.AUDIO}
_seen_albums: set[str] = set()


class S(StatesGroup):
    new = State()
    content = State()
    buttons = State()
    links = State()
    delay = State()
    reply = State()
    schedule = State()


def not_command(m: Message) -> bool:
    return not (m.text or "").startswith("/")


# ───────────────────── допоміжне ─────────────────────
async def edit_or_send(bot: Bot, p: Post, text: str, kb: Markup | None) -> None:
    if p.panel_msg_id:
        try:
            await bot.edit_message_text(text, chat_id=p.panel_chat_id, message_id=p.panel_msg_id, reply_markup=kb)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
    m = await bot.send_message(p.owner_id, text, reply_markup=kb)
    p.panel_chat_id, p.panel_msg_id = m.chat.id, m.message_id


async def show_panel(bot: Bot, session: AsyncSession, p: Post, screen: str = "1", note: str | None = None) -> None:
    titles = []
    if p.channel_ids:
        rows = (await session.scalars(select(Channel).where(Channel.id.in_(p.channel_ids)))).all()
        titles = [c.title for c in rows]
    await edit_or_send(bot, p, panel_text(p, titles, note), panel_kb(p, screen))


async def prompt(bot: Bot, p: Post, text: str, kb: Markup) -> None:
    await edit_or_send(bot, p, text, kb)


async def show_channels(bot: Bot, session: AsyncSession, p: Post) -> None:
    chans = (await session.scalars(select(Channel).order_by(Channel.title))).all()
    text = ("📡 <b>Куди публікуємо?</b>\nОберіть один або кілька каналів:" if chans
            else "📡 Каналів ще немає. Додайте їх командою /channels, потім поверніться до поста.")
    await edit_or_send(bot, p, text, channels_kb(p, chans))


async def load(c: CallbackQuery, session: AsyncSession, pid: int, need_draft: bool = True) -> Post | None:
    p = await session.get(Post, pid)
    if not p or p.owner_id != c.from_user.id:
        await c.answer("Пост не знайдено", show_alert=True)
        return None
    if need_draft and p.status != "draft":
        await c.answer("Цей пост уже заплановано або опубліковано. Скористайтесь «Копіювати».", show_alert=True)
        return None
    if c.message:
        p.panel_chat_id, p.panel_msg_id = c.message.chat.id, c.message.message_id
    return p


async def fsm_post(m: Message, state: FSMContext, session: AsyncSession) -> Post | None:
    pid = (await state.get_data()).get("pid")
    p = await session.get(Post, pid) if pid else None
    if not p or p.owner_id != m.from_user.id or p.status != "draft":
        await state.clear()
        return None
    return p


async def drop(m: Message) -> None:
    with suppress(TelegramAPIError):
        await m.delete()


async def begin(state: FSMContext, st: State, p: Post) -> None:
    await state.set_state(st)
    await state.update_data(pid=p.id)


# ───────────────────── новий пост ─────────────────────
@router.callback_query(F.data == "new_post")
async def cb_new_post(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.new)
    await c.message.answer(
        "✏️ <b>Надішліть текст або медіа</b> (фото, відео, GIF, файл, аудіо) для нового поста.",
                        reply_markup=Markup(inline_keyboard=[[InlineKeyboardButton(text="❌ Скасувати", callback_data="new_post_x")]]))
    await c.answer()


@router.callback_query(F.data == "new_post_x")
async def cb_new_post_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Скасовано. Щоб почати знову — /start або просто надішліть текст/медіа.")
    await c.answer()


async def create_post(m: Message, session: AsyncSession, bot: Bot) -> None:
    if m.media_group_id:
        if m.media_group_id not in _seen_albums:
            if len(_seen_albums) > 500:
                _seen_albums.clear()
            _seen_albums.add(m.media_group_id)
            await m.answer("📚 Альбоми поки не підтримуються — надсилайте медіа по одному.")
        return
    text, mtype, fid = svc.extract_content(m)
    existing = set((await session.scalars(select(Channel.id))).all())
    last = await session.scalar(select(Post).where(Post.owner_id == m.from_user.id).order_by(Post.id.desc()).limit(1))
    ids = [i for i in (last.channel_ids if last else []) if i in existing]
    if not ids and len(existing) == 1:
        ids = list(existing)
    p = Post(owner_id=m.from_user.id, text=text, media_type=mtype, media_id=fid, channel_ids=ids, buttons=[])
    session.add(p)
    await session.flush()
    panel = await m.answer("⏳")
    p.panel_chat_id, p.panel_msg_id = panel.chat.id, panel.message_id
    note = None if existing else "ℹ️ Спершу додайте канал: /channels"
    await show_panel(bot, session, p, "1", note)
    await drop(m)


@router.message(StateFilter(None), F.content_type.in_(CONTENT), not_command)
async def on_new_post(m: Message, session: AsyncSession, bot: Bot):
    await create_post(m, session, bot)


@router.message(S.new, F.content_type.in_(CONTENT), not_command)
async def on_new_post_via_button(m: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    await state.clear()
    await create_post(m, session, bot)


# ───────────────────── екрани / перемикачі ─────────────────────
@router.callback_query(PostCb.filter(F.act == "scr"))
async def cb_screen(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    await state.clear()
    p = await load(c, session, callback_data.pid)
    if p:
        await show_panel(bot, session, p, callback_data.val or "1")
        await c.answer()


TOGGLE_SCREEN = {"silent": "1", "pin": "2", "protect": "2", "is_ad": "3"}


@router.callback_query(PostCb.filter(F.act == "tgl"))
async def cb_toggle(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    field = callback_data.val
    p = await load(c, session, callback_data.pid)
    if p and field in TOGGLE_SCREEN:
        setattr(p, field, not getattr(p, field))
        await show_panel(bot, session, p, TOGGLE_SCREEN[field])
    await c.answer()


# ───────────────────── текст / медіа ─────────────────────
@router.callback_query(PostCb.filter(F.act == "edit"))
async def cb_edit(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    p = await load(c, session, callback_data.pid)
    if not p:
        return
    await begin(state, S.content, p)
    await prompt(bot, p, "✏️ <b>Надішліть новий текст або медіа</b> (фото, відео, GIF, файл, аудіо).\n"
                         "Форматування й premium-емодзі збережуться.", cancel_kb(p.id))
    await c.answer()


@router.message(S.content, F.content_type.in_(CONTENT), not_command)
async def on_content(m: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    p = await fsm_post(m, state, session)
    if p:
        p.text, p.media_type, p.media_id = svc.extract_content(m)
        await state.clear()
        await show_panel(bot, session, p, "1", "✅ Контент оновлено")
    await drop(m)


# ───────────────────── URL-кнопки ─────────────────────
@router.callback_query(PostCb.filter(F.act == "btn"))
async def cb_btn(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    p = await load(c, session, callback_data.pid)
    if not p:
        return
    await begin(state, S.buttons, p)
    extra = b("🗑 Очистити кнопки", "btnx", p.id) if p.buttons else None
    await prompt(bot, p, "🔘 <b>Надішліть кнопки</b>, кожен рядок — окремий ряд:\n\n"
                         "<code>Сайт - https://example.com\n"
                         "Telegram - t.me/durov | Чат - t.me/chat</code>\n\n"
                         "Кілька кнопок в одному ряду розділяйте символом <code>|</code>.",
                 cancel_kb(p.id, extra))
    await c.answer()


@router.callback_query(PostCb.filter(F.act == "btnx"))
async def cb_btn_clear(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    await state.clear()
    p = await load(c, session, callback_data.pid)
    if p:
        p.buttons = []
        await show_panel(bot, session, p, "1", "🗑 Кнопки очищено")
        await c.answer()


@router.message(S.buttons, F.text, not_command)
async def on_buttons(m: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    p = await fsm_post(m, state, session)
    if p:
        try:
            p.buttons = svc.parse_buttons(m.text)
        except ValueError as e:
            await prompt(bot, p, f"⚠️ {html.escape(str(e))}\n\nСпробуйте ще раз у форматі:\n<code>Текст - https://посилання</code>",
                         cancel_kb(p.id))
        else:
            await state.clear()
            await show_panel(bot, session, p, "1", "✅ Кнопки збережено")
    await drop(m)


# ───────────────────── заміна посилань ─────────────────────
@router.callback_query(PostCb.filter(F.act == "lnk"))
async def cb_links(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    p = await load(c, session, callback_data.pid)
    if not p:
        return
    links = svc.find_links(p.text) + [x["url"] for r in p.buttons or [] for x in r]
    links = list(dict.fromkeys(links))
    if not links:
        return await c.answer("У пості немає гіперпосилань.", show_alert=True)
    await begin(state, S.links, p)
    listing = "\n".join(f"• <code>{html.escape(u[:80])}</code>" for u in links[:10])
    await prompt(bot, p, f"🔗 <b>Знайдені посилання:</b>\n{listing}\n\n"
                         "Надішліть <code>старе => нове</code>.\n"
                         "Якщо посилання одне — достатньо надіслати лише нове.", cancel_kb(p.id))
    await c.answer()


@router.message(S.links, F.text, not_command)
async def on_links(m: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    p = await fsm_post(m, state, session)
    if p:
        raw = m.text.strip()
        links = list(dict.fromkeys(svc.find_links(p.text) + [x["url"] for r in p.buttons or [] for x in r]))
        if "=>" in raw:
            old, new = (x.strip() for x in raw.split("=>", 1))
        elif len(links) == 1:
            old, new = links[0], raw
        else:
            old = new = ""
        n = svc.replace_links(p, old, new) if old and new else 0
        if n:
            await state.clear()
            await show_panel(bot, session, p, "1", f"✅ Замінено: {n}")
        else:
            await prompt(bot, p, "⚠️ Не знайшов таке посилання. Надішліть у форматі <code>старе => нове</code>.",
                         cancel_kb(p.id))
    await drop(m)


# ───────────────────── прев'ю ─────────────────────
@router.callback_query(PostCb.filter(F.act == "prev"))
async def cb_preview(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if not p:
        return
    if not p.text and not p.media_type:
        return await c.answer("Пост порожній", show_alert=True)
    try:
        await svc.send_post(bot, c.from_user.id, p)
        await c.answer("👁 Прев'ю надіслано")
    except TelegramAPIError as e:
        await c.answer(f"Помилка: {str(e)[:150]}", show_alert=True)


# ───────────────────── автовидалення ─────────────────────
@router.callback_query(PostCb.filter(F.act == "ad"))
async def cb_ad(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        await edit_or_send(bot, p, "⏰ <b>Через скільки видалити пост із каналу?</b>", autodel_kb(p))
        await c.answer()


@router.callback_query(PostCb.filter(F.act == "adv"))
async def cb_ad_set(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        p.delete_after_min = int(callback_data.val) or None
        await show_panel(bot, session, p, "3")
        await c.answer()


@router.callback_query(PostCb.filter(F.act == "adc"))
async def cb_ad_custom(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    p = await load(c, session, callback_data.pid)
    if p:
        await begin(state, S.delay, p)
        await prompt(bot, p, "⏰ Надішліть час: <code>90m</code>, <code>5h</code> або <code>3d</code>\n"
                             "(хв / год / дні; без літери = години; максимум 30 днів).", cancel_kb(p.id, back="3"))
        await c.answer()


@router.message(S.delay, F.text, not_command)
async def on_delay(m: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    p = await fsm_post(m, state, session)
    if p:
        minutes = svc.parse_delay(m.text)
        if minutes:
            p.delete_after_min = minutes
            await state.clear()
            await show_panel(bot, session, p, "3", f"✅ Автовидалення: {fmt_min(minutes)}")
        else:
            await prompt(bot, p, "⚠️ Не зрозумів. Приклад: <code>90m</code>, <code>5h</code>, <code>3d</code>",
                         cancel_kb(p.id, back="3"))
    await drop(m)


# ───────────────────── пост-відповідь ─────────────────────
@router.callback_query(PostCb.filter(F.act == "rp"))
async def cb_reply(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    p = await load(c, session, callback_data.pid)
    if not p:
        return
    await begin(state, S.reply, p)
    extra = b("🚫 Прибрати відповідь", "rpx", p.id) if p.reply_to else None
    await prompt(bot, p, "🗣 <b>Надішліть посилання на пост у каналі</b>, на який відповідаємо\n"
                         "(у каналі: пост → «Копіювати посилання»), наприклад <code>https://t.me/mychannel/15</code>\n\n"
                         "ℹ️ Працює для того каналу, якому належить посилання.", cancel_kb(p.id, extra, back="3"))
    await c.answer()


@router.callback_query(PostCb.filter(F.act == "rpx"))
async def cb_reply_clear(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    await state.clear()
    p = await load(c, session, callback_data.pid)
    if p:
        p.reply_to = None
        await show_panel(bot, session, p, "3", "🚫 Відповідь прибрано")
        await c.answer()


@router.message(S.reply, F.text, not_command)
async def on_reply(m: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    p = await fsm_post(m, state, session)
    if p:
        link = svc.parse_msg_link(m.text)
        if link:
            p.reply_to = link
            await state.clear()
            await show_panel(bot, session, p, "3", "✅ Пост буде опубліковано як відповідь")
        else:
            await prompt(bot, p, "⚠️ Це не схоже на посилання на пост. Приклад: <code>https://t.me/mychannel/15</code>",
                         cancel_kb(p.id, back="3"))
    await drop(m)


# ───────────────────── автоповтор ─────────────────────
@router.callback_query(PostCb.filter(F.act == "rep"))
async def cb_rep(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        await edit_or_send(bot, p, "🔄 <b>Автоповтор</b>\nЯк часто повторювати публікацію?", repeat_kb(p))
        await c.answer()


@router.callback_query(PostCb.filter(F.act == "repi"))
async def cb_rep_interval(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        p.repeat_every_min = int(callback_data.val)
        await edit_or_send(bot, p, f"🔄 Кожні <b>{fmt_min(p.repeat_every_min)}</b>.\nСкільки разів повторити <i>після</i> першої публікації?",
                           repeat_count_kb(p))
        await c.answer()


@router.callback_query(PostCb.filter(F.act == "repc"))
async def cb_rep_count(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        p.repeat_left = int(callback_data.val)
        await show_panel(bot, session, p, "3")
        await c.answer()


@router.callback_query(PostCb.filter(F.act == "repx"))
async def cb_rep_off(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        p.repeat_every_min, p.repeat_left = None, 0
        await show_panel(bot, session, p, "3")
        await c.answer()


# ───────────────────── канали ─────────────────────
@router.callback_query(PostCb.filter(F.act == "chs"))
async def cb_channels(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        await show_channels(bot, session, p)
        await c.answer()


@router.callback_query(PostCb.filter(F.act == "ch"))
async def cb_channel_toggle(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        cid = int(callback_data.val)
        ids = list(p.channel_ids or [])
        ids.remove(cid) if cid in ids else ids.append(cid)
        p.channel_ids = ids
        await show_channels(bot, session, p)
        await c.answer()


@router.callback_query(PostCb.filter(F.act == "cha"))
async def cb_channel_all(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        all_ids = list((await session.scalars(select(Channel.id))).all())
        p.channel_ids = [] if set(p.channel_ids or []) == set(all_ids) else all_ids
        await show_channels(bot, session, p)
        await c.answer()


# ───────────────────── копіювати / видалити ─────────────────────
@router.callback_query(PostCb.filter(F.act == "copy"))
async def cb_copy(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid, need_draft=False)
    if not p:
        return
    new = Post(owner_id=p.owner_id, text=p.text, media_type=p.media_type, media_id=p.media_id,
               buttons=[list(r) for r in p.buttons or []], silent=p.silent, pin=p.pin, protect=p.protect,
               is_ad=p.is_ad, delete_after_min=p.delete_after_min, reply_to=p.reply_to,
               repeat_every_min=p.repeat_every_min, repeat_left=max(p.repeat_left, 0),
               channel_ids=list(p.channel_ids or []))
    session.add(new)
    await session.flush()
    panel = await c.message.answer("⏳")
    new.panel_chat_id, new.panel_msg_id = panel.chat.id, panel.message_id
    await show_panel(bot, session, new, "1", f"📑 Копія поста #{p.id}")
    await c.answer("📑 Копію створено")


@router.callback_query(PostCb.filter(F.act == "del"))
async def cb_del(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if p:
        await edit_or_send(bot, p, f"🗑 Видалити чернетку #{p.id}?", Markup(inline_keyboard=[
            [b("✅ Так, видалити", "delok", p.id), b("❌ Ні", "scr", p.id, "1")]]))
        await c.answer()


@router.callback_query(PostCb.filter(F.act == "delok"))
async def cb_del_ok(c: CallbackQuery, callback_data: PostCb, session: AsyncSession):
    p = await load(c, session, callback_data.pid)
    if p:
        await session.delete(p)
        await c.message.edit_text("🗑 Чернетку видалено. Надішліть новий текст або медіа, щоб створити пост.")
        await c.answer()


# ───────────────────── публікація ─────────────────────
def validate(p: Post) -> str | None:
    if not p.text and not p.media_type:
        return "Пост порожній"
    if p.media_type and len(svc_plain(p)) > 1024:
        return "Підпис до медіа довший за 1024 символи"
    return None


def svc_plain(p: Post) -> str:
    from ..ui import plain
    return plain(p.text)


@router.callback_query(PostCb.filter(F.act == "pub"))
async def cb_publish(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid)
    if not p:
        return
    if err := validate(p):
        return await c.answer(err, show_alert=True)
    existing = set((await session.scalars(select(Channel.id))).all())
    p.channel_ids = [i for i in (p.channel_ids or []) if i in existing]
    if not p.channel_ids:
        await show_channels(bot, session, p)
        return await c.answer("Оберіть, куди публікувати")
    p.status = "publishing"
    await c.answer("⏳ Публікую…")
    ok, lines = await svc.publish_post(bot, session, p)
    if ok:
        head = "📢 <b>Опубліковано</b>"
        if p.status == "scheduled":
            head += f"\n🔄 Наступний повтор: {fmt_local(p.publish_at)}"
    else:
        head = "⚠️ <b>Не вдалося опублікувати</b>"
    kb = after_kb(p) if ok else panel_kb(p, "3")
    await edit_or_send(bot, p, f"{head}\n<i>{excerpt(p.text, 50)}</i>\n\n" + "\n".join(lines), kb)


# ───────────────────── відкласти ─────────────────────
@router.callback_query(PostCb.filter(F.act == "sched"))
async def cb_sched(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot, state: FSMContext):
    p = await load(c, session, callback_data.pid)
    if not p:
        return
    if err := validate(p):
        return await c.answer(err, show_alert=True)
    if not p.channel_ids:
        await show_channels(bot, session, p)
        return await c.answer("Спершу оберіть канали")
    await begin(state, S.schedule, p)
    now = fmt_local(utcnow())
    await prompt(bot, p, "🗓 <b>Коли опублікувати?</b>\nФормат: <code>ДД.ММ.РРРР ГГ:ХХ</code>\n"
                         f"Зараз: <code>{now}</code> ({settings.timezone})", cancel_kb(p.id, back="3"))
    await c.answer()


@router.message(S.schedule, F.text, not_command)
async def on_schedule(m: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    p = await fsm_post(m, state, session)
    if p:
        when = svc.parse_local_dt(m.text)
        if when is None:
            await prompt(bot, p, "⚠️ Невірний формат. Приклад: <code>05.11.2026 18:30</code>", cancel_kb(p.id, back="3"))
        elif when <= utcnow():
            await prompt(bot, p, "⚠️ Цей час уже минув. Вкажіть майбутню дату.", cancel_kb(p.id, back="3"))
        else:
            p.publish_at, p.status = when, "scheduled"
            await state.clear()
            await edit_or_send(bot, p, f"🗓 <b>Заплановано на {fmt_local(when)}</b>\n<i>{excerpt(p.text, 50)}</i>\n"
                                       f"📡 Каналів: {len(p.channel_ids)}", after_kb(p))
    await drop(m)


@router.callback_query(PostCb.filter(F.act.in_({"unsched", "open"})))
async def cb_unsched(c: CallbackQuery, callback_data: PostCb, session: AsyncSession, bot: Bot):
    p = await load(c, session, callback_data.pid, need_draft=False)
    if not p:
        return
    if p.status != "scheduled":
        return await c.answer("Пост уже не в черзі", show_alert=True)
    p.status, p.publish_at = "draft", None
    if callback_data.act == "open":
        await show_panel(bot, session, p, "1", "✏️ Знято з черги для редагування")
    else:
        await edit_or_send(bot, p, f"❌ Знято з черги. Пост #{p.id} знову чернетка.",
                           Markup(inline_keyboard=[[b("✏️ Відкрити", "scr", p.id, "1")]]))
    await c.answer()


# ───────────────────── /queue ─────────────────────
@router.message(Command("queue"))
async def cmd_queue(m: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    posts = (await session.scalars(select(Post).where(Post.status == "scheduled", Post.owner_id == m.from_user.id)
                                   .order_by(Post.publish_at).limit(20))).all()
    if not posts:
        return await m.answer("⏳ Запланованих постів немає.")
    lines, rows = ["⏳ <b>Заплановані пости</b>"], []
    for p in posts:
        t = fmt_local(p.publish_at)
        lines.append(f"• <b>#{p.id}</b> · {t} · <i>{excerpt(p.text, 40)}</i>")
        rows.append([b(f"✏️ #{p.id} · {t[:5]} {t[11:]}", "open", p.id), b("❌", "unsched", p.id)])
    await m.answer("\n".join(lines), reply_markup=Markup(inline_keyboard=rows))
