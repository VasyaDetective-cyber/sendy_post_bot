from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker

from .db import User


class AccessMiddleware(BaseMiddleware):
    """White-list: чужих ігноруємо мовчки. Додає у handler `session` та `role`."""

    def __init__(self, sm: async_sessionmaker):
        self.sm = sm

    async def __call__(self, handler, event, data):
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return None
        async with self.sm() as session:
            user = await session.get(User, tg_user.id)
            if user is None:
                return None
            data["session"] = session
            data["role"] = user.role
            result = await handler(event, data)
            await session.commit()
            return result
