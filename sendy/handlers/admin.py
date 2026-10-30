from contextlib import suppress

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import User

ADMIN_COMMANDS = [
    BotCommand(command="channels", description="📡 Канали"),
    BotCommand(command="queue", description="⏳ Заплановані пости"),
    BotCommand(command="help", description="❓ Довідка"),
    BotCommand(command="admins", description="👑 Адміни"),
]

router = Router(name="admin")

HELP = (
    "👋 <b>Sendy</b> — ваш помічник для постів у каналах.\n\n"
    "<b>Як користуватись</b>\n"
    "1️⃣ /channels → додайте канал (бот має бути адміном каналу з правом публікації)\n"
    "2️⃣ Просто надішліть мені текст або фото/відео/файл — з'явиться панель поста\n"
    "3️⃣ Налаштуйте → <b>Далі >></b> → <b>📢 Опублікувати</b> або <b>🗓 Відкласти</b>\n\n"
    "<b>Команди</b>\n"
    "/channels — канали\n"
    "/queue — заплановані пости\n"
    "/help — ця довідка"
)
HELP_SUPER = "\n\n<b>Лише власник</b>\n/admins — список адмінів\n/add_admin ID\n/del_admin ID"

START_KB = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ Створити пост", callback_data="new_post")]])


@router.message(Command("start", "help"))
async def cmd_start(m: Message, role: str, state: FSMContext, bot: Bot):
    await state.clear()
    if role == "superadmin":
        # Персональне меню команд може виставитись лише після того, як чат із власником вже існує.
        with suppress(TelegramAPIError):
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=m.from_user.id))
    await m.answer(HELP + (HELP_SUPER if role == "superadmin" else ""), reply_markup=START_KB)


@router.message(Command("admins"))
async def cmd_admins(m: Message, role: str, session: AsyncSession):
    if role != "superadmin":
        return await m.answer("⛔ Лише для власника.")
    users = (await session.scalars(select(User).order_by(User.created_at))).all()
    lines = [f"{'👑' if u.role == 'superadmin' else '👤'} <code>{u.id}</code>" for u in users]
    await m.answer("<b>Доступ до бота:</b>\n" + "\n".join(lines))


@router.message(Command("add_admin"))
async def cmd_add_admin(m: Message, command: CommandObject, role: str, session: AsyncSession, bot: Bot):
    if role != "superadmin":
        return await m.answer("⛔ Лише для власника.")
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        return await m.answer("Використання: <code>/add_admin 123456789</code>")
    uid = int(arg)
    if await session.get(User, uid):
        return await m.answer("Цей користувач уже має доступ.")
    session.add(User(id=uid, role="admin", added_by=m.from_user.id))
    await m.answer(f"✅ <code>{uid}</code> тепер адмін.")
    try:
        await bot.send_message(uid, "✅ Вас додано до Sendy. Натисніть /start")
    except TelegramAPIError:
        await m.answer("ℹ️ Він(вона) ще не запускав(ла) бота — хай натисне /start.")


@router.message(Command("del_admin"))
async def cmd_del_admin(m: Message, command: CommandObject, role: str, session: AsyncSession):
    if role != "superadmin":
        return await m.answer("⛔ Лише для власника.")
    arg = (command.args or "").strip()
    if not arg.lstrip("-").isdigit():
        return await m.answer("Використання: <code>/del_admin 123456789</code>")
    user = await session.get(User, int(arg))
    if not user:
        return await m.answer("Такого адміна немає.")
    if user.role == "superadmin":
        return await m.answer("Власника видалити не можна.")
    await session.delete(user)
    await m.answer(f"🗑 <code>{arg}</code> більше не має доступу.")