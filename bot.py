import json
import re
import requests
import logging
import math
import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

# Получаем путь к файлу flats_data.json
current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')

# Загрузка данных
try:
    with open(json_path, 'r', encoding='utf-8') as f:
        FLATS = json.load(f)
    logger.info(f"✅ Загружено {len(FLATS)} квартир из {json_path}")
except FileNotFoundError:
    logger.error(f"❌ Файл не найден: {json_path}")
    FLATS = []
except json.JSONDecodeError as e:
    logger.error(f"❌ Ошибка парсинга JSON: {e}")
    FLATS = []

# Координаты районов
DISTRICT_COORDS = {
    'Заводской': (53.85, 27.60), 'Московский': (53.88, 27.53),
    'Октябрьский': (53.85, 27.55), 'Первомайский': (53.92, 27.62),
    'Советский': (53.92, 27.58), 'Центральный': (53.90, 27.56),
    'Фрунзенский': (53.89, 27.48), 'Ленинский': (53.86, 27.57),
    'Партизанский': (53.85, 27.65)
}

def find_nearby_pois(lat, lon, radius=800):
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
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=30)
        if r.status_code == 200:
            return parse_poi(r.json(), lat, lon)
    except Exception as e:
        logger.warning(f"POI API error: {e}")
    return {}

def parse_poi(data, lat, lon):
    results = {'shops': [], 'bus_stops': [], 'schools': [], 'pharmacies': [], 'cafes': [], 'parks': []}
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        el_lat, el_lon = el.get('lat', 0), el.get('lon', 0)
        dist = int(math.sqrt((lat - el_lat)**2 + (lon - el_lon)**2) * 111000)
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
    if not nearby:
        return ""
    text = "\n🏪 *Что рядом:*\n"
    if nearby.get('shops'):
        s = nearby['shops'][0]
        text += f"• {s['name']} — {s['distance']} м\n"
    if nearby.get('bus_stops'):
        s = nearby['bus_stops'][0]
        text += f"• Остановка \"{s['name']}\" — {s['distance']} м\n"
    if nearby.get('pharmacies'):
        s = nearby['pharmacies'][0]
        text += f"• Аптека \"{s['name']}\" — {s['distance']} м\n"
    if nearby.get('schools'):
        text += f"• Школа — {nearby['schools'][0]['distance']} м\n"
    if nearby.get('cafes'):
        s = nearby['cafes'][0]
        text += f"• Кафе \"{s['name']}\" — {s['distance']} м\n"
    if nearby.get('parks'):
        text += f"• Парк — {nearby['parks'][0]['distance']} м\n"
    return text

async def start(update: Update, context):
    await update.message.reply_text(
        f"🏠 *ИИ-помощник «Твоя Столица»*\n\n📊 В базе {len(FLATS)} квартир\n\nЯ анализирую инфраструктуру!\n\n📝 *Примеры:*\n• `1 комнату до 70000`\n• `все квартиры`",
        parse_mode="Markdown"
    )

async def search(update: Update, context):
    text = update.message.text.lower()
    await update.message.chat.send_action(action="typing")
    rooms = 1 if '1' in text or 'одно' in text else (2 if '2' in text or 'двух' in text else None)
    max_price = None
    for p in re.findall(r'(\d{4,6})', text):
        price = int(p)
        if 30000 < price < 300000:
            max_price = price
            break
    results = [f for f in FLATS if (rooms is None or f['rooms'] == rooms) and (max_price is None or f['price_usd'] <= max_price)]
    results = sorted(results, key=lambda x: x['price_usd'])[:5]
    if not results:
        await update.message.reply_text("😔 *Ничего не найдено*\nПопробуйте: `1 комнату до 70000`", parse_mode="Markdown")
        return
    msg = f"🔍 *Найдено {len(results)} вариантов:*\n\n"
    for i, flat in enumerate(results, 1):
        lat, lon = DISTRICT_COORDS.get(flat['district'], (53.90, 27.56))
        nearby = find_nearby_pois(lat, lon)
        msg += f"{i}. *{flat['rooms']}к*, {flat['price_usd']}$\n🏠 {flat['address']}\n🏘 {flat['district']}\n{format_nearby(nearby)}🔗 [Смотреть]({flat['url']})\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def all_flats(update: Update, context):
    flats = sorted(FLATS, key=lambda x: x['price_usd'])
    msg = f"🏠 *Все квартиры ({len(flats)}):*\n\n"
    for i, f in enumerate(flats[:20], 1):
        msg += f"{i}. *{f['rooms']}к*, {f['price_usd']}$\n📍 {f['address'][:45]}\n🔗 [Смотреть]({f['url']})\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def error_handler(update, context):
    logger.error(f"Ошибка: {context.error}")

def main():
    logger.info("🚀 Запуск бота...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("all", all_flats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))
    app.add_error_handler(error_handler)
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    app.run_polling()

if __name__ == "__main__":
    main()