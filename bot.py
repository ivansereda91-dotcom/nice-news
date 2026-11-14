"""Новости Ниццы и окрестностей: утренняя сводка каждый день, афиша на выходные по четвергам."""
import asyncio
import html
import logging
import os
from datetime import date, datetime, time, timedelta

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, TelegramObject

import db
import news

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TOKEN = os.environ["BOT_TOKEN"]
ALLOWED = [int(x) for x in os.getenv("ALLOWED_IDS", "").replace(" ", "").split(",") if x]
NEWS_TIME = time.fromisoformat(os.getenv("NEWS_TIME", "08:00"))
AFISHA_DAY = int(os.getenv("AFISHA_DAY", "3"))  # 0 = понедельник, 3 = четверг
AFISHA_TIME = time.fromisoformat(os.getenv("AFISHA_TIME", "18:00"))
GRACE = timedelta(hours=4)  # если сервер был выключен, отправим позже, но не позднее этого

router = Router()
e = html.escape
LOCK = asyncio.Lock()


class Access(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        u = data.get("event_from_user")
        if u is None:
            return
        if u.id not in ALLOWED:
            if isinstance(event, Message):
                await event.answer(f"Это частный бот. Твой Telegram ID: <code>{u.id}</code>")
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return
        return await handler(event, data)


# ---------- оформление ----------

def item_html(it: dict, extra: str = "") -> str:
    title = e(str(it.get("title", "")).strip())
    if it.get("url"):
        title = f'<a href="{e(it["url"], quote=True)}">{title}</a>'
    text = e(str(it.get("text", "")).strip())
    return f"• <b>{title}</b>{extra}\n{text}" if text else f"• <b>{title}</b>{extra}"


def render_news(d: dict, day: date, weather: str | None) -> list[str]:
    parts = [f"🗞 <b>Ницца, {e(news.ru_date(day))}</b>"]
    if weather:
        parts.append(f"🌤 <b>Погода:</b> {e(weather)}")
    sections = [("alerts", "⚠️ Внимание"), ("city", "🏙 Город и транспорт"),
                ("culture", "🎭 Культура"), ("sport", "⚽ Спорт")]
    any_items = False
    for key, name in sections:
        items = d.get(key) or []
        if items:
            any_items = True
            parts.append(f"<b>{name}</b>\n" + "\n\n".join(item_html(it) for it in items))
    if not any_items:
        parts.append("Свежих заметных новостей за сутки не нашлось.")
    return split_messages(parts)


def render_afisha(d: dict) -> list[str]:
    fri, sun = d["fri"], d["sun"]
    fri = date.fromisoformat(fri) if isinstance(fri, str) else fri
    sun = date.fromisoformat(sun) if isinstance(sun, str) else sun
    parts = [f"🎟 <b>Афиша на выходные: {fri.day}–{sun.day} {news.MONTHS[sun.month - 1]}</b>"]

    def ev(it):
        meta = " · ".join(e(str(it.get(k, "")).strip()) for k in ("when", "where", "price") if str(it.get(k, "")).strip())
        return item_html(it, f"\n<i>{meta}</i>" if meta else "")

    if d.get("weekend"):
        parts += [ev(it) for it in d["weekend"]]
    else:
        parts.append("Событий не нашлось.")
    if d.get("next_week"):
        parts.append("<b>📅 На следующей неделе</b>\n" + "\n\n".join(ev(it) for it in d["next_week"]))
    return split_messages(parts)


def split_messages(parts: list[str], limit: int = 3800) -> list[str]:
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 2 > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


# ---------- сборка (с кешем: оба получают одну и ту же сводку) ----------

async def get_news(day: date) -> list[str]:
    async with LOCK:
        d = db.get_digest("news", day)
        if d is None:
            d = await news.morning_news(day, db.seen_titles())
            d["weather"] = await asyncio.to_thread(news.weather_today)
            db.save_digest("news", day, d)
            db.add_seen([it["title"] for k in ("alerts", "city", "culture", "sport") for it in d.get(k, [])], day)
    return render_news(d, day, d.get("weather"))


def week_key(day: date) -> date:
    return day - timedelta(days=day.weekday())  # понедельник этой недели


async def get_afisha(day: date) -> list[str]:
    async with LOCK:
        d = db.get_digest("afisha", week_key(day))
        if d is None:
            d = await news.weekend_afisha(day)
            db.save_digest("afisha", week_key(day), d)
    return render_afisha(d)


async def send_all(bot: Bot, msgs: list[str], to: list[int]):
    for uid in to:
        for m in msgs:
            try:
                await bot.send_message(uid, m, disable_web_page_preview=True)
            except Exception as ex:  # noqa: BLE001
                logging.warning("Не отправлено %s: %s", uid, ex)


# ---------- команды ----------

@router.message(CommandStart())
@router.message(Command("help"))
async def start(m: Message):
    await m.answer(
        "Привет! Я присылаю новости Ниццы и окрестностей.\n\n"
        f"🗞 Каждый день в {NEWS_TIME:%H:%M} — утренняя сводка: погода, тревоги, город и транспорт, культура, спорт.\n"
        f"🎟 По четвергам в {AFISHA_TIME:%H:%M} — афиша на выходные.\n\n"
        "/news — сводка за сегодня\n/afisha — афиша на ближайшие выходные")


@router.message(Command("news"))
async def cmd_news(m: Message):
    wait = await m.answer("Собираю новости… это займёт до минуты.")
    try:
        msgs = await get_news(db.today())
    except Exception:  # noqa: BLE001
        logging.exception("Ошибка сводки")
        await wait.edit_text("Не получилось собрать новости. Попробуй позже.")
        return
    await wait.delete()
    await send_all(m.bot, msgs, [m.chat.id])


@router.message(Command("afisha"))
async def cmd_afisha(m: Message):
    wait = await m.answer("Собираю афишу… это займёт до минуты.")
    try:
        msgs = await get_afisha(db.today())
    except Exception:  # noqa: BLE001
        logging.exception("Ошибка афиши")
        await wait.edit_text("Не получилось собрать афишу. Попробуй позже.")
        return
    await wait.delete()
    await send_all(m.bot, msgs, [m.chat.id])


# ---------- расписание ----------

def due(at: datetime, t: time) -> bool:
    start = datetime.combine(at.date(), t, tzinfo=db.TZ)
    return start <= at <= start + GRACE


async def run_job(bot: Bot, kind: str, day: date, make):
    if not db.mark_sent(kind, day):
        return
    for attempt in range(3):
        try:
            msgs = await make(day)
            await send_all(bot, msgs, ALLOWED)
            return
        except Exception:  # noqa: BLE001
            logging.exception("Ошибка %s, попытка %s", kind, attempt + 1)
            await asyncio.sleep(600)
    if ALLOWED:  # больше не повторяем автоматически: можно запросить вручную /news или /afisha
        try:
            await bot.send_message(ALLOWED[0], f"⚠️ Не удалось собрать {'новости' if kind == 'news' else 'афишу'}. "
                                               "Подробности: journalctl -u nice-news -n 30")
        except Exception:  # noqa: BLE001
            pass


async def scheduler(bot: Bot):
    while True:
        try:
            at = db.now()
            if due(at, NEWS_TIME):
                await run_job(bot, "news", at.date(), get_news)
            if at.weekday() == AFISHA_DAY and due(at, AFISHA_TIME):
                await run_job(bot, "afisha", at.date(), get_afisha)
        except Exception:  # noqa: BLE001
            logging.exception("Ошибка планировщика")
        await asyncio.sleep(30)


async def main():
    db.init()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.outer_middleware(Access())
    dp.callback_query.outer_middleware(Access())
    dp.include_router(router)
    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
