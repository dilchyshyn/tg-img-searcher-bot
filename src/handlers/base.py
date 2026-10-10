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
        "Привіт! Я шукаю картинки в Google 🖼\n\n"
        f"У будь-якому чаті напиши <code>@{me.username} котики</code>, "
        "зачекай секунду й обери картинку зі списку.\n\n"
        "Або просто надішли мені текст запиту.",
        reply_markup=search_keyboard(),
    )


@router.message(F.text & ~F.text.startswith("/"))
async def text_query(message: Message) -> None:
    await message.answer(
        f"Пошук: <b>{message.html_text}</b>\nНатисни кнопку, щоб побачити картинки 👇",
        reply_markup=search_keyboard(message.text),
    )
