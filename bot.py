import os
import json
import re
import logging
import asyncio
import requests
import math
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from geopy.distance import distance
from cachetools import TTLCache
from openai import OpenAI

# ========== НАСТРОЙКИ ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"
OPENAI_API_KEY = "sk-Bylz2io6oa46zyduebiq3It5xncjfPgqGhiujd4JaCg7GSvg"

openai_client = OpenAI(api_key=OPENAI_API_KEY)
api_cache = TTLCache(maxsize=500, ttl=86400)          # кэш на 24 часа

# ========== КООРДИНАТЫ МЕТРО И РАЙОНОВ ==========
METRO_STATIONS = {
    'Немига': (53.9065, 27.5550), 'Купаловская': (53.9075, 27.5620),
    'Октябрьская': (53.9000, 27.5600), 'Площадь Ленина': (53.8960, 27.5510),
    'Институт культуры': (53.8940, 27.5420), 'Грушевка': (53.8780, 27.5230),
    'Малиновка': (53.8600, 27.5280), 'Каменная горка': (53.8930, 27.4630),
    'Спортивная': (53.8950, 27.4780), 'Пушкинская': (53.9040, 27.5020),
    'Партизанская': (53.8620, 27.6090), 'Автозаводская': (53.8620, 27.6320),
    'Могилевская': (53.8570, 27.6580), 'Уручье': (53.9460, 27.6910),
    'Восток': (53.9230, 27.6310), 'Московская': (53.9140, 27.5920),
    'Парк Челюскинцев': (53.9230, 27.6100), 'Академия наук': (53.9200, 27.5990),
    'Площадь Победы': (53.9100, 27.5750), 'Вокзальная': (53.8900, 27.5490)
}

DISTRICT_COORDS = {
    'Чижовка': (53.8600, 27.5750), 'Лошица': (53.8650, 27.5650),
    'Серебрянка': (53.8700, 27.5900), 'Уручье': (53.9460, 27.6910),
    'Каменная горка': (53.8930, 27.4630), 'Кунцевщина': (53.8860, 27.4420),
    'Сухарево': (53.8780, 27.4380), 'Малиновка': (53.8600, 27.5280),
    'Грушевка': (53.8780, 27.5230), 'Петровщина': (53.8700, 27.5400),
    'Михалово': (53.8550, 27.5200), 'Сосны': (53.8500, 27.6100),
    'Шабаны': (53.8450, 27.6150), 'Ангарская': (53.8620, 27.6550),
    'Восток': (53.9230, 27.6310), 'Веснянка': (53.9300, 27.6400),
    'Зеленый Луг': (53.9180, 27.5500), 'Красный Бор': (53.8880, 27.5250)
}

