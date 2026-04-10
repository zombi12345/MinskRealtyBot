import os
import json
import re
import logging
import asyncio
import requests
import math
from threading import Thread
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from geopy.distance import distance
from cachetools import TTLCache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

# API КЛЮЧИ
YANDEX_GEO_KEY = "ac332495-30ba-43ef-a119-e842e8fe23b2"
ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImI0ZTcxNDQ2ZjU4ZjQwNDY5NDM4OTIyNGZjMjQzZWRmIiwiaCI6Im11cm11cjY0In0="

# Кэш
api_cache = TTLCache(maxsize=500, ttl=86400)

# СЛОВАРЬ ДЛЯ ИСПРАВЛЕНИЯ ОПЕЧАТОК
CORRECTIONS = {
    'немега': 'Немига', 'нимига': 'Немига', 'немего': 'Немига',
    'купаловская': 'Купаловская', 'октябрьская': 'Октябрьская',
    'площадь ленина': 'Площадь Ленина', 'институт культуры': 'Институт культуры',
    'грушевка': 'Грушевка', 'малиновка': 'Малиновка',
    'каменная горка': 'Каменная горка', 'спортивная': 'Спортивная',
    'пушкинская': 'Пушкинская', 'партизанская': 'Партизанская',
    'автозаводская': 'Автозаводская', 'могилевская': 'Могилевская',
    'уручье': 'Уручье', 'восток': 'Восток', 'московская': 'Московская',
    'детскй сад': 'детский сад', 'детски сад': 'детский сад', 'садик': 'детский сад',
    'школа': 'школа', 'школы': 'школа', 'школу': 'школа',
    'тц': 'торговый центр', 'трц': 'торговый центр',
    'аптека': 'аптека', 'аптеки': 'аптека',
    'кафе': 'кафе', 'кофейня': 'кафе', 'ресторан': 'кафе',
    'парк': 'парк', 'парки': 'парк', 'сквер': 'парк',
    'магазин': 'магазин', 'магазины': 'магазин', 'супермаркет': 'магазин'
}

# КООРДИНАТЫ
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
    'Площадь Победы': (53.9100, 27.5750), 'Вокзальная': (53.8900, 27.5490),
    'Чижовка': (53.8600, 27.5750)
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

# ЗАГРУЗКА ДАННЫХ
current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')

