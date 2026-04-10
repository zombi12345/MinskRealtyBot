import json
import re
import requests
import logging
import math
import os
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from geopy.distance import distance

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"
GEO_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImI0ZTcxNDQ2ZjU4ZjQwNDY5NDM4OTIyNGZjMjQzZWRmIiwiaCI6Im11cm11cjY0In0="

# Константы
CENTER_COORD = (53.9025, 27.5619)  # Центр Минска
MKAD_COORD = (53.8800, 27.6500)    # Примерная координата МКАД

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

# Функции для гео-анализа
def calculate_distance(lat1, lon1, lat2, lon2):
    """Расчет расстояния в км между двумя точками"""
    if not all([lat1, lon1, lat2, lon2]):
        return 999
    return distance((lat1, lon1), (lat2, lon2)).km

def find_nearby_pois(lat, lon, radius=1000):
    """Поиск POI через Overpass API"""
    if not lat or not lon:
        return {}
    
    query = f"""
    [out:json];
    (
      node["shop"~"supermarket|convenience"](around:{radius},{lat},{lon});
      node["highway"="bus_stop"](around:{radius},{lat},{lon});
      node["amenity"="school"](around:{radius},{lat},{lon});
      node["amenity"="pharmacy"](around:{radius},{lat},{lon});
      node["amenity"~"cafe|restaurant"](around:{radius},{lat},{lon});
      node["leisure"="park"](around:{radius},{lat},{lon});
    );
    out body;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=20)
        if r.status_code == 200:
            return parse_poi(r.json(), lat, lon)
    except Exception as e:
        logger.warning(f"POI API error: {e}")
    return {}

def parse_poi(data, lat, lon):
    """Парсинг результатов POI"""
    results = {'shops': [], 'bus_stops': [], 'schools': [], 'pharmacies': [], 'cafes': [], 'parks': []}
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        el_lat, el_lon = el.get('lat', 0), el.get('lon', 0)
        dist = int(distance((lat, lon), (el_lat, el_lon)).meters)
        name = tags.get('name', '')
        if 'shop' in tags:
            results['shops'].append({'name': name or 'Магазин', 'distance': dist})
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
    for k in results:
        results[k] = sorted(results[k], key=lambda x: x['distance'])[:3]
    return results

def format_nearby(nearby):
    """Форматирование POI для вывода"""
    if not nearby:
        return ""
    text = "\n   🏪 *Что рядом:*\n"
    if nearby.get('shops'):
        s = nearby['shops'][0]
        text += f"   • {s['name']} — {s['distance']} м\n"
    if nearby.get('bus_stops'):
        s = nearby['bus_stops'][0]
        text += f"   • Остановка \"{s['name']}\" — {s['distance']} м\n"
    if nearby.get('pharmacies'):
        s = nearby['pharmacies'][0]
        text += f"   • Аптека \"{s['name']}\" — {s['distance']} м\n"
    if nearby.get('schools'):
        text += f"   • Школа — {nearby['schools'][0]['distance']} м\n"
    if nearby.get('cafes'):
        s = nearby['cafes'][0]
        text += f"   • Кафе \"{s['name']}\" — {s['distance']} м\n"
    if nearby.get('parks'):
        text += f"   • Парк — {nearby['parks'][0]['distance']} м\n"
    return text

def parse_geo_request(text):
    """Понимает гео-запросы пользователя"""
    text_lower = text.lower()
    return {
        'want_center': any(w in text_lower for w in ['центр', 'центру', 'центра', 'центре', 'центральный']),
        'want_mkad': any(w in text_lower for w in ['мкад', 'кольцевая', 'мкаду', 'мкада']),
        'want_park': any(w in text_lower for w in ['парк', 'парка', 'парке', 'зеленый', 'сквер']),
        'want_metro': any(w in text_lower for w in ['метро', 'метра', 'подземка'])
    }

# Веб-сервер для Render
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "🤖 Бот для поиска квартир «Твоя Столица» работает!"

@web_app.route('/health')
def health():
    return {"status": "ok", "flats_count": len(FLATS)}

def run_web():
    port = int(os.environ.get('PORT', 10000))
    web_app.run(host='0.0.0.0', port=port)

Thread(target=run_web, daemon=True).start()
logger.info("🌐 Веб-сервер запущен")

async def start(update: Update, context):
    await update.message.reply_text(
        f"🏠 *ИИ-помощник «Твоя Столица»*\n\n"
        f"📊 В базе {len(FLATS)} квартир\n\n"
        f"🗺️ *Я понимаю гео-запросы:*\n"
        f"• `в центре` — рядом с центром\n"
        f"• `у МКАД` — ближе к кольцевой\n"
        f"• `с парком` — рядом есть парк\n"
        f"• `у метро` — близко к метро\n\n"
        f"📝 *Примеры:*\n"
        f"• `1 комнату до 70000 в центре`\n"
        f"• `двушку у МКАД с парком`\n"
        f"• `все квартиры`",
        parse_mode="Markdown"
    )

async def search(update: Update, context):
    text = update.message.text.lower()
    await update.message.chat.send_action(action="typing")
    
    # Парсинг базовых параметров
    rooms = None
    if '1' in text or 'одно' in text or 'однушк' in text:
        rooms = 1
    elif '2' in text or 'двух' in text or 'двушк' in text:
        rooms = 2
    
    max_price = None
    for p in re.findall(r'(\d{4,6})', text):
        price = int(p)
        if 30000 < price < 300000:
            max_price = price
            break
    
    # Парсинг гео-запроса
    geo = parse_geo_request(text)
    
    # Фильтрация
    results = []
    for flat in FLATS:
        if rooms is not None and flat['rooms'] != rooms:
            continue
        if max_price is not None and flat['price_usd'] > max_price:
            continue
        
        lat, lon = flat.get('lat'), flat.get('lon')
        
        # Гео-фильтры
        if geo['want_center'] and lat and lon:
            dist_to_center = calculate_distance(lat, lon, CENTER_COORD[0], CENTER_COORD[1])
            if dist_to_center > 3:
                continue
        
        if geo['want_mkad'] and lat and lon:
            dist_to_mkad = calculate_distance(lat, lon, MKAD_COORD[0], MKAD_COORD[1])
            if dist_to_mkad > 5:
                continue
        
        results.append(flat)
    
    # Сортировка по цене
    results = sorted(results, key=lambda x: x['price_usd'])[:5]
    
    if not results:
        await update.message.reply_text(
            "😔 *Ничего не найдено*\n\n"
            "Попробуйте другие критерии:\n"
            "• `1 комнату до 70000`\n"
            "• `квартиру в центре`\n"
            "• `все квартиры`",
            parse_mode="Markdown"
        )
        return
    
    # Формируем ответ с гео-анализом
    msg = f"🔍 *Найдено {len(results)} вариантов:*\n\n"
    for i, flat in enumerate(results, 1):
        lat, lon = flat.get('lat'), flat.get('lon')
        
        # Расчет расстояний
        dist_center = calculate_distance(lat, lon, CENTER_COORD[0], CENTER_COORD[1]) if lat and lon else None
        dist_mkad = calculate_distance(lat, lon, MKAD_COORD[0], MKAD_COORD[1]) if lat and lon else None
        
        # Поиск POI
        nearby = find_nearby_pois(lat, lon) if lat and lon else {}
        
        msg += f"{i}. *{flat['rooms']}к*, {flat['price_usd']}$\n"
        msg += f"   🏠 {flat['address']}\n"
        msg += f"   🏘 Район: {flat['district']}\n"
        if dist_center:
            msg += f"   📍 {dist_center:.1f} км от центра\n"
        if dist_mkad:
            msg += f"   🛣 {dist_mkad:.1f} км от МКАД\n"
        msg += format_nearby(nearby)
        msg += f"   🔗 [Смотреть на сайте]({flat['url']})\n\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def all_flats(update: Update, context):
    flats_sorted = sorted(FLATS, key=lambda x: x['price_usd'])
    msg = f"🏠 *Все квартиры ({len(flats_sorted)}):*\n\n"
    for i, flat in enumerate(flats_sorted[:20], 1):
        msg += f"{i}. *{flat['rooms']}к*, {flat['price_usd']}$\n"
        msg += f"   📍 {flat['address'][:45]}\n"
        msg += f"   🔗 [Смотреть]({flat['url']})\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def help_command(update: Update, context):
    await update.message.reply_text(
        "📖 *Как пользоваться ботом:*\n\n"
        "📝 *Базовые запросы:*\n"
        "• `1 комнату до 70000`\n"
        "• `2 комнаты до 90000`\n"
        "• `все квартиры`\n\n"
        "🗺️ *Гео-запросы:*\n"
        "• `квартиру в центре`\n"
        "• `у МКАД с парком`\n"
        "• `рядом с метро`\n\n"
        "🔧 *Команды:*\n"
        "/start - начать\n"
        "/help - справка\n"
        "/all - все квартиры",
        parse_mode="Markdown"
    )

def main():
    logger.info("🚀 Запуск бота...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("all", all_flats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    app.run_polling()

if __name__ == "__main__":
    main()