# ========== ЗАГРУЗКА ДАННЫХ О КВАРТИРАХ ==========
current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')
try:
    with open(json_path, 'r', encoding='utf-8') as f:
        FLATS = json.load(f)
    logger.info(f"✅ Загружено {len(FLATS)} квартир")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки: {e}")
    FLATS = []

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (POI, РАССТОЯНИЯ) ==========
def calculate_distance_meters(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999999
    return int(distance((lat1, lon1), (lat2, lon2)).meters)

def get_osm_pois(lat, lon, radius=1000):
    cache_key = f"osm_{lat}_{lon}_{radius}"
    if cache_key in api_cache:
        return api_cache[cache_key]
    query = f"""
    [out:json][timeout:15];
    (
      node["shop"~"supermarket|convenience|mall"](around:{radius},{lat},{lon});
      node["amenity"~"kindergarten|school|pharmacy|cafe|restaurant"](around:{radius},{lat},{lon});
      node["leisure"="park"](around:{radius},{lat},{lon});
    );
    out body;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=15)
        if r.status_code == 200:
            result = parse_osm_response(r.json(), lat, lon)
            api_cache[cache_key] = result
            return result
    except Exception as e:
        logger.warning(f"OSM API error: {e}")
    return {}

def parse_osm_response(data, lat, lon):
    results = {'shops': [], 'cafes': [], 'parks': [], 'schools': [], 'kindergartens': [], 'pharmacies': [], 'malls': []}
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        el_lat, el_lon = el.get('lat', 0), el.get('lon', 0)
        dist = int(distance((lat, lon), (el_lat, el_lon)).meters)
        name = tags.get('name', '')
        if tags.get('shop') == 'mall':
            results['malls'].append({'name': name or 'ТЦ', 'distance': dist})
        elif tags.get('shop') in ['supermarket', 'convenience']:
            results['shops'].append({'name': name or 'Магазин', 'distance': dist})
        elif tags.get('amenity') == 'kindergarten':
            results['kindergartens'].append({'name': name or 'Детский сад', 'distance': dist})
        elif tags.get('amenity') == 'school':
            results['schools'].append({'name': name or 'Школа', 'distance': dist})
        elif tags.get('amenity') == 'pharmacy':
            results['pharmacies'].append({'name': name or 'Аптека', 'distance': dist})
        elif tags.get('amenity') in ['cafe', 'restaurant']:
            results['cafes'].append({'name': name or 'Кафе', 'distance': dist})
        elif tags.get('leisure') == 'park':
            results['parks'].append({'name': name or 'Парк', 'distance': dist})
    for cat in results:
        results[cat] = sorted(results[cat], key=lambda x: x['distance'])[:5]
    return results

def check_poi_nearby(lat, lon, poi_type, max_distance=1000):
    if not lat or not lon:
        return False, None
    poi = get_osm_pois(lat, lon)
    if poi.get(poi_type):
        nearest = poi[poi_type][0]
        if nearest['distance'] <= max_distance:
            return True, nearest
    return False, None

# ========== ИЗВЛЕЧЕНИЕ ПАРАМЕТРОВ ЧЕРЕЗ OPENAI ==========
def extract_needs_with_llm(user_text):
    prompt = f"""
Ты — помощник по поиску квартир в Минске. Извлеки из запроса пользователя следующие параметры (только если они явно упомянуты):
- количество комнат (1, 2, 3)
- максимальная цена в долларах США (число)
- желаемый этаж (число)
- станция метро (название, например "Немига", "Спортивная", "Каменная горка")
- район или микрорайон (например "Чижовка", "Уручье")
- объекты инфраструктуры, которые должны быть рядом: детский сад, школа, ТЦ (торговый центр), магазин, кафе, парк, аптека

Верни ТОЛЬКО JSON-объект в точном формате:
{{
  "rooms": null,
  "max_price": null,
  "floor": null,
  "metro_station": null,
  "district": null,
  "infrastructure": []
}}

Запрос пользователя: "{user_text}"
"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=250
        )
        content = response.choices[0].message.content.strip()
        # Извлекаем JSON из ответа (на случай лишнего текста)
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(content)
        if 'infrastructure' not in data:
            data['infrastructure'] = []
        return data
    except Exception as e:
        logger.error(f"LLM parsing error: {e}")
        return None

