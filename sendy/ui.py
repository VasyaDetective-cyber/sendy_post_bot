"""Тексти й клавіатури. Жодної бізнес-логіки."""
from __future__ import annotations

import html
import re
from datetime import timezone

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup as Markup

from .config import settings
from .db import Channel, Post


class PostCb(CallbackData, prefix="p"):
    act: str
    pid: int
    val: str = ""


class ChCb(CallbackData, prefix="c"):
    act: str
    cid: int = 0


MEDIA_LABEL = {"photo": "🖼 Фото", "video": "🎬 Відео", "animation": "🎞 GIF",
               "document": "📎 Файл", "audio": "🎵 Аудіо"}
STATUS_LABEL = {"draft": "чернетка", "scheduled": "⏳ заплановано",
                "publishing": "публікується…", "published": "✅ опубліковано"}


def b(text: str, act: str, pid: int, val: str = "") -> Btn:
    return Btn(text=text, callback_data=PostCb(act=act, pid=pid, val=val).pack())


def chk(on: bool) -> str:
    return "✅" if on else "☐"


def fmt_min(m: int) -> str:
    if m % 1440 == 0:
        return f"{m // 1440} дн"
    if m % 60 == 0:
        return f"{m // 60} год"
    return f"{m} хв"


def plain(t: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", t or ""))


def excerpt(t: str | None, n: int = 70) -> str:
    s = plain(t).replace("\n", " ").strip()
    return html.escape(s[:n] + ("…" if len(s) > n else ""))


def fmt_local(dt) -> str:
    return dt.replace(tzinfo=timezone.utc).astimezone(settings.tz).strftime("%d.%m.%Y %H:%M")


# ───────────────────────── Панель поста ─────────────────────────
def panel_text(p: Post, chan_titles: list[str], note: str | None = None) -> str:
    n = len(plain(p.text))
    head = f"📝 <b>Пост #{p.id}</b> · {STATUS_LABEL.get(p.status, p.status)}"
    lines = [head]
    ex = excerpt(p.text)
    if ex:
        lines.append(f"<i>{ex}</i>")
    content = [MEDIA_LABEL[p.media_type]] if p.media_type in MEDIA_LABEL else []
    content.append(f"{n} симв.")
    lines.append("📄 " + " · ".join(content))
    lines.append("📡 " + (", ".join(html.escape(t) for t in chan_titles) if chan_titles
                         else "⚠️ канали не обрано (Далі >> → 📡 Канали)"))
    flags = []
    if p.silent: flags.append("🔕 без звуку")
    if p.buttons: flags.append(f"🔘 кнопок: {sum(len(r) for r in p.buttons)}")
    if p.pin: flags.append("📌 закріп")
    if p.protect: flags.append("🔒 захист")
    if p.is_ad: flags.append("🏷 реклама")
    if p.delete_after_min: flags.append(f"⏰ видалити через {fmt_min(p.delete_after_min)}")
    if p.reply_to: flags.append("🗣 відповідь")
    if p.repeat_every_min and p.repeat_left > 0:
        flags.append(f"🔄 кожні {fmt_min(p.repeat_every_min)} ×{p.repeat_left}")
    if p.publish_at and p.status == "scheduled":
        flags.append(f"🗓 {fmt_local(p.publish_at)}")
    if flags:
        lines.append(" · ".join(flags))
    if p.media_type and n > 1024:
        lines.append("⚠️ Підпис до медіа — максимум 1024 символи.")
    if not p.text and not p.media_type:
        lines.append("⚠️ Пост порожній.")
    if note:
        lines.append("\n" + note)
    return "\n".join(lines)


def panel_kb(p: Post, screen: str) -> Markup:
    pid = p.id
    if screen in ("1", "2"):
        nb = sum(len(r) for r in p.buttons or [])
        rows = [
            [b("✏️ Змінити текст / медіа", "edit", pid)],
            [b("🔕 Без звуку" if p.silent else "🔔 Зі звуком", "tgl", pid, "silent"),
             b(f"🔘 URL-кнопки{f' ({nb})' if nb else ''}", "btn", pid)],
            [b("👁 Прев'ю", "prev", pid), b("🔗 Заміна посилань", "lnk", pid)],
        ]
        if screen == "2":
            rows.append([b(f"{chk(p.pin)} Закріпити", "tgl", pid, "pin"),
                         b(f"{chk(p.protect)} Захист контенту", "tgl", pid, "protect")])
            rows.append([b("➖ Менше налаштувань", "scr", pid, "1")])
        else:
            rows.append([b("➕ Більше налаштувань", "scr", pid, "2")])
        rows.append([b("🗑 Видалити", "del", pid), b("Далі >>", "scr", pid, "3")])
        return Markup(inline_keyboard=rows)

    # screen 3 — публікація
    ad = f"⏰ Автовидалення: {fmt_min(p.delete_after_min)}" if p.delete_after_min else "⏰ Автовидалення"
    rp = "🗣 Пост-відповідь ✅" if p.reply_to else "🗣 Пост-відповідь"
    rep = (f"🔄 Автоповтор ×{p.repeat_left}" if p.repeat_every_min and p.repeat_left > 0 else "🔄 Автоповтор")
    return Markup(inline_keyboard=[
        [b(ad, "ad", pid), b(rp, "rp", pid)],
        [b(rep, "rep", pid), b(f"{chk(p.is_ad)} 🏷 Реклама", "tgl", pid, "is_ad")],
        [b(f"📡 Канали ({len(p.channel_ids or [])})", "chs", pid), b("📑 Копіювати", "copy", pid)],
        [b("🗓 Відкласти", "sched", pid), b("📢 Опублікувати", "pub", pid)],
        [b("<< Назад", "scr", pid, "1")],
    ])


def cancel_kb(pid: int, extra: Btn | None = None, back: str = "1") -> Markup:
    rows = [[extra]] if extra else []
    rows.append([b("❌ Скасувати", "scr", pid, back)])
    return Markup(inline_keyboard=rows)


def channels_kb(p: Post, chans: list[Channel]) -> Markup:
    sel = set(p.channel_ids or [])
    rows = [[b(f"{chk(c.id in sel)} {c.title}"[:60], "ch", p.id, str(c.id))] for c in chans]
    if chans:
        rows.append([b("☑️ Усі / жодного", "cha", p.id)])
    rows.append([b("✔️ Готово", "scr", p.id, "3")])
    return Markup(inline_keyboard=rows)


def autodel_kb(p: Post) -> Markup:
    presets = [60, 180, 360, 720, 1440, 2880, 10080]
    btns = [b(("✅ " if p.delete_after_min == m else "") + fmt_min(m), "adv", p.id, str(m)) for m in presets]
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    rows.append([b("✍️ Своє значення", "adc", p.id), b("🚫 Вимкнути", "adv", p.id, "0")])
    rows.append([b("<< Назад", "scr", p.id, "3")])
    return Markup(inline_keyboard=rows)


def repeat_kb(p: Post) -> Markup:
    iv = [360, 720, 1440, 4320, 10080]
    rows = [[b(fmt_min(m), "repi", p.id, str(m)) for m in iv],
            [b("🚫 Вимкнути", "repx", p.id)], [b("<< Назад", "scr", p.id, "3")]]
    return Markup(inline_keyboard=rows)


def repeat_count_kb(p: Post) -> Markup:
    rows = [[b(f"×{n}", "repc", p.id, str(n)) for n in (2, 3, 5, 10, 30)],
            [b("<< Назад", "rep", p.id)]]
    return Markup(inline_keyboard=rows)


def after_kb(p: Post) -> Markup:
    rows = []
    if p.status == "scheduled":
        rows.append([b("✏️ Редагувати", "open", p.id), b("❌ Зняти з черги", "unsched", p.id)])
    rows.append([b("📑 Копіювати як новий", "copy", p.id)])
    return Markup(inline_keyboard=rows)
