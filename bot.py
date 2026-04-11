import os
import json
import re
import logging
import asyncio
import requests
import difflib
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

try:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("OpenAI client initialized")
except Exception as e:
    logger.error(f"OpenAI init error: {e}")
    openai_client = None

api_cache = TTLCache(maxsize=500, ttl=86400)

# ========== КООРДИНАТЫ ==========
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
    'Каменная горка': (53.8930, 27.4630), 'Чижовка': (53.8600, 27.5750),
    'Лошица': (53.8650, 27.5650), 'Серебрянка': (53.8700, 27.5900),
    'Уручье': (53.9460, 27.6910), 'Кунцевщина': (53.8860, 27.4420),
    'Сухарево': (53.8780, 27.4380), 'Малиновка': (53.8600, 27.5280),
    'Грушевка': (53.8780, 27.5230), 'Петровщина': (53.8700, 27.5400),
    'Михалово': (53.8550, 27.5200), 'Сосны': (53.8500, 27.6100),
    'Шабаны': (53.8450, 27.6150), 'Ангарская': (53.8620, 27.6550),
    'Восток': (53.9230, 27.6310), 'Веснянка': (53.9300, 27.6400),
    'Зеленый Луг': (53.9180, 27.5500), 'Красный Бор': (53.8880, 27.5250)
}

# ========== ЗАГРУЗКА КВАРТИР ==========
current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')
try:
    with open(json_path, 'r', encoding='utf-8') as f:
        FLATS = json.load(f)
    logger.info(f"✅ Загружено {len(FLATS)} квартир")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки: {e}")
    FLATS = []

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def calculate_distance_meters(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999999
    return int(distance((lat1, lon1), (lat2, lon2)).meters)

def get_osm_pois(lat, lon, radius=1000):
    cache_key = f"osm_{lat}_{lon}_{radius}"
    if cache_key in api_cache:
        return api_cache[cache_key]
    query = f"""
    [out:json][timeout:12];
    (
      node["shop"~"supermarket|convenience|mall"](around:{radius},{lat},{lon});
      node["amenity"~"kindergarten|school|pharmacy|cafe|restaurant"](around:{radius},{lat},{lon});
      node["leisure"="park"](around:{radius},{lat},{lon});
    );
    out body;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=12)
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

# ========== ПАРСИНГ ЗАПРОСА ЧЕРЕЗ OPENAI ==========
def parse_query_with_openai(user_text):
    prompt = f"""
Ты — помощник по поиску квартир в Минске. Извлеки из запроса пользователя следующие параметры (только если они явно упомянуты). Верни ТОЛЬКО JSON-объект без лишнего текста.

Возможные значения:
- rooms: целое число 1, 2 или 3
- max_price: целое число (цена в долларах США)
- floor: целое число (этаж)
- metro_station: строка (название станции метро из списка: Немига, Купаловская, Октябрьская, Площадь Ленина, Институт культуры, Грушевка, Малиновка, Каменная горка, Спортивная, Пушкинская, Партизанская, Автозаводская, Могилевская, Уручье, Восток, Московская, Парк Челюскинцев, Академия наук, Площадь Победы, Вокзальная)
- district: строка (название района: Каменная горка, Чижовка, Лошица, Серебрянка, Уручье, Кунцевщина, Сухарево, Малиновка, Грушевка, Петровщина, Михалово, Сосны, Шабаны, Ангарская, Восток, Веснянка, Зеленый Луг, Красный Бор)
- infrastructure: массив строк, возможные значения: "детский сад", "школа", "ТЦ", "магазин", "кафе", "парк", "аптека"

Если параметр не упомянут, ставь null. Для инфраструктуры — пустой массив.

Формат ответа (пример):
{{
  "rooms": 1,
  "max_price": 50000,
  "floor": null,
  "metro_station": null,
  "district": "Каменная горка",
  "infrastructure": ["школа"]
}}

Запрос пользователя: "{user_text}"
"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300,
            timeout=10
        )
        content = response.choices[0].message.content.strip()
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(content)
        if 'infrastructure' not in data:
            data['infrastructure'] = []
        # Нормализация
        if data.get('district'):
            data['district'] = data['district'].strip()
        if data.get('metro_station'):
            data['metro_station'] = data['metro_station'].strip()
        return data
    except Exception as e:
        logger.error(f"OpenAI parse error: {e}")
        return None

