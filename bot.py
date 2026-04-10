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

CENTER_COORD = (53.9025, 27.5619)
MKAD_COORD = (53.8800, 27.6500)

current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')

try:
    with open(json_path, 'r', encoding='utf-8') as f:
        FLATS = json.load(f)
    logger.info(f"✅ Загружено {len(FLATS)} квартир")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки: {e}")
    FLATS = []

POI_CATEGORIES = {
    'shops': {'ru': 'магазины', 'emoji': '🏪'},
    'malls': {'ru': 'ТЦ', 'emoji': '🏬'},
    'kindergartens': {'ru': 'детские сады', 'emoji': '🏫'},
    'schools': {'ru': 'школы', 'emoji': '📚'},
    'pharmacies': {'ru': 'аптеки', 'emoji': '💊'},
    'cafes': {'ru': 'кафе', 'emoji': '☕'},
    'parks': {'ru': 'парки', 'emoji': '🌳'},
    'metro': {'ru': 'метро', 'emoji': '🚇'}
}

def calculate_distance(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999
    return distance((lat1, lon1), (lat2, lon2)).km

@lru_cache(maxsize=100)
def find_nearby_pois_cached(lat, lon, radius=1000):
    if not lat or not lon:
        return {}
    query = f"""
    [out:json][timeout:10];
    (
      node["shop"~"supermarket|convenience|mall"](around:{radius},{lat},{lon});
      node["amenity"~"kindergarten|school|pharmacy|cafe|restaurant"](around:{radius},{lat},{lon});
      node["railway"="subway_entrance"](around:{radius},{lat},{lon});
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
    results = {cat: [] for cat in POI_CATEGORIES.keys()}
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        el_lat, el_lon = el.get('lat', 0), el.get('lon', 0)
        dist = int(distance((lat, lon), (el_lat, el_lon)).meters)
        name = tags.get('name', '')
        if tags.get('shop') == 'mall' or tags.get('shop') == 'shopping_centre':
            results['malls'].append({'name': name or 'ТЦ', 'distance': dist})
        elif tags.get('shop'):
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
        elif tags.get('railway') == 'subway_entrance':
            results['metro'].append({'name': name or 'Метро', 'distance': dist})
    for cat in results:
        results[cat] = sorted(results[cat], key=lambda x: x['distance'])[:2]
    return results

def extract_user_needs(text):
    text_lower = text.lower()
    needs = {'rooms': None, 'max_price': None, 'floor': None, 'infrastructure': []}
    if '2' in text_lower or 'двух' in text_lower or 'двушк' in text_lower:
        needs['rooms'] = 2
    elif '1' in text_lower or 'одно' in text_lower or 'однушк' in text_lower:
        needs['rooms'] = 1
    price_match = re.search(r'до\s*(\d{4,6})', text_lower)
    if price_match:
        needs['max_price'] = int(price_match.group(1))
    floor_match = re.search(r'(\d+)\s*этаж', text_lower)
    if floor_match:
        needs['floor'] = int(floor_match.group(1))
    infra_keywords = {
        'тц': 'malls', 'торговый центр': 'malls', 'школа': 'schools',
        'сад': 'kindergartens', 'детский сад': 'kindergartens',
        'аптека': 'pharmacies', 'парк': 'parks', 'кафе': 'cafes', 'метро': 'metro'
    }
    for keyword, infra_type in infra_keywords.items():
        if keyword in text_lower:
            needs['infrastructure'].append(infra_type)
    needs['infrastructure'] = list(set(needs['infrastructure']))[:2]
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
            matches.append(f"ℹ️ {flat['rooms']}-комнатная")
    else:
        score += 35
    max_score += 35
    if needs['max_price'] is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 35
            matches.append(f"✅ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
        else:
            matches.append(f"⚠️ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 35
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
    if lat and lon and needs['infrastructure']:
        poi = find_nearby_pois_cached(lat, lon)
        infra_score = 0
        infra_max = 15
        points = infra_max // len(needs['infrastructure']) if needs['infrastructure'] else 0
        for infra in needs['infrastructure']:
            if poi.get(infra):
                nearest = poi[infra][0]
                infra_score += points
                matches.append(f"✅ {POI_CATEGORIES.get(infra, {}).get('emoji', '📍')} {POI_CATEGORIES.get(infra, {}).get('ru', infra)}: {nearest['distance']} м")
        score += min(infra_score, infra_max)
        max_score += infra_max
    match_percent = int((score / max_score) * 100) if max_score > 0 else 0
    return {'match_percent': match_percent, 'matches': matches}

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
        "🧠 *Понимаю:* метро, ТЦ, школы, сады, парки\n\n"
        "📝 *Пример:*\n"
        "`Найди 2-комнатную до 100000$, рядом метро и парк`\n\n"
        "⚡ *Бот работает быстро с кэшированием*",
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
    if top[0][1]['match_percent'] >= 60:
        msg = f"🔍 *Найдено {len(top)} вариантов:*\n\n"
        for i, (flat, analysis) in enumerate(top[:3], 1):
            msg += format_flat_response(flat, analysis, i)
            msg += "\n\n" + "─" * 35 + "\n\n"
    else:
        msg = "😔 *Идеальных вариантов не найдено*\n\n🔍 *Лучшие альтернативы:*\n\n"
        for i, (flat, analysis) in enumerate(top[:3], 1):
            msg += f"{i}. *{flat['rooms']}к, {flat['price_usd']}$* ({analysis['match_percent']}%)\n   📍 {flat['address'][:45]}\n\n"
    keyboard = [[InlineKeyboardButton("📋 Следующие варианты", callback_data="next")]]
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
    keyboard = [[InlineKeyboardButton("📋 Еще варианты", callback_data="next")]]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(next_flats, pattern="next"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_flats))
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    app.run_polling()

if __name__ == "__main__":
    main()