try:
    with open(json_path, 'r', encoding='utf-8') as f:
        FLATS = json.load(f)
    logger.info(f"✅ Загружено {len(FLATS)} квартир")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки: {e}")
    FLATS = []

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def calculate_distance_meters(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999999
    return int(distance((lat1, lon1), (lat2, lon2)).meters)

def correct_text(text):
    text_lower = text.lower()
    for wrong, correct in CORRECTIONS.items():
        if wrong in text_lower:
            text_lower = text_lower.replace(wrong, correct.lower())
    return text_lower

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

def extract_user_needs(text):
    text_corrected = correct_text(text)
    needs = {
        'rooms': None, 'max_price': None, 'floor': None, 
        'metro_station': None, 'district': None,
        'want_kindergarten': False, 'want_school': False, 'want_shop': False,
        'want_mall': False, 'want_cafe': False, 'want_park': False,
        'want_pharmacy': False, 'explanation': []
    }
    
    # Комнаты
    if '3-комнатн' in text_corrected or 'трёхкомнатн' in text_corrected:
        needs['rooms'] = 3
        needs['explanation'].append("🏠 3-комнатная")
    elif '2-комнатн' in text_corrected or 'двухкомнатн' in text_corrected:
        needs['rooms'] = 2
        needs['explanation'].append("🏠 2-комнатная")
    elif '1-комнатн' in text_corrected or 'однокомнатн' in text_corrected:
        needs['rooms'] = 1
        needs['explanation'].append("🏠 1-комнатная")
    
    # Цена
    price = None
    patterns = [
        r'до\s*(\d{2,6})\s*(?:долларов|доллара|доллар|\$|\b)',
        r'до\s*(\d{2,6})\s*(?:тысяч|тысячи|тыс|к)\s*(?:долларов|доллара|\$)?',
        r'(\d{2,6})\s*(?:тысяч|тысячи|тыс|к)\s*(?:долларов|доллара)?',
        r'(\d{2,6})\s*\$',
        r'бюджет\s*до\s*(\d{2,6})',
        r'не дороже\s*(\d{2,6})',
        r'максимум\s*(\d{2,6})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_corrected)
        if match:
            price_candidate = int(match.group(1))
            if price_candidate < 1000 and ('тысяч' in text_corrected or 'тыс' in text_corrected or 'к' in text_corrected):
                price_candidate = price_candidate * 1000
            price = price_candidate
            break
    
    if price:
        needs['max_price'] = price
        needs['explanation'].append(f"💰 до {price}$")
    
    # Этаж
    floor_match = re.search(r'(\d+)\s*этаж', text_corrected)
    if floor_match:
        needs['floor'] = int(floor_match.group(1))
        needs['explanation'].append(f"📌 на {needs['floor']} этаже")
    
    # Метро
    for station in METRO_STATIONS.keys():
        if station.lower() in text_corrected:
            needs['metro_station'] = station
            needs['explanation'].append(f"🚇 рядом с метро {station}")
            break
    
    # Район
    for district, coords in DISTRICT_COORDS.items():
        if district.lower() in text_corrected:
            needs['district'] = district
            needs['explanation'].append(f"📍 в районе {district}")
            break
    
    # Инфраструктура
    if 'детский сад' in text_corrected or 'садик' in text_corrected:
        needs['want_kindergarten'] = True
        needs['explanation'].append("🏫 рядом детский сад")
    if 'школ' in text_corrected:
        needs['want_school'] = True
        needs['explanation'].append("📚 рядом школа")
    if 'магазин' in text_corrected:
        needs['want_shop'] = True
        needs['explanation'].append("🏪 рядом магазин")
    if 'торговый центр' in text_corrected or 'тц' in text_corrected:
        needs['want_mall'] = True
        needs['explanation'].append("🏬 рядом ТЦ")
    if 'кафе' in text_corrected:
        needs['want_cafe'] = True
        needs['explanation'].append("☕ рядом кафе")
    if 'парк' in text_corrected:
        needs['want_park'] = True
        needs['explanation'].append("🌳 рядом парк")
    if 'аптек' in text_corrected:
        needs['want_pharmacy'] = True
        needs['explanation'].append("💊 рядом аптека")
    
    return needs

def score_flat(flat, needs):
    lat, lon = flat.get('lat'), flat.get('lon')
    score = 0
    max_score = 0
    matched_criteria = []
    failed_criteria = []
    
    # Комнаты (15 баллов)
    max_score += 15
    if needs['rooms'] is not None:
        if flat['rooms'] == needs['rooms']:
            score += 15
            matched_criteria.append(f"✅ {flat['rooms']}-комнатная")
        else:
            failed_criteria.append(f"❌ {flat['rooms']}-комнатная (запрошена {needs['rooms']})")
    else:
        score += 15
        matched_criteria.append(f"ℹ️ {flat['rooms']}-комнатная")
    
    # Цена (15 баллов)
    max_score += 15
    if needs['max_price'] is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 15
            matched_criteria.append(f"✅ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
        else:
            failed_criteria.append(f"❌ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 15
        matched_criteria.append(f"ℹ️ {flat['price_usd']}$")
    
    # Этаж (10 баллов)
    max_score += 10
    if needs['floor'] is not None:
        flat_floor = flat.get('floor')
        if flat_floor and flat_floor == needs['floor']:
            score += 10
            matched_criteria.append(f"✅ Этаж {flat_floor}")
        elif flat_floor:
            failed_criteria.append(f"❌ Этаж {flat_floor} (запрошен {needs['floor']})")
    else:
        score += 10
    
    # Метро (15 баллов)
    max_score += 15
    if needs['metro_station'] and lat and lon:
        station_coord = METRO_STATIONS.get(needs['metro_station'])
        if station_coord:
            dist = calculate_distance_meters(lat, lon, station_coord[0], station_coord[1])
            if dist < 1500:
                score += 15
                matched_criteria.append(f"✅ метро {needs['metro_station']}: {dist} м")
            else:
                failed_criteria.append(f"❌ метро {needs['metro_station']}: {dist} м")
    else:
        score += 15
    
    # Район (15 баллов)
    max_score += 15
    if needs['district'] and lat and lon:
        district_coord = DISTRICT_COORDS.get(needs['district'])
        if district_coord:
            dist = calculate_distance_meters(lat, lon, district_coord[0], district_coord[1])
            if dist < 2000:
                score += 15
                matched_criteria.append(f"✅ рядом с районом {needs['district']}: {dist} м")
            else:
                failed_criteria.append(f"❌ далеко от {needs['district']}: {dist} м")
    else:
        score += 15
    
    # Детский сад (15 баллов)
    if needs['want_kindergarten']:
        max_score += 15
        has, info = check_poi_nearby(lat, lon, 'kindergartens')
        if has:
            score += 15
            matched_criteria.append(f"✅ Детский сад \"{info['name']}\": {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Детский сад не найден в радиусе 1 км")
    
    # Школа (5 баллов)
    if needs['want_school']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'schools')
        if has:
            score += 5
            matched_criteria.append(f"✅ Школа: {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Школа не найдена")
    
    # Магазин (5 баллов)
    if needs['want_shop']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'shops')
        if has:
            score += 5
            matched_criteria.append(f"✅ Магазин: {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Магазин не найден")
    
    # ТЦ (5 баллов)
    if needs['want_mall']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'malls')
        if has:
            score += 5
            matched_criteria.append(f"✅ ТЦ: {info['distance']} м")
        else:
            failed_criteria.append(f"❌ ТЦ не найден")
    
    # Кафе (5 баллов)
    if needs['want_cafe']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'cafes')
        if has:
            score += 5
            matched_criteria.append(f"✅ Кафе: {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Кафе не найдено")
    
    # Парк (5 баллов)
    if needs['want_park']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'parks')
        if has:
            score += 5
            matched_criteria.append(f"✅ Парк: {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Парк не найден")
    
    # Аптека (5 баллов)
    if needs['want_pharmacy']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'pharmacies')
        if has:
            score += 5
            matched_criteria.append(f"✅ Аптека: {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Аптека не найдена")
    
    match_percent = int((score / max_score) * 100) if max_score > 0 else 0
    return {
        'match_percent': match_percent,
        'matched': matched_criteria,
        'failed': failed_criteria,
        'lat': lat,
        'lon': lon,
        'district': flat.get('district')
    }