# ========== РЕЗЕРВНЫЙ ПАРСЕР (С ИСПРАВЛЕНИЕМ ОПЕЧАТОК) ==========
def fallback_parse_query(user_text):
    text_lower = user_text.lower()
    query = {
        'rooms': None,
        'max_price': None,
        'floor': None,
        'metro_station': None,
        'district': None,
        'infrastructure': []
    }
    # Комнаты
    if '3-комнатн' in text_lower or 'трёхкомнатн' in text_lower or '3 комнаты' in text_lower:
        query['rooms'] = 3
    elif '2-комнатн' in text_lower or 'двухкомнатн' in text_lower or '2 комнаты' in text_lower:
        query['rooms'] = 2
    elif '1-комнатн' in text_lower or 'однокомнатн' in text_lower or '1 комнату' in text_lower:
        query['rooms'] = 1
    # Цена
    price_match = re.search(r'до\s*(\d{4,6})', text_lower)
    if price_match:
        query['max_price'] = int(price_match.group(1))
    # Этаж
    floor_match = re.search(r'(\d+)\s*этаж', text_lower)
    if floor_match:
        query['floor'] = int(floor_match.group(1))
    # Метро (с исправлением опечаток)
    metro_names = list(METRO_STATIONS.keys())
    for station in metro_names:
        if station.lower() in text_lower:
            query['metro_station'] = station
            break
    else:
        words = text_lower.split()
        for word in words:
            matches = difflib.get_close_matches(word, [s.lower() for s in metro_names], n=1, cutoff=0.7)
            if matches:
                for orig in metro_names:
                    if orig.lower() == matches[0]:
                        query['metro_station'] = orig
                        break
                if query['metro_station']:
                    break
    # Район (с исправлением опечаток)
    district_names = list(DISTRICT_COORDS.keys())
    for district in district_names:
        if district.lower() in text_lower:
            query['district'] = district
            break
    else:
        words = text_lower.split()
        for word in words:
            matches = difflib.get_close_matches(word, [d.lower() for d in district_names], n=1, cutoff=0.7)
            if matches:
                for orig in district_names:
                    if orig.lower() == matches[0]:
                        query['district'] = orig
                        break
                if query['district']:
                    break
    # Инфраструктура
    infra_keywords = {
        'школ': 'школа', 'школы': 'школа',
        'детский сад': 'детский сад', 'садик': 'детский сад', 'сад': 'детский сад',
        'тц': 'ТЦ', 'торговый центр': 'ТЦ', 'молл': 'ТЦ',
        'магазин': 'магазин', 'супермаркет': 'магазин',
        'кафе': 'кафе', 'кофейня': 'кафе',
        'парк': 'парк', 'сквер': 'парк',
        'аптека': 'аптека'
    }
    for word, infra in infra_keywords.items():
        if word in text_lower:
            query['infrastructure'].append(infra)
    query['infrastructure'] = list(set(query['infrastructure']))
    return query

