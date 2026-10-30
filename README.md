# Sendy — Telegram-бот для публікації постів у канали

Aiogram 3 · SQLAlchemy (SQLite/PostgreSQL) · APScheduler

## Запуск
```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # впишіть BOT_TOKEN і SUPER_ADMIN_ID
python main.py
```
Docker: `docker build -t sendy . && docker run -d --env-file .env -v $PWD/data:/app/data sendy`

## Як користуватись
1. `/channels` → «➕ Додати канал» → перешліть пост з каналу (бот — адмін каналу з правом публікації).
2. Надішліть боту текст/медіа → з'явиться панель поста (редагується на місці).
3. **Далі >>** → канали, автовидалення, відповідь, автоповтор, реклама → **📢 Опублікувати** або **🗓 Відкласти**.
4. `/queue` — заплановані пости (редагувати / зняти з черги).
5. Власник: `/add_admin ID`, `/del_admin ID`, `/admins`.

## Структура
```
main.py              запуск
sendy/config.py      налаштування з .env
sendy/db.py          моделі (users, channels, posts, deliveries)
sendy/middleware.py  white-list
sendy/ui.py          тексти й клавіатури
sendy/services.py    відправка, публікація, парсинг
sendy/scheduler.py   відкладені пости + автовидалення
sendy/handlers/      admin.py, channels.py, post.py
```

## Важливо
- Premium-емодзі зберігаються як `<tg-emoji>`. За документацією Telegram, боти можуть відправляти custom emoji лише якщо на боті куплено додатковий username на Fragment — це обмеження Telegram, не коду.
- FSM у пам'яті: після рестарту незавершений крок введення скидається, але чернетки й черга зберігаються в БД.
