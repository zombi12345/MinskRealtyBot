import json
import re
import requests
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from geopy.distance import distance
from functools import lru_cache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

# Координаты станций метро Минска
METRO_STATIONS = {
    'Немига': {'lat': 53.9065, 'lon': 27.5550},
    'Купаловская': {'lat': 53.9075, 'lon': 27.5620},
    'Октябрьская': {'lat': 53.9000, 'lon': 27.5600},
    'Площадь Ленина': {'lat': 53.8960, 'lon': 27.5510},
    'Институт культуры': {'lat': 53.8940, 'lon': 27.5420},
    'Грушевка': {'lat': 53.8780, 'lon': 27.5230},
    'Малиновка': {'lat': 53.8600, 'lon': 27.5280},
    'Петровщина': {'lat': 53.8700, 'lon': 27.5400},
    'Михалово': {'lat': 53.8550, 'lon': 27.5200},
    'Каменная горка': {'lat': 53.8930, 'lon': 27.4630},
    'Кунцевщина': {'lat': 53.8860, 'lon': 27.4420},
    'Спортивная': {'lat': 53.8950, 'lon': 27.4780},
    'Пушкинская': {'lat': 53.9040, 'lon': 27.5020},
    'Молодежная': {'lat': 53.8980, 'lon': 27.4280},
    'Фрунзенская': {'lat': 53.9140, 'lon': 27.5160},
    'Вокзальная': {'lat': 53.8900, 'lon': 27.5490},
    'Партизанская': {'lat': 53.8620, 'lon': 27.6090},
    'Автозаводская': {'lat': 53.8620, 'lon': 27.6320},
    'Могилевская': {'lat': 53.8570, 'lon': 27.6580},
    'Уручье': {'lat': 53.9460, 'lon': 27.6910},
    'Борисовский тракт': {'lat': 53.9350, 'lon': 27.6550},
    'Восток': {'lat': 53.9230, 'lon': 27.6310},
    'Московская': {'lat': 53.9140, 'lon': 27.5920},
    'Парк Челюскинцев': {'lat': 53.9230, 'lon': 27.6100},
    'Академия наук': {'lat': 53.9200, 'lon': 27.5990},
    'Якуба Коласа': {'lat': 53.9170, 'lon': 27.5830},
    'Площадь Победы': {'lat': 53.9100, 'lon': 27.5750}
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

def calculate_distance(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999
    return distance((lat1, lon1), (lat2, lon2)).km

def calculate_distance_meters(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999999
    return int(distance((lat1, lon1), (lat2, lon2)).meters)

@lru_cache(maxsize=100)
def find_nearby_pois_cached(lat, lon, radius=1000):
    if not lat or not lon:
        return {}
    query = f"""
    [out:json][timeout:10];
    (
      node["shop"~"supermarket|convenience"](around:{radius},{lat},{lon});
      node["amenity"~"kindergarten|school|pharmacy|cafe|restaurant"](around:{radius},{lat},{lon});
      node["leisure"="park"](around:{radius},{lat},{lon});
    );
    out body;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=10)
        if r.status_code == 200:
            return parse_poi(r.json(), lat, lon)
    except Exception as e:
        logger.warning(f"POI API error: {e}")
    return {}

def parse_poi(data, lat, lon):
    results = {'shops': [], 'kindergartens': [], 'schools': [], 'pharmacies': [], 'cafes': [], 'parks': []}
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        el_lat, el_lon = el.get('lat', 0), el.get('lon', 0)
        dist = int(distance((lat, lon), (el_lat, el_lon)).meters)
        name = tags.get('name', '')
        if tags.get('shop'):
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
        results[cat] = sorted(results[cat], key=lambda x: x['distance'])[:3]
    return results

def extract_user_needs(text):
    text_lower = text.lower()
    needs = {'rooms': None, 'max_price': None, 'floor': None, 'metro_station': None}
    
    if 'трёхкомнатн' in text_lower or 'трехкомнатн' in text_lower or '3-комнатн' in text_lower or '3 комнаты' in text_lower or 'трёшк' in text_lower:
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
    
    max_score += 35
    if needs['rooms'] is not None:
        if flat['rooms'] == needs['rooms']:
            score += 35
            matches.append(f"✅ {flat['rooms']}-комнатная")
    else:
        score += 35
    
    max_score += 35
    if needs['max_price'] is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 35
            matches.append(f"✅ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 35
    
    max_score += 15
    if needs['floor'] is not None:
        flat_floor = flat.get('floor')
        if flat_floor and flat_floor == needs['floor']:
            score += 15
            matches.append(f"✅ Этаж {flat_floor}")
    
    # Поиск метро
    if needs['metro_station'] and lat and lon:
        station_coord = METRO_STATIONS.get(needs['metro_station'])
        if station_coord:
            dist = calculate_distance_meters(lat, lon, station_coord['lat'], station_coord['lon'])
            if dist < 1500:
                score += 15
                max_score += 15
                matches.append(f"✅ 🚇 метро {needs['metro_station']}: {dist} м")
            else:
                max_score += 15
                matches.append(f"ℹ️ 🚇 метро {needs['metro_station']}: {dist} м (далековато)")
    
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
        "• Станции метро (Немига, Каменная горка и др.)\n\n"
        "📝 *Примеры:*\n"
        "`Найди 3-комнатную до 100000$ рядом с метро Немига`\n"
        "`2 комнаты до 80000$ возле метро Каменная горка`\n\n"
        "После результатов можно спросить:\n"
        "• *Какие магазины рядом с первым вариантом?*\n"
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
    
    # Сохраняем результаты в контекст
    context.user_data['last_results'] = top
    context.user_data['last_needs'] = needs
    context.user_data['last_query'] = text
    
    msg = f"🔍 *Варианты 1-{min(3, len(top))} из {len(top)}:*\n\n"
    for i, (flat, analysis) in enumerate(top[:3], 1):
        msg += format_flat_response(flat, analysis, i)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
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
        await query.edit_message_text("Нет результатов. Напишите новый запрос.")
        return
    
    start, end = idx, min(idx + 3, len(results))
    if start >= len(results):
        start, end = 0, 3
        idx = 3
    
    msg = f"🔍 *Варианты {start+1}-{end} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[start:end], start + 1):
        msg += format_flat_response(flat, analysis, i)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    context.user_data['idx'] = end
    keyboard = [
        [InlineKeyboardButton("📋 Еще варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Задать вопрос о варианте", callback_data="ask")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def ask_question(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💬 *Задайте вопрос о вариантах*\n\n"
        "Например:\n"
        "• *Какие магазины рядом с первым вариантом?*\n"
        "• *Что рядом со вторым вариантом?*\n"
        "• *Есть ли парк рядом с третьим?*\n\n"
        "Вопросы можно задавать прямо в чат!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Вернуться к вариантам", callback_data="back")
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
        [InlineKeyboardButton("❓ Задать вопрос о варианте", callback_data="ask")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data['waiting_for_question'] = False

async def handle_question(update: Update, context):
    if not context.user_data.get('waiting_for_question'):
        # Если не в режиме вопросов, обрабатываем как обычный поиск
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
    if 'перв' in text or 'первая' in text or '1' in text:
        flat_index = 0
    elif 'втор' in text or 'вторая' in text or '2' in text:
        flat_index = 1 if len(results) > 1 else 0
    elif 'треть' in text or 'третья' in text or '3' in text:
        flat_index = 2 if len(results) > 2 else 0
    
    flat, analysis = results[flat_index]
    lat, lon = analysis.get('lat'), analysis.get('lon')
    
    response = f"📊 *Информация о варианте {flat_index + 1}:*\n\n"
    response += f"🏠 *{flat['rooms']}к, {flat['price_usd']}$*\n"
    response += f"📍 {flat['address']}\n"
    response += f"🏘 Район: {flat['district']}\n\n"
    
    # Поиск инфраструктуры
    if lat and lon:
        if 'магазин' in text or 'тц' in text or 'торгов' in text:
            poi = find_nearby_pois_cached(lat, lon)
            shops = poi.get('shops', [])
            if shops:
                response += "*🏪 Магазины рядом:*\n"
                for s in shops[:3]:
                    response += f"• {s['name']} — {s['distance']} м\n"
            else:
                response += "🏪 Магазины в радиусе 1 км не найдены.\n"
        
        elif 'школ' in text:
            poi = find_nearby_pois_cached(lat, lon)
            schools = poi.get('schools', [])
            if schools:
                response += "*📚 Школы рядом:*\n"
                for s in schools[:3]:
                    response += f"• {s['name']} — {s['distance']} м\n"
            else:
                response += "📚 Школы в радиусе 1 км не найдены.\n"
        
        elif 'сад' in text or 'детск' in text:
            poi = find_nearby_pois_cached(lat, lon)
            kindergartens = poi.get('kindergartens', [])
            if kindergartens:
                response += "*🏫 Детские сады рядом:*\n"
                for k in kindergartens[:3]:
                    response += f"• {k['name']} — {k['distance']} м\n"
            else:
                response += "🏫 Детские сады в радиусе 1 км не найдены.\n"
        
        elif 'парк' in text:
            poi = find_nearby_pois_cached(lat, lon)
            parks = poi.get('parks', [])
            if parks:
                response += "*🌳 Парки рядом:*\n"
                for p in parks[:3]:
                    response += f"• {p['name']} — {p['distance']} м\n"
            else:
                response += "🌳 Парки в радиусе 1 км не найдены.\n"
        
        elif 'кафе' in text or 'ресторан' in text:
            poi = find_nearby_pois_cached(lat, lon)
            cafes = poi.get('cafes', [])
            if cafes:
                response += "*☕ Кафе и рестораны рядом:*\n"
                for c in cafes[:3]:
                    response += f"• {c['name']} — {c['distance']} м\n"
            else:
                response += "☕ Кафе в радиусе 1 км не найдены.\n"
        
        elif 'аптек' in text:
            poi = find_nearby_pois_cached(lat, lon)
            pharmacies = poi.get('pharmacies', [])
            if pharmacies:
                response += "*💊 Аптеки рядом:*\n"
                for p in pharmacies[:3]:
                    response += f"• {p['name']} — {p['distance']} м\n"
            else:
                response += "💊 Аптеки в радиусе 1 км не найдены.\n"
        
        elif 'метро' in text:
            # Ищем ближайшее метро
            min_dist = 999999
            nearest_metro = None
            for station, coord in METRO_STATIONS.items():
                dist = calculate_distance_meters(lat, lon, coord['lat'], coord['lon'])
                if dist < min_dist:
                    min_dist = dist
                    nearest_metro = station
            if nearest_metro:
                response += f"🚇 *Ближайшее метро:* {nearest_metro} — {min_dist} м\n"
            else:
                response += "🚇 Метро не найдено.\n"
        
        else:
            # Общая информация
            poi = find_nearby_pois_cached(lat, lon)
            response += "*🏪 Ближайшая инфраструктура:*\n"
            if poi.get('shops'):
                response += f"• Магазин: {poi['shops'][0]['name']} — {poi['shops'][0]['distance']} м\n"
            if poi.get('cafes'):
                response += f"• Кафе: {poi['cafes'][0]['name']} — {poi['cafes'][0]['distance']} м\n"
            if poi.get('parks'):
                response += f"• Парк: {poi['parks'][0]['name']} — {poi['parks'][0]['distance']} м\n"
    
    response += "\n💡 *Совет:* Можете уточнить вопрос или спросить о другом варианте."
    
    await update.message.reply_text(response, parse_mode="Markdown")
    
    # Не выходим из режима вопросов, чтобы можно было задать еще вопрос
    await update.message.reply_text(
        "Остались вопросы? Спрашивайте! Или нажмите кнопку ниже, чтобы вернуться к вариантам.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Вернуться к вариантам", callback_data="back")
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