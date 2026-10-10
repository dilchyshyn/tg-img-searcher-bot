import html

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

router = Router(name="base")
router.message.filter(F.chat.type == "private")


def search_keyboard(query: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Шукати тут", switch_inline_query_current_chat=query)],
        [InlineKeyboardButton(text="📤 Надіслати в інший чат", switch_inline_query=query)],
    ])


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message, bot: Bot) -> None:
    me = await bot.me()
    await message.answer(
        "Привіт! Я допоможу знайти картинку 🖼\n\n"
        f"У будь-якому чаті напиши:\n<code>@{me.username} &lt;опис картинки, яку хочеш знайти&gt;</code>\n"
        "і обери потрібну зі списку.\n\n"
        "Або просто надішли мені опис сюди.",
        reply_markup=search_keyboard(),
    )


@router.message(F.text & ~F.text.startswith("/"))
async def text_query(message: Message, bot: Bot) -> None:
    me = await bot.me()
    # Прибираємо згадку бота, якщо запит надіслали як "@бот котики"
    query = message.text.strip()
    mention = f"@{me.username}"
    if query.lower().startswith(mention.lower()):
        query = query[len(mention):].strip()
    if not query:
        await message.answer("Напиши опис картинки, яку хочеш знайти 🙂")
        return

    await message.answer(
        f"Пошук: <b>{html.escape(query)}</b>\nНатисни кнопку, щоб побачити картинки 👇",
        reply_markup=search_keyboard(query),
    )