# ========== ОЦЕНКА КВАРТИРЫ ==========
def score_flat(flat, query):
    lat, lon = flat.get('lat'), flat.get('lon')
    score = 0
    max_score = 0
    matched = []
    failed = []
    details = {}

    # 1. Комнаты (20 баллов)
    max_score += 20
    if query.get('rooms') is not None:
        if flat['rooms'] == query['rooms']:
            score += 20
            matched.append(f"✅ {flat['rooms']}-комнатная")
        else:
            failed.append(f"❌ {flat['rooms']}-комнатная (запрошена {query['rooms']})")
    else:
        score += 20
        matched.append(f"ℹ️ {flat['rooms']}-комнатная")

    # 2. Цена (20 баллов)
    max_score += 20
    if query.get('max_price') is not None:
        if flat['price_usd'] <= query['max_price']:
            score += 20
            matched.append(f"✅ {flat['price_usd']}$ (бюджет {query['max_price']}$)")
        else:
            failed.append(f"❌ {flat['price_usd']}$ (бюджет {query['max_price']}$)")
    else:
        score += 20
        matched.append(f"ℹ️ {flat['price_usd']}$")

    # 3. Этаж (10 баллов)
    max_score += 10
    if query.get('floor') is not None:
        flat_floor = flat.get('floor')
        if flat_floor and flat_floor == query['floor']:
            score += 10
            matched.append(f"✅ Этаж {flat_floor}")
        elif flat_floor:
            failed.append(f"❌ Этаж {flat_floor} (запрошен {query['floor']})")
    else:
        score += 10

    # 4. Метро (15 баллов)
    max_score += 15
    if query.get('metro_station') and lat and lon:
        station = METRO_STATIONS.get(query['metro_station'])
        if station:
            dist = calculate_distance_meters(lat, lon, station[0], station[1])
            details['metro_distance'] = dist
            if dist < 1500:
                score += 15
                matched.append(f"✅ метро {query['metro_station']}: {dist} м")
            else:
                failed.append(f"❌ метро {query['metro_station']}: {dist} м")
    else:
        score += 15

    # 5. Район (15 баллов)
    max_score += 15
    if query.get('district') and lat and lon:
        district_coord = DISTRICT_COORDS.get(query['district'])
        if district_coord:
            dist = calculate_distance_meters(lat, lon, district_coord[0], district_coord[1])
            details['district_distance'] = dist
            if dist < 2000:
                score += 15
                matched.append(f"✅ рядом с {query['district']}: {dist} м")
            else:
                failed.append(f"❌ далеко от {query['district']}: {dist} м")
    else:
        score += 15

    # 6. Инфраструктура (каждый пункт 5 баллов, макс 20)
    infra_map = {
        'детский сад': 'kindergartens',
        'школа': 'schools',
        'ТЦ': 'malls',
        'магазин': 'shops',
        'кафе': 'cafes',
        'парк': 'parks',
        'аптека': 'pharmacies'
    }
    infra_score = 0
    infra_max = 20
    for req in query.get('infrastructure', [])[:4]:
        max_score += 5
        poi_type = infra_map.get(req)
        if poi_type:
            has, info = check_poi_nearby(lat, lon, poi_type)
            if has:
                infra_score += 5
                matched.append(f"✅ {req.capitalize()}: {info['distance']} м")
            else:
                failed.append(f"❌ {req.capitalize()} не найдено в радиусе 1 км")
    score += infra_score

    percent = int((score / max_score) * 100) if max_score > 0 else 0
    return {
        'match_percent': percent,
        'matched': matched,
        'failed': failed,
        'lat': lat,
        'lon': lon,
        'details': details
    }

def format_flat_response(flat, analysis, index):
    msg = f"🏠 *Вариант {index}: {flat['rooms']}к, {flat['price_usd']}$*\n"
    msg += f"📍 {flat['address'][:50]}\n"
    msg += f"🏘 Район: {flat.get('district', 'Не указан')}\n"
    if 'district_distance' in analysis.get('details', {}):
        msg += f"📏 Расстояние до района: {analysis['details']['district_distance']} м\n"
    if 'metro_distance' in analysis.get('details', {}):
        msg += f"🚇 Расстояние до метро: {analysis['details']['metro_distance']} м\n"
    msg += f"📊 *Совпадение: {analysis['match_percent']}%*\n\n"
    if analysis['matched']:
        msg += "*✅ Выполненные условия:*\n"
        for m in analysis['matched'][:6]:
            msg += f"{m}\n"
    if analysis['failed']:
        msg += "\n*❌ Невыполненные условия:*\n"
        for f in analysis['failed'][:4]:
            msg += f"{f}\n"
    msg += f"\n🔗 [Смотреть]({flat['url']})"
    return msg

def format_infrastructure_response(flat, poi):
    msg = f"📊 *Инфраструктура вокруг квартиры:*\n\n🏠 *{flat['rooms']}к, {flat['price_usd']}$*\n📍 {flat['address'][:50]}\n\n"
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
        "🏠 *Добро пожаловать в ИИ-консультанта «Твоя Столица»!*\n\n"
        f"📊 *В базе:* {len(FLATS)} квартир\n\n"
        "🧠 *Я использую искусственный интеллект, чтобы точно понимать ваши запросы.*\n\n"
        "📝 *Примеры:*\n"
        "• `Найди 1 комнату до 50000$ рядом с Каменной горкой и школой`\n"
        "• `Квартиру рядом со станцией Спортивная и аптекой`\n"
        "• `2 комнаты до 70000$ у метро Немига с детским садом`\n\n"
        "После результатов можно задавать уточняющие вопросы: `Что рядом с первым вариантом?`, `Как далеко Каменная горка от первого варианта?`",
        parse_mode="Markdown"
    )

