"""Сбор новостей и афиши: Claude с веб-поиском + прогноз погоды Open-Meteo."""
import json

import json_repair
import logging
import os
import re
import urllib.request
from datetime import date, timedelta

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
MAX_SEARCHES = int(os.getenv("MAX_SEARCHES", "8"))
LOCATION = {"type": "approximate", "city": "Nice", "region": "Provence-Alpes-Côte d'Azur",
            "country": "FR", "timezone": "Europe/Paris"}

DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def ru_date(d: date) -> str:
    return f"{DAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]} {d.year}"


_client = None


def client():
    global _client
    if _client is None:
        from anthropic import AsyncAnthropic
        _client = AsyncAnthropic()  # ключ из ANTHROPIC_API_KEY
    return _client


# ---------- погода (без ключей) ----------

WMO = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "туман",
       51: "морось", 53: "морось", 55: "морось", 61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
       66: "ледяной дождь", 67: "ледяной дождь", 71: "снег", 73: "снег", 75: "сильный снег", 80: "ливни",
       81: "ливни", 82: "сильные ливни", 95: "гроза", 96: "гроза с градом", 99: "гроза с градом"}


def weather_today() -> str | None:
    url = ("https://api.open-meteo.com/v1/forecast?latitude=43.7034&longitude=7.2663"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
           "wind_speed_10m_max&timezone=Europe%2FParis&forecast_days=1")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            d = json.load(r)["daily"]
        code = d["weather_code"][0]
        txt = (f"{WMO.get(code, 'погода переменчива').capitalize()}, "
               f"{round(d['temperature_2m_min'][0])}…{round(d['temperature_2m_max'][0])}°C")
        rain = d["precipitation_probability_max"][0]
        if rain is not None and rain >= 30:
            txt += f", вероятность осадков {rain}%"
        wind = d["wind_speed_10m_max"][0]
        if wind is not None and wind >= 30:
            txt += f", ветер до {round(wind)} км/ч"
        return txt
    except Exception as ex:  # noqa: BLE001
        logging.warning("Погода недоступна: %s", ex)
        return None


# ---------- Claude ----------

COMMON = """Ты — редактор русскоязычного телеграм-канала о жизни в Ницце для семьи, которая там живёт.
Ищи свежие сведения в местных французских источниках: Nice-Matin, France 3 Côte d'Azur, ici Azur (France Bleu),
nice.fr, Métropole Nice Côte d'Azur, Lignes d'Azur, Météo-France, Préfecture des Alpes-Maritimes, OGC Nice, сайты музеев и театров.
География: Ницца и окрестности (Антиб, Канны, Монако, Ментон, Вильфранш, Сен-Поль-де-Ванс, Грас и т. п.).
Пиши по-русски своими словами, коротко, без копирования абзацев из статей. Французские названия мест и событий
оставляй как есть. Не выдумывай: включай только то, что нашёл в поиске, у каждого пункта — ссылка на источник.
В конце ответа выдай ТОЛЬКО JSON по схеме ниже, без пояснений до и после.
Внутри текстовых значений JSON никогда не используй двойные кавычки " — для названий бери «ёлочки»."""

NEWS_TASK = """Сегодня {today}. Собери утреннюю сводку новостей за последние сутки (не старше 36 часов).
Разделы:
- "alerts": тревоги и предупреждения, если есть (vigilance Météo-France оранжевая/красная, пожары, наводнения,
  перекрытия, забастовки транспорта сегодня/завтра). Если тревог нет — пустой список.
- "city": город и транспорт (трамвай и автобусы Lignes d'Azur, аэропорт, стройки, решения мэрии, цены, жильё).
- "culture": культура (выставки, кино, концерты, театр, фестивали — новости и ближайшие события).
- "sport": спорт (OGC Nice и другие клубы региона, крупные соревнования).
Максимум 4 пункта в разделе, только самое важное и полезное для жителя. Пустой раздел — пустой список.
Не повторяй уже отправленное раньше:
{seen}

JSON:
{{"alerts": [{{"title": "...", "text": "1–2 предложения", "url": "https://..."}}],
  "city": [...], "culture": [...], "sport": [...]}}"""

AFISHA_TASK = """Сегодня {today}. Составь афишу на выходные: пятница {fri} — воскресенье {sun},
плюс 2–3 главных события следующей недели. Ищи на сайтах музеев, театров, кинотеатров (фильмы в VO),
концертных залов, nice.fr, nicecotedazurtourisme.com, сайтах Антиба, Канн, Монако, Ментона.
Отбирай разнообразно: выставки (особенно фото и искусство), кино, концерты, театр, ярмарки и рынки, спорт, прогулки и
бесплатные события. 8–12 пунктов на выходные.

JSON:
{{"weekend": [{{"title": "...", "when": "сб 22 ноября, 20:00", "where": "место, город", "price": "бесплатно / от 15 € / ''",
               "text": "1 предложение, чем интересно", "url": "https://..."}}],
  "next_week": [то же самое]}}"""


def parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("В ответе нет JSON")
    end = text.rfind("}")
    chunk = text[start:end + 1] if end > start else text[start:]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError as ex:
        # Claude иногда ставит " внутри названий или обрывает ответ — чиним
        logging.warning("JSON с ошибкой (%s), чиню", ex)
        d = json_repair.loads(text[start:])
        if not isinstance(d, dict):
            raise ValueError("Не удалось разобрать JSON") from ex
        return d


async def ask(task: str) -> dict:
    messages = [{"role": "user", "content": task}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES, "user_location": LOCATION}]
    text = ""
    for _ in range(4):  # поиск может приостановить ход (pause_turn) — продолжаем
        resp = await client().messages.create(model=MODEL, max_tokens=10000, system=COMMON,
                                              messages=messages, tools=tools)
        text += "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    logging.info("Claude: stop=%s searches=%s", resp.stop_reason,
                 getattr(getattr(resp.usage, "server_tool_use", None), "web_search_requests", "?"))
    return parse_json(text)


def clean_items(items, n: int) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not str(it.get("title", "")).strip():
            continue
        url = str(it.get("url", ""))
        it["url"] = url if url.startswith(("http://", "https://")) else ""
        out.append(it)
    return out[:n]


async def morning_news(today: date, seen_titles: list[str]) -> dict:
    seen = "\n".join(f"- {t}" for t in seen_titles[-60:]) or "(ничего)"
    d = await ask(NEWS_TASK.format(today=ru_date(today), seen=seen))
    return {k: clean_items(d.get(k), 4) for k in ("alerts", "city", "culture", "sport")}


async def weekend_afisha(today: date) -> dict:
    wd = today.weekday()
    fri = today - timedelta(days=wd - 4) if wd >= 4 else today + timedelta(days=4 - wd)  # в пт–вс — эти выходные
    sun = fri + timedelta(days=2)
    d = await ask(AFISHA_TASK.format(today=ru_date(today), fri=ru_date(fri), sun=ru_date(sun)))
    return {"weekend": clean_items(d.get("weekend"), 12), "next_week": clean_items(d.get("next_week"), 3),
            "fri": fri, "sun": sun}