def format_flat_response(flat, analysis, index, needs):
    msg = f"🏠 *Вариант {index}: {flat['rooms']}к, {flat['price_usd']}$*\n"
    msg += f"📍 {flat['address'][:50]}\n"
    msg += f"🏘 Район: {flat.get('district', 'Не указан')}\n"
    msg += f"📊 *Совпадение: {analysis['match_percent']}%*\n\n"
    
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

# ===== ОБРАБОТЧИКИ TELEGRAM =====
async def start(update: Update, context):
    await update.message.reply_text(
        f"🏠 *Добро пожаловать в ИИ-консультанта «Твоя Столица»!*\n\n"
        f"📊 *В базе:* {len(FLATS)} квартир\n\n"
        f"📖 *Как пользоваться:*\n\n"
        f"• Напишите, что ищете: `1 комнату до 50000$ рядом с Чижовкой и детским садом`\n"
        f"• Бот понимает опечатки и сокращения\n"
        f"• Покажет, какие условия выполнены, а какие нет\n\n"
        f"📝 *Примеры:*\n"
        f"• `2 комнаты до 70000$`\n"
        f"• `Квартиру у метро Немига`\n"
        f"• `Рядом с парком и школой`",
        parse_mode="Markdown"
    )

async def search_flats(update: Update, context):
    text = update.message.text
    await update.message.chat.send_action(action="typing")
    
    thinking = await update.message.reply_text(
        "🤔 *Анализирую запрос...*\n\n"
        "🔍 Исправляю опечатки...\n"
        "📍 Проверяю параметры...\n"
        "🏫 Ищу подходящие квартиры...\n"
        "💰 Сравниваю цены...",
        parse_mode="Markdown"
    )
    
    needs = extract_user_needs(text)
    
    scored = []
    for flat in FLATS:
        analysis = score_flat(flat, needs)
        scored.append((flat, analysis))
    
    scored.sort(key=lambda x: x[1]['match_percent'], reverse=True)
    top = scored[:5]
    
    context.user_data['last_results'] = top
    context.user_data['last_needs'] = needs
    
    if not top or top[0][1]['match_percent'] == 0:
        msg = "😔 *Ничего не найдено по вашему запросу.*\n\n"
        msg += "💡 *Попробуйте:*\n"
        msg += "• Увеличить бюджет\n"
        msg += "• Убрать некоторые фильтры (этаж, конкретное метро)\n"
        msg += "• Расширить географию поиска\n\n"
        msg += "📝 *Пример:* `1 комнату до 70000$`"
        await thinking.edit_text(msg, parse_mode="Markdown")
        return
    
    msg = f"🔍 *Результаты поиска*\n\n"
    if needs['explanation']:
        msg += f"📋 *Ваш запрос:*\n"
        for exp in needs['explanation'][:5]:
            msg += f"{exp}\n"
        msg += f"\n{'─' * 40}\n\n"
    
    if top[0][1]['match_percent'] >= 60:
        msg += f"✨ *Найдено {len(top)} отличных вариантов:*\n\n"
    elif top[0][1]['match_percent'] >= 30:
        msg += f"📌 *Найдено {len(top)} частично подходящих вариантов:*\n\n"
    else:
        msg += f"⚠️ *Найдено {len(top)} вариантов, но они не идеальны:*\n\n"
        msg += "💡 *Совет:* Попробуйте расширить бюджет или убрать некоторые фильтры.\n\n"
    
    for i, (flat, analysis) in enumerate(top[:3], 1):
        msg += format_flat_response(flat, analysis, i, needs)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    if len(top) > 3:
        msg += f"_Показаны топ-3 из {len(top)}. Нажмите кнопку для просмотра следующих вариантов._"
    
    keyboard = [[InlineKeyboardButton("📋 Следующие варианты", callback_data="next")]]
    await thinking.edit_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def next_flats(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    results = context.user_data.get('last_results', [])
    idx = context.user_data.get('idx', 3)
    needs = context.user_data.get('last_needs', {})
    
    if not results:
        await query.edit_message_text("Нет результатов. Напишите новый запрос.")
        return
    
    start, end = idx, min(idx + 3, len(results))
    if start >= len(results):
        start, end = 0, 3
    
    msg = f"🔍 *Варианты {start+1}-{end} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[start:end], start + 1):
        msg += format_flat_response(flat, analysis, i, needs)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    context.user_data['idx'] = end
    keyboard = [[InlineKeyboardButton("📋 Еще варианты", callback_data="next")]]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

# ===== ВЕБ-СЕРВЕР ДЛЯ RENDER (В ОТДЕЛЬНОМ ПОТОКЕ) =====
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

# ЗАПУСКАЕМ ВЕБ-СЕРВЕР В ОТДЕЛЬНОМ ПОТОКЕ
web_thread = Thread(target=run_web, daemon=True)
web_thread.start()

# ===== ЗАПУСК БОТА (С ПРАВИЛЬНЫМ EVENT LOOP) =====
def main():
    # Создаем новый event loop для основного потока
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(next_flats, pattern="next"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_flats))
    
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    
    # Запускаем polling в созданном event loop
    loop.run_until_complete(app.initialize())
    loop.run_until_complete(app.start())
    loop.run_until_complete(app.updater.start_polling())
    loop.run_forever()

if __name__ == "__main__":
    # Сначала удаляем вебхук (в отдельном event loop)
    async def reset_webhook():
        bot = Bot(token=BOT_TOKEN)
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook удален")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(reset_webhook())
    
    # Запускаем основного бота
    main()