async def search_flats(update: Update, context):
    text = update.message.text
    await update.message.chat.send_action(action="typing")
    thinking = await update.message.reply_text("🤔 *Анализирую запрос с помощью ИИ...*", parse_mode="Markdown")

    query = parse_query_with_openai(text)
    if not query:
        query = fallback_parse_query(text)
        logger.info("Used fallback parser")

    if not query.get('rooms') and not query.get('max_price') and not query.get('metro_station') and not query.get('district') and not query.get('infrastructure'):
        await thinking.edit_text("⚠️ *Не удалось распознать запрос. Пожалуйста, переформулируйте.*\n\nПример: `1 комнату до 50000$ рядом с Каменной горкой`", parse_mode="Markdown")
        return

    scored = [(flat, score_flat(flat, query)) for flat in FLATS]
    if query.get('district'):
        scored.sort(key=lambda x: ( -x[1]['match_percent'], x[1].get('details', {}).get('district_distance', 999999) ))
    elif query.get('metro_station'):
        scored.sort(key=lambda x: ( -x[1]['match_percent'], x[1].get('details', {}).get('metro_distance', 999999) ))
    else:
        scored.sort(key=lambda x: -x[1]['match_percent'])

    top = scored[:5]
    context.user_data['last_results'] = top
    context.user_data['last_query'] = query
    context.user_data['idx'] = 3

    if not top or top[0][1]['match_percent'] == 0:
        await thinking.edit_text("😔 *Ничего не найдено. Попробуйте изменить критерии.*", parse_mode="Markdown")
        return

    msg = "🔍 *Результаты поиска*\n\n📋 *Как я понял запрос:*\n"
    if query.get('rooms'): msg += f"🏠 {query['rooms']}-комнатная\n"
    if query.get('max_price'): msg += f"💰 до {query['max_price']}$\n"
    if query.get('floor'): msg += f"📌 на {query['floor']} этаже\n"
    if query.get('metro_station'): msg += f"🚇 рядом с метро {query['metro_station']}\n"
    if query.get('district'): msg += f"📍 в районе {query['district']}\n"
    for infra in query.get('infrastructure', []):
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
        "💬 *Задайте вопрос о вариантах*\n\nНапример:\n• Что рядом с первым вариантом?\n• Какие магазины рядом со вторым?\n• Как далеко Каменная горка от первого варианта?\n\nПросто напишите вопрос в чат!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Вернуться к вариантам", callback_data="back")]])
    )

async def back_to_results(update: Update, context):
    query = update.callback_query
    await query.answer()
    results = context.user_data.get('last_results', [])
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

    # Расстояние до района
    for district in DISTRICT_COORDS.keys():
        if district.lower() in text and ('далеко' in text or 'расстоян' in text or 'сколько' in text or 'далек' in text):
            if lat and lon:
                dist = calculate_distance_meters(lat, lon, DISTRICT_COORDS[district][0], DISTRICT_COORDS[district][1])
                response = f"📍 *Расстояние от квартиры до района {district}:* {dist} м"
                await update.message.reply_text(response, parse_mode="Markdown")
                return
            else:
                await update.message.reply_text("Координаты квартиры отсутствуют.")
                return

    # Расстояние до метро
    for station in METRO_STATIONS.keys():
        if station.lower() in text and ('далеко' in text or 'расстоян' in text or 'сколько' in text or 'далек' in text):
            if lat and lon:
                dist = calculate_distance_meters(lat, lon, METRO_STATIONS[station][0], METRO_STATIONS[station][1])
                response = f"🚇 *Расстояние от квартиры до метро {station}:* {dist} м"
                await update.message.reply_text(response, parse_mode="Markdown")
                return
            else:
                await update.message.reply_text("Координаты квартиры отсутствуют.")
                return

    # Общая инфраструктура
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

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER ==========
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

# ========== ЗАПУСК БОТА ==========
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