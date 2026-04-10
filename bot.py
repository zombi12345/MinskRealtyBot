import json
import re
import requests
import logging
import os
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from geopy.distance import distance
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

# Координаты станций метро Минска
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

current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')

try:
    with open(json_path, 'r', encoding='utf-8') as f:
        FLATS = json.load(f)
    logger.info(f"✅ Загружено {len(FLATS)} квартир")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки: {e}")
    FLATS = []

# Кэш для API запросов
api_cache = {}
cache_duration = timedelta(hours=24)

def get_from_cache(key):
    if key in api_cache:
        data, timestamp = api_cache[key]
        if datetime.now() - timestamp < cache_duration:
            return data
    return None

def set_to_cache(key, data):
    api_cache[key] = (data, datetime.now())

def calculate_distance(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999
    return distance((lat1, lon1), (lat2, lon2)).km

def calculate_distance_meters(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999999
    return int(distance((lat1, lon1), (lat2, lon2)).meters)

@lru_cache(maxsize=200)
def get_metro_distance(lat, lon):
    """Находит ближайшую станцию метро"""
    min_dist = 999999
    nearest = None
    for station, coord in METRO_STATIONS.items():
        dist = calculate_distance_meters(lat, lon, coord[0], coord[1])
        if dist < min_dist:
            min_dist = dist
            nearest = station
    return nearest, min_dist

@lru_cache(maxsize=100)
def get_osm_pois(lat, lon, radius=800):
    """Поиск POI через OpenStreetMap Overpass API"""
    cache_key = f"osm_{lat}_{lon}_{radius}"
    cached = get_from_cache(cache_key)
    if cached:
        return cached
    
    query = f"""
    [out:json][timeout:12];
    (
      node["shop"~"supermarket|convenience|mall"](around:{radius},{lat},{lon});
      node["amenity"~"kindergarten|school|pharmacy|cafe|restaurant|fast_food"](around:{radius},{lat},{lon});
      node["leisure"="park"](around:{radius},{lat},{lon});
    );
    out body;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=12)
        if r.status_code == 200:
            result = parse_osm_response(r.json(), lat, lon)
            set_to_cache(cache_key, result)
            return result
    except Exception as e:
        logger.warning(f"OSM API error: {e}")
    
    # Возвращаем пустой результат, если API не ответил
    return {'shops': [], 'cafes': [], 'parks': [], 'schools': [], 'kindergartens': [], 'pharmacies': []}

def parse_osm_response(data, lat, lon):
    results = {'shops': [], 'cafes': [], 'parks': [], 'schools': [], 'kindergartens': [], 'pharmacies': []}
    
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        el_lat, el_lon = el.get('lat', 0), el.get('lon', 0)
        dist = calculate_distance_meters(lat, lon, el_lat, el_lon)
        name = tags.get('name', '')
        
        if tags.get('shop') in ['supermarket', 'convenience', 'mall']:
            results['shops'].append({'name': name or 'Магазин', 'distance': dist})
        elif tags.get('amenity') == 'kindergarten':
            results['kindergartens'].append({'name': name or 'Детский сад', 'distance': dist})
        elif tags.get('amenity') == 'school':
            results['schools'].append({'name': name or 'Школа', 'distance': dist})
        elif tags.get('amenity') == 'pharmacy':
            results['pharmacies'].append({'name': name or 'Аптека', 'distance': dist})
        elif tags.get('amenity') in ['cafe', 'restaurant', 'fast_food']:
            results['cafes'].append({'name': name or 'Кафе', 'distance': dist})
        elif tags.get('leisure') == 'park':
            results['parks'].append({'name': name or 'Парк', 'distance': dist})
    
    for cat in results:
        results[cat] = sorted(results[cat], key=lambda x: x['distance'])[:4]
    
    return results

@lru_cache(maxsize=100)
def get_yandex_geocode(address):
    """Геокодирование через Yandex API"""
    cache_key = f"geo_{address}"
    cached = get_from_cache(cache_key)
    if cached:
        return cached
    
    try:
        url = f"https://geocode-maps.yandex.ru/1.x/?apikey=ваш_ключ&geocode={address}&format=json"
        # Пока используем заглушку, так как ключ нужно получить отдельно
        return None
    except:
        return None

def extract_user_needs(text):
    text_lower = text.lower()
    needs = {'rooms': None, 'max_price': None, 'floor': None, 'metro_station': None}
    
    if 'трёхкомнатн' in text_lower or 'трехкомнатн' in text_lower or '3-комнатн' in text_lower or '3 комнаты' in text_lower:
        needs['rooms'] = 3
    elif '2' in text_lower or 'двух' in text_lower or 'двушк' in text_lower:
        needs['rooms'] = 2
    elif '1' in text_lower or 'одно' in text_lower or 'однушк' in text_lower:
        needs['rooms'] = 1
    
    price_match = re.search(r'до\s*(\d{4,6})', text_lower)
    if price_match:
        needs['max_price'] = int(price_match.group(1))
    
    floor_match = re.search(r'(\d+)\s*этаж', text_lower)
    if floor_match:
        needs['floor'] = int(floor_match.group(1))
    
    for station in METRO_STATIONS.keys():
        if station.lower() in text_lower:
            needs['metro_station'] = station
            break
    
    return needs

def score_flat(flat, needs):
    lat, lon = flat.get('lat'), flat.get('lon')
    score, max_score = 0, 0
    matches = []
    
    max_score += 30
    if needs['rooms'] is not None:
        if flat['rooms'] == needs['rooms']:
            score += 30
            matches.append(f"✅ {flat['rooms']}-комнатная")
        else:
            matches.append(f"ℹ️ {flat['rooms']}-комнатная (запрошена {needs['rooms']})")
    else:
        score += 30
    
    max_score += 30
    if needs['max_price'] is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 30
            matches.append(f"✅ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
        else:
            matches.append(f"⚠️ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 30
    
    max_score += 15
    if needs['floor'] is not None:
        flat_floor = flat.get('floor')
        if flat_floor and flat_floor == needs['floor']:
            score += 15
            matches.append(f"✅ Этаж {flat_floor}")
        elif flat_floor:
            matches.append(f"ℹ️ Этаж {flat_floor} (запрошен {needs['floor']})")
    else:
        score += 15
    
    if needs['metro_station'] and lat and lon:
        station_coord = METRO_STATIONS.get(needs['metro_station'])
        if station_coord:
            dist = calculate_distance_meters(lat, lon, station_coord[0], station_coord[1])
            if dist < 1500:
                score += 25
                max_score += 25
                matches.append(f"✅ 🚇 метро {needs['metro_station']}: {dist} м")
            else:
                max_score += 25
                matches.append(f"ℹ️ 🚇 метро {needs['metro_station']}: {dist} м")
    
    match_percent = int((score / max_score) * 100) if max_score > 0 else 0
    return {'match_percent': match_percent, 'matches': matches, 'lat': lat, 'lon': lon}

def format_flat_response(flat, analysis, index):
    msg = f"🏠 *Вариант {index}: {flat['rooms']}к, {flat['price_usd']}$*\n"
    msg += f"📍 {flat['address'][:50]}\n"
    msg += f"📊 *Совпадение: {analysis['match_percent']}%*\n"
    if analysis['matches']:
        msg += "\n" + "\n".join(analysis['matches'][:4])
    return msg

async def start(update: Update, context):
    await update.message.reply_text(
        "🏠 *ИИ-консультант «Твоя Столица»*\n\n"
        f"📊 *В базе:* {len(FLATS)} квартир\n\n"
        "🧠 *Понимаю:*\n"
        "• Количество комнат (1,2,3)\n"
        "• Цену (до 100000$)\n"
        "• Этаж (на 3 этаже)\n"
        "• Станции метро\n\n"
        "📝 *Примеры:*\n"
        "`Найди 3-комнатную до 100000$ рядом с метро Немига`\n\n"
        "После результатов можно спросить:\n"
        "• *Какие магазины рядом с первым?*\n"
        "• *Что есть из инфраструктуры?*",
        parse_mode="Markdown"
    )

async def search_flats(update: Update, context):
    text = update.message.text
    await update.message.chat.send_action(action="typing")
    
    thinking = await update.message.reply_text("🤔 *Анализирую варианты...*", parse_mode="Markdown")
    
    needs = extract_user_needs(text)
    scored = [(flat, score_flat(flat, needs)) for flat in FLATS]
    scored.sort(key=lambda x: x[1]['match_percent'], reverse=True)
    top = scored[:5]
    
    context.user_data['last_results'] = top
    context.user_data['last_needs'] = needs
    
    msg = f"🔍 *Варианты 1-{min(3, len(top))} из {len(top)}:*\n\n"
    for i, (flat, analysis) in enumerate(top[:3], 1):
        msg += format_flat_response(flat, analysis, i)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    keyboard = [
        [InlineKeyboardButton("📋 Следующие варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Спросить о варианте", callback_data="ask")]
    ]
    
    await thinking.edit_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def next_flats(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    results = context.user_data.get('last_results', [])
    idx = context.user_data.get('idx', 3)
    
    if not results:
        await query.edit_message_text("Нет результатов. Напишите новый запрос.")
        return
    
    start, end = idx, min(idx + 3, len(results))
    if start >= len(results):
        start, end = 0, 3
    
    msg = f"🔍 *Варианты {start+1}-{end} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[start:end], start + 1):
        msg += format_flat_response(flat, analysis, i)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    context.user_data['idx'] = end
    keyboard = [
        [InlineKeyboardButton("📋 Еще варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Спросить о варианте", callback_data="ask")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def ask_question(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💬 *Задайте вопрос о вариантах*\n\n"
        "Например:\n"
        "• *Какие магазины рядом с первым?*\n"
        "• *Что есть из инфраструктуры?*\n"
        "• *Есть ли парк рядом?*\n\n"
        "Просто напишите вопрос в чат!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ К вариантам", callback_data="back")
        ]])
    )
    context.user_data['waiting_for_question'] = True

async def back_to_results(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    results = context.user_data.get('last_results', [])
    if not results:
        await query.edit_message_text("Нет результатов. Напишите новый запрос.")
        return
    
    msg = f"🔍 *Варианты 1-{min(3, len(results))} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[:3], 1):
        msg += format_flat_response(flat, analysis, i)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    keyboard = [
        [InlineKeyboardButton("📋 Следующие варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Спросить о варианте", callback_data="ask")]
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
    
    # Определяем номер варианта
    flat_index = 0
    if 'перв' in text or '1' in text:
        flat_index = 0
    elif 'втор' in text or '2' in text:
        flat_index = 1 if len(results) > 1 else 0
    elif 'треть' in text or '3' in text:
        flat_index = 2 if len(results) > 2 else 0
    
    flat, analysis = results[flat_index]
    lat, lon = analysis.get('lat'), analysis.get('lon')
    
    # Отправляем сообщение о загрузке
    thinking = await update.message.reply_text("🔍 *Ищу информацию...*", parse_mode="Markdown")
    
    response = f"📊 *Информация о варианте {flat_index + 1}:*\n\n"
    response += f"🏠 *{flat['rooms']}к, {flat['price_usd']}$*\n"
    response += f"📍 {flat['address']}\n"
    response += f"🏘 Район: {flat['district']}\n\n"
    
    if lat and lon:
        # Метро
        nearest_metro, metro_dist = get_metro_distance(lat, lon)
        if nearest_metro:
            response += f"🚇 *Метро:* {nearest_metro} — {metro_dist} м\n\n"
        
        # POI через OSM API
        pois = get_osm_pois(lat, lon)
        
        added = False
        
        if 'магазин' in text or 'тц' in text or 'все' in text:
            if pois.get('shops'):
                response += "🏪 *Магазины рядом:*\n"
                for s in pois['shops'][:4]:
                    response += f"• {s['name']} — {s['distance']} м\n"
                added = True
        
        if 'кафе' in text or 'ресторан' in text or 'все' in text:
            if pois.get('cafes'):
                response += "\n☕ *Кафе и рестораны:*\n"
                for c in pois['cafes'][:4]:
                    response += f"• {c['name']} — {c['distance']} м\n"
                added = True
        
        if 'парк' in text or 'все' in text:
            if pois.get('parks'):
                response += "\n🌳 *Парки:*\n"
                for p in pois['parks'][:4]:
                    response += f"• {p['name']} — {p['distance']} м\n"
                added = True
        
        if 'школ' in text or 'все' in text:
            if pois.get('schools'):
                response += "\n📚 *Школы:*\n"
                for s in pois['schools'][:3]:
                    response += f"• {s['name']} — {s['distance']} м\n"
                added = True
        
        if 'сад' in text or 'детск' in text or 'все' in text:
            if pois.get('kindergartens'):
                response += "\n🏫 *Детские сады:*\n"
                for k in pois['kindergartens'][:3]:
                    response += f"• {k['name']} — {k['distance']} м\n"
                added = True
        
        if 'аптек' in text or 'все' in text:
            if pois.get('pharmacies'):
                response += "\n💊 *Аптеки:*\n"
                for ph in pois['pharmacies'][:3]:
                    response += f"• {ph['name']} — {ph['distance']} м\n"
                added = True
        
        if not added:
            response += "📍 *Ближайшая инфраструктура:*\n"
            if pois.get('shops'):
                response += f"• Магазин: {pois['shops'][0]['name']} — {pois['shops'][0]['distance']} м\n"
            if pois.get('cafes'):
                response += f"• Кафе: {pois['cafes'][0]['name']} — {pois['cafes'][0]['distance']} м\n"
            if pois.get('parks'):
                response += f"• Парк: {pois['parks'][0]['name']} — {pois['parks'][0]['distance']} м\n"
    else:
        response += "📍 Координаты для поиска инфраструктуры отсутствуют.\n"
    
    await thinking.delete()
    await update.message.reply_text(response, parse_mode="Markdown")
    
    # Остаемся в режиме вопросов
    await update.message.reply_text(
        "💡 Еще вопросы? Спрашивайте!",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ К вариантам", callback_data="back")
        ]])
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(next_flats, pattern="next"))
    app.add_handler(CallbackQueryHandler(ask_question, pattern="ask"))
    app.add_handler(CallbackQueryHandler(back_to_results, pattern="back"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_question))
    
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    app.run_polling()

if __name__ == "__main__":
    main()