import json
import re
import requests
import logging
import os
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from geopy.distance import distance

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

# Константы
CENTER_COORD = (53.9025, 27.5619)
MKAD_COORD = (53.8800, 27.6500)

# Состояния для диалога
ANSWERING_QUESTION = 1

current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')

# Загрузка данных
try:
    with open(json_path, 'r', encoding='utf-8') as f:
        FLATS = json.load(f)
    logger.info(f"✅ Загружено {len(FLATS)} квартир")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки: {e}")
    FLATS = []

# Хранилище контекста
user_context = {}

def calculate_distance(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999
    return distance((lat1, lon1), (lat2, lon2)).km

def find_nearby_pois(lat, lon, radius=1500):
    if not lat or not lon:
        return {}
    
    query = f"""
    [out:json];
    (
      node["shop"~"supermarket|convenience"](around:{radius},{lat},{lon});
      node["amenity"="kindergarten"](around:{radius},{lat},{lon});
      node["highway"="bus_stop"](around:{radius},{lat},{lon});
      node["amenity"="school"](around:{radius},{lat},{lon});
      node["amenity"="pharmacy"](around:{radius},{lat},{lon});
      node["amenity"~"cafe|restaurant"](around:{radius},{lat},{lon});
      node["leisure"="park"](around:{radius},{lat},{lon});
      node["railway"="subway_entrance"](around:{radius},{lat},{lon});
    );
    out body;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=25)
        if r.status_code == 200:
            return parse_poi(r.json(), lat, lon)
    except Exception as e:
        logger.warning(f"POI API error: {e}")
    return {}

def parse_poi(data, lat, lon):
    results = {
        'shops': [], 'kindergartens': [], 'bus_stops': [], 
        'schools': [], 'pharmacies': [], 'cafes': [], 
        'parks': [], 'metro': []
    }
    
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        el_lat, el_lon = el.get('lat', 0), el.get('lon', 0)
        dist = int(distance((lat, lon), (el_lat, el_lon)).meters)
        name = tags.get('name', '')
        
        if 'shop' in tags:
            results['shops'].append({'name': name or 'Магазин', 'distance': dist})
        elif tags.get('amenity') == 'kindergarten':
            results['kindergartens'].append({'name': name or 'Детский сад', 'distance': dist})
        elif tags.get('highway') == 'bus_stop':
            results['bus_stops'].append({'name': name or 'Остановка', 'distance': dist})
        elif tags.get('amenity') == 'school':
            results['schools'].append({'name': name or 'Школа', 'distance': dist})
        elif tags.get('amenity') == 'pharmacy':
            results['pharmacies'].append({'name': name or 'Аптека', 'distance': dist})
        elif tags.get('amenity') in ['cafe', 'restaurant']:
            results['cafes'].append({'name': name or 'Кафе', 'distance': dist})
        elif tags.get('leisure') == 'park':
            results['parks'].append({'name': name or 'Парк', 'distance': dist})
        elif tags.get('railway') == 'subway_entrance':
            results['metro'].append({'name': name or 'Метро', 'distance': dist})
    
    for k in results:
        results[k] = sorted(results[k], key=lambda x: x['distance'])[:3]
    return results

def analyze_flat(flat, user_request):
    lat, lon = flat.get('lat'), flat.get('lon')
    
    analysis = {
        'matches_floor': False,
        'matches_price': False,
        'details': []
    }
    
    # Проверка этажа
    if 'этаж' in user_request:
        floor_match = re.search(r'(\d+)\s*этаж', user_request)
        if floor_match:
            requested_floor = int(floor_match.group(1))
            flat_floor = flat.get('floor')
            if flat_floor and flat_floor == requested_floor:
                analysis['matches_floor'] = True
                analysis['details'].append(f"✅ Этаж {flat_floor} — соответствует запросу")
            elif flat_floor:
                analysis['details'].append(f"⚠️ Этаж {flat_floor} (запрошен {requested_floor})")
    
    # Проверка цены
    price_match = re.search(r'до\s*(\d{4,6})', user_request)
    if price_match:
        max_price = int(price_match.group(1))
        if flat['price_usd'] <= max_price:
            analysis['matches_price'] = True
            analysis['details'].append(f"✅ Цена {flat['price_usd']}$ — входит в бюджет")
        else:
            analysis['details'].append(f"⚠️ Цена {flat['price_usd']}$ (бюджет {max_price}$)")
    
    # Поиск POI
    if lat and lon:
        poi = find_nearby_pois(lat, lon)
        
        if poi.get('metro'):
            nearest_metro = poi['metro'][0]
            analysis['details'].append(f"🚇 Метро \"{nearest_metro['name']}\" — {nearest_metro['distance']} м")
            analysis['metro_info'] = nearest_metro
        
        if poi.get('kindergartens'):
            nearest_kind = poi['kindergartens'][0]
            analysis['details'].append(f"🏫 Детский сад — {nearest_kind['distance']} м")
        
        if poi.get('parks'):
            nearest_park = poi['parks'][0]
            analysis['details'].append(f"🌳 Парк \"{nearest_park['name']}\" — {nearest_park['distance']} м")
        
        if poi.get('shops'):
            nearest_shop = poi['shops'][0]
            analysis['details'].append(f"🏪 Магазин \"{nearest_shop['name']}\" — {nearest_shop['distance']} м")
        
        dist_mkad = calculate_distance(lat, lon, MKAD_COORD[0], MKAD_COORD[1])
        analysis['details'].append(f"🛣 {dist_mkad:.1f} км от МКАД")
        
        dist_center = calculate_distance(lat, lon, CENTER_COORD[0], CENTER_COORD[1])
        analysis['details'].append(f"📍 {dist_center:.1f} км от центра")
        
        analysis['poi'] = poi
    
    return analysis

def parse_complex_request(text):
    text_lower = text.lower()
    
    max_price = None
    price_match = re.search(r'до\s*(\d{4,6})', text_lower)
    if price_match:
        max_price = int(price_match.group(1))
    
    rooms = None
    if '1' in text_lower or 'одно' in text_lower:
        rooms = 1
    elif '2' in text_lower or 'двух' in text_lower:
        rooms = 2
    
    floor = None
    floor_match = re.search(r'(\d+)\s*этаж', text_lower)
    if floor_match:
        floor = int(floor_match.group(1))
    
    geo = {
        'want_center': 'центр' in text_lower,
        'want_mkad': 'мкад' in text_lower or 'кольцев' in text_lower,
        'want_metro': 'метро' in text_lower,
        'want_park': 'парк' in text_lower,
        'want_kindergarten': 'детск' in text_lower or 'сад' in text_lower
    }
    
    return {'max_price': max_price, 'rooms': rooms, 'floor': floor, 'geo': geo, 'original': text}

async def start(update: Update, context):
    user_id = update.effective_user.id
    user_context[user_id] = {'last_results': [], 'last_query': None}
    
    await update.message.reply_text(
        "🏠 *ИИ-консультант «Твоя Столица»*\n\n"
        f"📊 В базе {len(FLATS)} квартир\n\n"
        "Я понимаю сложные запросы!\n\n"
        "📝 *Пример:*\n"
        "`Найди 2-комнатную квартиру до 80000$, недалеко от метро, чтобы был детский сад и парк, желательно на 3 этаже`\n\n"
        "После выбора квартиры можете спрашивать:\n"
        "• *Как далеко магазины?*\n"
        "• *А есть варианты поближе к центру?*",
        parse_mode="Markdown"
    )

async def search_flats(update: Update, context):
    user_id = update.effective_user.id
    text = update.message.text
    await update.message.chat.send_action(action="typing")
    
    query = parse_complex_request(text)
    
    results = []
    for flat in FLATS:
        if query['rooms'] and flat['rooms'] != query['rooms']:
            continue
        if query['max_price'] and flat['price_usd'] > query['max_price']:
            continue
        if query['floor'] and flat.get('floor') and flat['floor'] != query['floor']:
            continue
        
        lat, lon = flat.get('lat'), flat.get('lon')
        if lat and lon and query['geo']['want_mkad']:
            dist = calculate_distance(lat, lon, MKAD_COORD[0], MKAD_COORD[1])
            if dist > 7:
                continue
        if lat and lon and query['geo']['want_center']:
            dist = calculate_distance(lat, lon, CENTER_COORD[0], CENTER_COORD[1])
            if dist > 4:
                continue
        
        results.append(flat)
    
    if not results:
        await update.message.reply_text(
            "😔 *Ничего не найдено*\n\nПопробуйте изменить критерии поиска.",
            parse_mode="Markdown"
        )
        return
    
    results = sorted(results, key=lambda x: x['price_usd'])[:5]
    user_context[user_id] = {'last_results': results, 'last_query': query, 'current_index': 0}
    
    msg = f"🔍 *Найдено {len(results)} вариантов:*\n\n"
    for i, flat in enumerate(results[:3], 1):
        analysis = analyze_flat(flat, text)
        msg += f"🏠 *Вариант {i}: {flat['rooms']}к, {flat['price_usd']}$*\n"
        msg += f"📍 {flat['address']}\n"
        msg += f"🏘 Район: {flat['district']}\n"
        msg += f"📊 *Анализ:*\n"
        for detail in analysis.get('details', [])[:6]:
            msg += f"{detail}\n"
        msg += f"\n🔗 [Смотреть]({flat['url']})\n\n" + "─" * 30 + "\n\n"
    
    keyboard = [[InlineKeyboardButton("📋 Следующие варианты", callback_data="next_flats")]]
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def next_flats(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    context_data = user_context.get(user_id, {})
    results = context_data.get('last_results', [])
    current = context_data.get('current_index', 0)
    
    if not results:
        await query.edit_message_text("Нет сохраненных результатов. Напишите новый запрос.")
        return
    
    start_idx = current + 3
    end_idx = min(start_idx + 3, len(results))
    
    if start_idx >= len(results):
        await query.edit_message_text("Это все варианты. Напишите новый запрос.")
        return
    
    msg = f"🔍 *Варианты {start_idx+1}-{end_idx} из {len(results)}:*\n\n"
    for i, flat in enumerate(results[start_idx:end_idx], start_idx + 1):
        msg += f"🏠 *Вариант {i}: {flat['rooms']}к, {flat['price_usd']}$*\n"
        msg += f"📍 {flat['address']}\n"
        msg += f"🏘 Район: {flat['district']}\n"
        msg += f"🔗 [Смотреть]({flat['url']})\n\n" + "─" * 30 + "\n\n"
    
    user_context[user_id]['current_index'] = end_idx
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def answer_question(update: Update, context):
    user_id = update.effective_user.id
    text = update.message.text.lower()
    context_data = user_context.get(user_id, {})
    results = context_data.get('last_results', [])
    
    if not results:
        await update.message.reply_text("Сначала выполните поиск квартир.")
        return ConversationHandler.END
    
    await update.message.chat.send_action(action="typing")
    
    flat = results[0]
    lat, lon = flat.get('lat'), flat.get('lon')
    
    response = ""
    if 'магазин' in text:
        if lat and lon:
            poi = find_nearby_pois(lat, lon, 1000)
            shops = poi.get('shops', [])
            if shops:
                response = "🏪 *Магазины рядом:*\n" + "\n".join([f"• {s['name']} — {s['distance']} м" for s in shops[:3]])
            else:
                response = "В радиусе 1 км магазинов не найдено."
    elif 'детск' in text or 'сад' in text:
        if lat and lon:
            poi = find_nearby_pois(lat, lon, 1000)
            kind = poi.get('kindergartens', [])
            if kind:
                response = "🏫 *Детские сады рядом:*\n" + "\n".join([f"• {k['name']} — {k['distance']} м" for k in kind[:3]])
            else:
                response = "В радиусе 1 км детских садов не найдено."
    elif 'парк' in text:
        if lat and lon:
            poi = find_nearby_pois(lat, lon, 1500)
            parks = poi.get('parks', [])
            if parks:
                response = "🌳 *Парки рядом:*\n" + "\n".join([f"• {p['name']} — {p['distance']} м" for p in parks[:3]])
            else:
                response = "В радиусе 1.5 км парков не найдено."
    elif 'метро' in text:
        if lat and lon:
            poi = find_nearby_pois(lat, lon, 1500)
            metro = poi.get('metro', [])
            if metro:
                response = "🚇 *Метро рядом:*\n" + "\n".join([f"• {m['name']} — {m['distance']} м" for m in metro[:3]])
            else:
                response = "В радиусе 1.5 км станций метро не найдено."
    else:
        response = "Я могу ответить на вопросы о:\n• Магазинах\n• Детских садах\n• Парках\n• Метро"
    
    await update.message.reply_text(response, parse_mode="Markdown")
    return ConversationHandler.END

async def cancel(update: Update, context):
    await update.message.reply_text("Диалог завершен.")
    return ConversationHandler.END

# Веб-сервер для Render
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "🤖 Бот работает!"

def run_web():
    port = int(os.environ.get('PORT', 10000))
    web_app.run(host='0.0.0.0', port=port)

Thread(target=run_web, daemon=True).start()

def main():
    logger.info("🚀 Запуск бота...")
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(next_flats, pattern="next_flats"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_flats))
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r'^(магазин|детск|сад|парк|метро)'), answer_question)],
        states={ANSWERING_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, answer_question)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)
    
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    app.run_polling()

if __name__ == "__main__":
    main()