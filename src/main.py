import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.config import load_config
from src.handlers import base, inline
from src.services.search import ImageSearch


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    search = ImageSearch(config)

    # config і search автоматично передаються в хендлери за назвою аргументу
    dp = Dispatcher(config=config, search=search)
    dp.include_routers(inline.router, base.router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await search.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