# ========== ОЦЕНКА КВАРТИРЫ ==========
def score_flat(flat, needs):
    lat, lon = flat.get('lat'), flat.get('lon')
    score = 0
    max_score = 0
    matched = []
    failed = []

    # Комнаты (15 баллов)
    max_score += 15
    if needs.get('rooms') is not None:
        if flat['rooms'] == needs['rooms']:
            score += 15
            matched.append(f"✅ {flat['rooms']}-комнатная")
        else:
            failed.append(f"❌ {flat['rooms']}-комнатная (запрошена {needs['rooms']})")
    else:
        score += 15
        matched.append(f"ℹ️ {flat['rooms']}-комнатная")

    # Цена (15 баллов)
    max_score += 15
    if needs.get('max_price') is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 15
            matched.append(f"✅ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
        else:
            failed.append(f"❌ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 15
        matched.append(f"ℹ️ {flat['price_usd']}$")

    # Этаж (10 баллов)
    max_score += 10
    if needs.get('floor') is not None:
        flat_floor = flat.get('floor')
        if flat_floor and flat_floor == needs['floor']:
            score += 10
            matched.append(f"✅ Этаж {flat_floor}")
        elif flat_floor:
            failed.append(f"❌ Этаж {flat_floor} (запрошен {needs['floor']})")
    else:
        score += 10

    # Метро (15 баллов)
    max_score += 15
    if needs.get('metro_station') and lat and lon:
        station = METRO_STATIONS.get(needs['metro_station'])
        if station:
            dist = calculate_distance_meters(lat, lon, station[0], station[1])
            if dist < 1500:
                score += 15
                matched.append(f"✅ метро {needs['metro_station']}: {dist} м")
            else:
                failed.append(f"❌ метро {needs['metro_station']}: {dist} м")
    else:
        score += 15

    # Район (15 баллов)
    max_score += 15
    if needs.get('district') and lat and lon:
        district_coord = DISTRICT_COORDS.get(needs['district'])
        if district_coord:
            dist = calculate_distance_meters(lat, lon, district_coord[0], district_coord[1])
            if dist < 2000:
                score += 15
                matched.append(f"✅ рядом с {needs['district']}: {dist} м")
            else:
                failed.append(f"❌ далеко от {needs['district']}: {dist} м")
    else:
        score += 15

    # Инфраструктура (каждый пункт 5 баллов)
    infra_map = {
        'детский сад': 'kindergartens',
        'школа': 'schools',
        'ТЦ': 'malls',
        'магазин': 'shops',
        'кафе': 'cafes',
        'парк': 'parks',
        'аптека': 'pharmacies'
    }
    for req in needs.get('infrastructure', []):
        max_score += 5
        poi_type = infra_map.get(req)
        if poi_type:
            has, info = check_poi_nearby(lat, lon, poi_type)
            if has:
                score += 5
                matched.append(f"✅ {req.capitalize()}: {info['distance']} м")
            else:
                failed.append(f"❌ {req.capitalize()} не найдено в радиусе 1 км")

    percent = int((score / max_score) * 100) if max_score > 0 else 0
    return {
        'match_percent': percent,
        'matched': matched,
        'failed': failed,
        'lat': lat,
        'lon': lon
    }

def format_flat_response(flat, analysis, index):
    msg = f"🏠 *Вариант {index}: {flat['rooms']}к, {flat['price_usd']}$*\n"
    msg += f"📍 {flat['address'][:50]}\n"
    msg += f"🏘 Район: {flat.get('district', 'Не указан')}\n"
    msg += f"📊 *Совпадение с запросом: {analysis['match_percent']}%*\n\n"
    if analysis['matched']:
        msg += "*✅ Выполненные условия:*\n"
        for m in analysis['matched'][:6]:
            msg += f"{m}\n"
    if analysis['failed']:
        msg += "\n*❌ Невыполненные условия:*\n"
        for f in analysis['failed'][:4]:
            msg += f"{f}\n"
    msg += f"\n🔗 [Смотреть на сайте]({flat['url']})"
    return msg

def format_infrastructure_response(flat, poi):
    msg = f"📊 *Инфраструктура вокруг квартиры:*\n\n"
    msg += f"🏠 *{flat['rooms']}к, {flat['price_usd']}$*\n"
    msg += f"📍 {flat['address'][:50]}\n\n"
    sections = [
        ('shops', '🏪 Магазины'),
        ('cafes', '☕ Кафе'),
        ('kindergartens', '🏫 Детские сады'),
        ('schools', '📚 Школы'),
        ('parks', '🌳 Парки'),
        ('pharmacies', '💊 Аптеки'),
        ('malls', '🏬 Торговые центры')
    ]
    for key, title in sections:
        if poi.get(key):
            msg += f"{title}:\n"
            for item in poi[key][:3]:
                msg += f"   • {item['name']} — {item['distance']} м\n"
            msg += "\n"
    return msg if len(msg) > 100 else "Инфраструктура в радиусе 1 км не найдена."

# ========== ОБРАБОТЧИКИ TELEGRAM ==========
async def start(update: Update, context):
    await update.message.reply_text(
        f"🏠 *Добро пожаловать в ИИ-консультанта «Твоя Столица»!*\n\n"
        f"📊 *В базе:* {len(FLATS)} квартир\n\n"
        f"🧠 *Я использую искусственный интеллект, чтобы точно понимать ваши запросы.*\n\n"
        f"📝 *Примеры:*\n"
        f"• `1 комнату до 50000$ рядом с Чижовкой и ТЦ`\n"
        f"• `Квартиру рядом со станцией Спортивная и аптекой`\n"
        f"• `2 комнаты до 70000$ у метро Немига с детским садом`\n\n"
        f"После результатов можно задавать уточняющие вопросы: `Что рядом с первым вариантом?`",
        parse_mode="Markdown"
    )

async def search_flats(update: Update, context):
    text = update.message.text
    await update.message.chat.send_action(action="typing")
    thinking = await update.message.reply_text("🤔 *Анализирую запрос с помощью ИИ...*", parse_mode="Markdown")

    needs = extract_needs_with_llm(text)
    if not needs:
        await thinking.edit_text("⚠️ *Не удалось распознать запрос. Пожалуйста, переформулируйте.*", parse_mode="Markdown")
        return

    scored = [(flat, score_flat(flat, needs)) for flat in FLATS]
    scored.sort(key=lambda x: x[1]['match_percent'], reverse=True)
    top = scored[:5]

    context.user_data['last_results'] = top
    context.user_data['last_needs'] = needs
    context.user_data['idx'] = 3

    if not top or top[0][1]['match_percent'] == 0:
        await thinking.edit_text("😔 *Ничего не найдено. Попробуйте изменить критерии.*", parse_mode="Markdown")
        return

    msg = "🔍 *Результаты поиска*\n\n📋 *Как я понял запрос:*\n"
    if needs.get('rooms'): msg += f"🏠 {needs['rooms']}-комнатная\n"
    if needs.get('max_price'): msg += f"💰 до {needs['max_price']}$\n"
    if needs.get('floor'): msg += f"📌 на {needs['floor']} этаже\n"
    if needs.get('metro_station'): msg += f"🚇 рядом с метро {needs['metro_station']}\n"
    if needs.get('district'): msg += f"📍 в районе {needs['district']}\n"
    for infra in needs.get('infrastructure', []):
        msg += f"🏪 {infra}\n"
    msg += "\n" + "─" * 40 + "\n\n"

    for i, (flat, analysis) in enumerate(top[:3], 1):
        msg += format_flat_response(flat, analysis, i)
        msg += "\n\n" + "─" * 35 + "\n\n"
    if len(top) > 3:
        msg += "_Показаны топ-3 из 5. Нажмите кнопку для просмотра следующих вариантов._"

    keyboard = [
        [InlineKeyboardButton("📋 Следующие варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Задать вопрос о варианте", callback_data="ask")]
    ]
    await thinking.edit_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def next_flats(update: Update, context):
    query = update.callback_query
    await query.answer()
    results = context.user_data.get('last_results', [])
    idx = context.user_data.get('idx', 3)
    if not results:
        await query.edit_message_text("Нет результатов.")
        return
    start, end = idx, min(idx+3, len(results))
    if start >= len(results):
        start, end = 0, 3
    msg = f"🔍 *Варианты {start+1}-{end} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[start:end], start+1):
        msg += format_flat_response(flat, analysis, i) + "\n\n" + "─" * 35 + "\n\n"
    context.user_data['idx'] = end
    keyboard = [
        [InlineKeyboardButton("📋 Еще варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Задать вопрос", callback_data="ask")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def ask_question(update: Update, context):
    query = update.callback_query
    await query.answer()
    context.user_data['waiting_for_question'] = True
    await query.edit_message_text(
        "💬 *Задайте вопрос о вариантах*\n\nНапример:\n• Что рядом с первым вариантом?\n• Какие магазины рядом со вторым?\n• Есть ли детский сад рядом?\n\nПросто напишите вопрос в чат!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Вернуться к вариантам", callback_data="back")]])
    )

async def back_to_results(update: Update, context):
    query = update.callback_query
    await query.answer()
    results = context.user_data.get('last_results', [])
    needs = context.user_data.get('last_needs', {})
    if not results:
        await query.edit_message_text("Нет результатов.")
        return
    msg = f"🔍 *Варианты 1-{min(3,len(results))} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[:3], 1):
        msg += format_flat_response(flat, analysis, i) + "\n\n" + "─" * 35 + "\n\n"
    keyboard = [
        [InlineKeyboardButton("📋 Следующие варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Задать вопрос", callback_data="ask")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data['waiting_for_question'] = False

async def handle_question(update: Update, context):
    if not context.user_data.get('waiting_for_question'):
        await search_flats(update, context)
        return
    text = update.message.text.lower()
    results = context.user_data.get('last_results', [])
    if not results:
        await update.message.reply_text("Сначала выполните поиск квартир.")
        context.user_data['waiting_for_question'] = False
        return

    idx = 0
    if 'перв' in text or '1' in text:
        idx = 0
    elif 'втор' in text or '2' in text:
        idx = 1 if len(results) > 1 else 0
    elif 'треть' in text or '3' in text:
        idx = 2 if len(results) > 2 else 0
    flat, analysis = results[idx]
    lat, lon = analysis.get('lat'), analysis.get('lon')
    thinking = await update.message.reply_text("🔍 *Ищу информацию...*", parse_mode="Markdown")

    response = f"📊 *Информация о варианте {idx+1}:*\n\n"
    response += f"🏠 *{flat['rooms']}к, {flat['price_usd']}$*\n📍 {flat['address']}\n\n"
    if lat and lon:
        poi = get_osm_pois(lat, lon)
        response += format_infrastructure_response(flat, poi)
    else:
        response += "📍 Координаты для поиска отсутствуют."

    await thinking.delete()
    await update.message.reply_text(response, parse_mode="Markdown")
    await update.message.reply_text(
        "💡 Еще вопросы? Спрашивайте!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Вернуться к вариантам", callback_data="back")]])
    )

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER (ЧТОБЫ НЕ ЗАСЫПАЛ) ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "🤖 Бот работает!", 200

@flask_app.route('/health')
def health_check():
    return {"status": "ok", "flats": len(FLATS)}, 200

@flask_app.route('/wakeup')
def wakeup():
    return {"status": "awake"}, 200

def run_web():
    port = int(os.environ.get('PORT', 10000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

web_thread = Thread(target=run_web, daemon=True)
web_thread.start()

# ========== ЗАПУСК БОТА С ПРАВИЛЬНЫМ EVENT LOOP ==========
async def reset_webhook():
    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    print("✅ Webhook удален")

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(reset_webhook())

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(next_flats, pattern="next"))
    app.add_handler(CallbackQueryHandler(ask_question, pattern="ask"))
    app.add_handler(CallbackQueryHandler(back_to_results, pattern="back"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_question))

    logger.info("✅ Бот запущен")
    loop.run_until_complete(app.initialize())
    loop.run_until_complete(app.start())
    loop.run_until_complete(app.updater.start_polling())
    loop.run_forever()

if __name__ == "__main__":
    main()