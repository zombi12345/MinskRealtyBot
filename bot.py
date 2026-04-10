 import json
import re
import requests
import logging
import os
import asyncio
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from geopy.distance import distance
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

CENTER_COORD = (53.9025, 27.5619)
MKAD_COORD = (53.8800, 27.6500)

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

# Кэш для POI (чтобы не запрашивать каждый раз)
poi_cache = {}
executor = ThreadPoolExecutor(max_workers=2)

# Категории для поиска POI
POI_CATEGORIES = {
    'shops': {'tags': ['shop', 'supermarket', 'convenience'], 'ru': 'магазины', 'emoji': '🏪', 'priority': 1},
    'malls': {'tags': ['mall', 'shopping_centre'], 'ru': 'ТЦ', 'emoji': '🏬', 'priority': 2},
    'kindergartens': {'tags': ['kindergarten'], 'ru': 'детские сады', 'emoji': '🏫', 'priority': 3},
    'schools': {'tags': ['school'], 'ru': 'школы', 'emoji': '📚', 'priority': 4},
    'pharmacies': {'tags': ['pharmacy'], 'ru': 'аптеки', 'emoji': '💊', 'priority': 5},
    'cafes': {'tags': ['cafe', 'restaurant', 'fast_food'], 'ru': 'кафе', 'emoji': '☕', 'priority': 6},
    'fitness': {'tags': ['fitness_centre', 'gym'], 'ru': 'фитнес', 'emoji': '💪', 'priority': 7},
    'cinemas': {'tags': ['cinema'], 'ru': 'кинотеатры', 'emoji': '🎬', 'priority': 8},
    'banks': {'tags': ['bank'], 'ru': 'банки', 'emoji': '🏦', 'priority': 9},
    'metro': {'tags': ['subway_entrance', 'subway'], 'ru': 'метро', 'emoji': '🚇', 'priority': 10},
    'parks': {'tags': ['park', 'garden'], 'ru': 'парки', 'emoji': '🌳', 'priority': 11},
    'hospitals': {'tags': ['hospital', 'clinic'], 'ru': 'больницы', 'emoji': '🏥', 'priority': 12}
}

def calculate_distance(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999
    return distance((lat1, lon1), (lat2, lon2)).km

@lru_cache(maxsize=100)
def find_nearby_pois_cached(lat, lon, radius=1500):
    """Кэшированный поиск POI"""
    if not lat or not lon:
        return {}
    
    # Сокращенный запрос для скорости
    query = f"""
    [out:json][timeout:15];
    (
      node["shop"~"supermarket|convenience|mall"](around:{radius},{lat},{lon});
      node["amenity"~"kindergarten|school|pharmacy|fitness_centre|cinema|bank|hospital|clinic|cafe|restaurant"](around:{radius},{lat},{lon});
      node["railway"="subway_entrance"](around:{radius},{lat},{lon});
      node["leisure"="park"](around:{radius},{lat},{lon});
    );
    out body;
    """
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data=query, timeout=15)
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
        elif tags.get('amenity') in ['cafe', 'restaurant', 'fast_food']:
            results['cafes'].append({'name': name or 'Кафе', 'distance': dist})
        elif tags.get('leisure') == 'park':
            results['parks'].append({'name': name or 'Парк', 'distance': dist})
        elif tags.get('railway') == 'subway_entrance':
            results['metro'].append({'name': name or 'Метро', 'distance': dist})
        elif tags.get('amenity') in ['hospital', 'clinic']:
            results['hospitals'].append({'name': name or 'Больница', 'distance': dist})
        elif tags.get('amenity') in ['fitness_centre', 'gym']:
            results['fitness'].append({'name': name or 'Фитнес', 'distance': dist})
        elif tags.get('amenity') == 'cinema':
            results['cinemas'].append({'name': name or 'Кинотеатр', 'distance': dist})
        elif tags.get('amenity') == 'bank':
            results['banks'].append({'name': name or 'Банк', 'distance': dist})
    
    for cat in results:
        results[cat] = sorted(results[cat], key=lambda x: x['distance'])[:2]
    return results

def extract_user_needs(text):
    text_lower = text.lower()
    
    needs = {
        'rooms': None,
        'max_price': None,
        'floor': None,
        'infrastructure': [],
        'original': text
    }
    
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
        'тц': 'malls', 'торговый центр': 'malls', 'молл': 'malls',
        'школа': 'schools', 'гимназия': 'schools',
        'сад': 'kindergartens', 'детский сад': 'kindergartens',
        'универ': 'universities', 'вуз': 'universities',
        'аптека': 'pharmacies', 'фитнес': 'fitness', 'кино': 'cinemas',
        'банк': 'banks', 'парк': 'parks', 'кафе': 'cafes',
        'больница': 'hospitals', 'метро': 'metro'
    }
    
    for keyword, infra_type in infra_keywords.items():
        if keyword in text_lower:
            needs['infrastructure'].append(infra_type)
    
    needs['infrastructure'] = list(set(needs['infrastructure']))[:3]  # Ограничиваем для скорости
    
    return needs

def score_flat(flat, needs):
    lat, lon = flat.get('lat'), flat.get('lon')
    
    score = 0
    max_score = 0
    matches = []
    mismatches = []
    
    max_score += 25
    if needs['rooms'] is not None:
        if flat['rooms'] == needs['rooms']:
            score += 25
            matches.append(f"✅ {flat['rooms']}-комнатная")
        else:
            mismatches.append(f"⚠️ {flat['rooms']}-комнатная (запрошена {needs['rooms']})")
    else:
        score += 25
    
    max_score += 25
    if needs['max_price'] is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 25
            matches.append(f"✅ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
        else:
            mismatches.append(f"⚠️ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 25
    
    max_score += 15
    if needs['floor'] is not None:
        flat_floor = flat.get('floor')
        if flat_floor and flat_floor == needs['floor']:
            score += 15
            matches.append(f"✅ Этаж {flat_floor}")
        elif flat_floor:
            mismatches.append(f"ℹ️ Этаж {flat_floor} (запрошен {needs['floor']})")
    else:
        score += 15
    
    # Быстрый POI анализ (только для запрошенной инфраструктуры)
    if lat and lon and needs['infrastructure']:
        poi = find_nearby_pois_cached(lat, lon)
        infra_score = 0
        infra_max = 35
        points_per_item = infra_max // len(needs['infrastructure'])
        
        for infra in needs['infrastructure']:
            if poi.get(infra):
                nearest = poi[infra][0]
                infra_score += points_per_item
                matches.append(f"✅ {POI_CATEGORIES.get(infra, {}).get('emoji', '📍')} {POI_CATEGORIES.get(infra, {}).get('ru', infra)}: {nearest['distance']} м")
            else:
                mismatches.append(f"⚠️ {POI_CATEGORIES.get(infra, {}).get('ru', infra)} не найдено в радиусе 1.5 км")
        
        score += min(infra_score, infra_max)
        max_score += infra_max
        flat['cached_poi'] = poi
    
    match_percent = int((score / max_score) * 100) if max_score > 0 else 0
    return {'match_percent': match_percent, 'matches': matches, 'mismatches': mismatches, 'poi': flat.get('cached_poi', {})}

def format_flat_response(flat, analysis, index, needs, show_full=False):
    msg = f"🏠 *Вариант {index}: {flat['rooms']}к, {flat['price_usd']}$*\n"
    msg += f"📍 {flat['address'][:50]}\n"
    msg += f"📊 *Совпадение: {analysis['match_percent']}%*\n"
    
    if analysis['matches']:
        msg += "\n✅ " + "\n✅ ".join(analysis['matches'][:3])
    
    if show_full and analysis['mismatches']:
        msg += "\n\n⚠️ " + "\n⚠️ ".join(analysis['mismatches'][:2])
    
    return msg

async def start(update: Update, context):
    await update.message.reply_text(
        "🏠 *ИИ-консультант «Твоя Столица»*\n\n"
        f"📊 *В базе:* {len(FLATS)} квартир\n\n"
        "🧠 *Понимаю:* метро, ТЦ, школы, сады, парки, фитнес и другие\n\n"
        "📝 *Пример:*\n"
        "`Найди 2-комнатную до 100000$, рядом метро и парк`\n\n"
        "⚡ *Быстрый ответ — кэширую данные для скорости*",
        parse_mode="Markdown"
    )

async def search_flats(update: Update, context):
    user_id = update.effective_user.id
    text = update.message.text
    chat_id = update.effective_chat.id
    
    # Отправляем сообщение "думаю"
    thinking_msg = await update.message.reply_text("🤔 *Анализирую варианты...*", parse_mode="Markdown")
    
    needs = extract_user_needs(text)
    
    scored_flats = []
    for flat in FLATS:
        analysis = score_flat(flat.copy(), needs)
        scored_flats.append((flat, analysis))
    
    scored_flats.sort(key=lambda x: x[1]['match_percent'], reverse=True)
    top_flats = scored_flats[:5]
    
    context.user_data['last_results'] = top_flats
    context.user_data['last_needs'] = needs
    context.user_data['last_message_id'] = thinking_msg.message_id
    
    if top_flats and top_flats[0][1]['match_percent'] >= 60:
        msg = f"🔍 *Найдено {len(top_flats)} вариантов:*\n\n"
        for i, (flat, analysis) in enumerate(top_flats[:3], 1):
            msg += format_flat_response(flat, analysis, i, needs, show_full=False)
            msg += "\n\n" + "─" * 30 + "\n\n"
    else:
        msg = "😔 *Идеальных вариантов не найдено*\n\n"
        msg += "🔍 *Лучшие альтернативы:*\n\n"
        for i, (flat, analysis) in enumerate(top_flats[:3], 1):
            msg += f"{i}. *{flat['rooms']}к, {flat['price_usd']}$* ({analysis['match_percent']}%)\n"
            msg += f"   📍 {flat['address'][:45]}\n\n"
    
    keyboard = [
        [InlineKeyboardButton("📋 Новые варианты", callback_data="next_flats")],
        [InlineKeyboardButton("❓ Задать вопрос о варианте", callback_data="ask_question")]
    ]
    
    await thinking_msg.edit_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Сохраняем ID сообщения с вариантами
    context.user_data['results_msg_id'] = thinking_msg.message_id

async def next_flats(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    results = context.user_data.get('last_results', [])
    current_idx = context.user_data.get('current_idx', 3)
    needs = context.user_data.get('last_needs', {})
    original_msg_id = context.user_data.get('results_msg_id')
    
    if not results:
        await query.edit_message_text("Нет сохраненных результатов. Напишите новый запрос.")
        return
    
    start_idx = current_idx
    end_idx = min(start_idx + 3, len(results))
    
    if start_idx >= len(results):
        context.user_data['current_idx'] = 0
        start_idx = 0
        end_idx = min(3, len(results))
    
    msg = f"🔍 *Варианты {start_idx+1}-{end_idx} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[start_idx:end_idx], start_idx + 1):
        msg += format_flat_response(flat, analysis, i, needs, show_full=False)
        msg += "\n\n" + "─" * 30 + "\n\n"
    
    context.user_data['current_idx'] = end_idx
    
    keyboard = [
        [InlineKeyboardButton("📋 Еще варианты", callback_data="next_flats")],
        [InlineKeyboardButton("❓ Задать вопрос о варианте", callback_data="ask_question")]
    ]
    
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def ask_question_start(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    # Сохраняем оригинальное сообщение
    context.user_data['question_mode'] = True
    
    await query.edit_message_text(
        "💬 *Задайте вопрос о любом варианте*\n\n"
        "Например:\n"
        "• *Какие магазины рядом с первым вариантом?*\n"
        "• *Что есть из инфраструктуры?*\n"
        "• *Как далеко до метро?*\n\n"
        "После ответа варианты останутся на месте!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Вернуться к вариантам", callback_data="back_to_results")
        ]])
    )

async def back_to_results(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    results = context.user_data.get('last_results', [])
    needs = context.user_data.get('last_needs', {})
    
    if not results:
        await query.edit_message_text("Нет сохраненных результатов. Напишите новый запрос.")
        return
    
    msg = f"🔍 *Варианты из последнего поиска:*\n\n"
    for i, (flat, analysis) in enumerate(results[:3], 1):
        msg += format_flat_response(flat, analysis, i, needs, show_full=False)
        msg += "\n\n" + "─" * 30 + "\n\n"
    
    keyboard = [
        [InlineKeyboardButton("📋 Новые варианты", callback_data="next_flats")],
        [InlineKeyboardButton("❓ Задать вопрос о варианте", callback_data="ask_question")]
    ]
    
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def answer_question(update: Update, context):
    text = update.message.text.lower()
    results = context.user_data.get('last_results', [])
    
    if not results:
        await update.message.reply_text("Сначала выполните поиск квартир.")
        return
    
    # Отправляем сообщение "думаю"
    thinking = await update.message.reply_text("🤔 *Ищу информацию...*", parse_mode="Markdown")
    
    flat, analysis = results[0]  # Берем первый вариант
    lat, lon = flat.get('lat'), flat.get('lon')
    
    response = ""
    
    if 'магазин' in text or 'тц' in text or 'торгов' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            shops = poi.get('shops', [])[:3]
            malls = poi.get('malls', [])[:2]
            if shops or malls:
                response = "🏪 *Магазины и ТЦ рядом:*\n"
                for s in shops:
                    response += f"• {s['name']} — {s['distance']} м\n"
                for m in malls:
                    response += f"• 🏬 ТЦ \"{m['name']}\" — {m['distance']} м\n"
            else:
                response = "🏪 Магазины в радиусе 1.5 км не найдены."
    
    elif 'школ' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            schools = poi.get('schools', [])[:3]
            if schools:
                response = "📚 *Школы рядом:*\n"
                for s in schools:
                    response += f"• {s['name']} — {s['distance']} м\n"
            else:
                response = "📚 Школы в радиусе 1.5 км не найдены."
    
    elif 'сад' in text or 'детск' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            kindergartens = poi.get('kindergartens', [])[:3]
            if kindergartens:
                response = "🏫 *Детские сады рядом:*\n"
                for k in kindergartens:
                    response += f"• {k['name']} — {k['distance']} м\n"
            else:
                response = "🏫 Детские сады в радиусе 1.5 км не найдены."
    
    elif 'парк' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            parks = poi.get('parks', [])[:3]
            if parks:
                response = "🌳 *Парки рядом:*\n"
                for p in parks:
                    response += f"• {p['name']} — {p['distance']} м\n"
            else:
                response = "🌳 Парки в радиусе 1.5 км не найдены."
    
    elif 'метро' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            metro = poi.get('metro', [])[:3]
            if metro:
                response = "🚇 *Метро рядом:*\n"
                for m in metro:
                    response += f"• {m['name']} — {m['distance']} м\n"
            else:
                response = "🚇 Метро в радиусе 1.5 км не найдено."
    
    elif 'фитнес' in text or 'спортзал' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            fitness = poi.get('fitness', [])[:3]
            if fitness:
                response = "💪 *Фитнес-центры рядом:*\n"
                for f in fitness:
                    response += f"• {f['name']} — {f['distance']} м\n"
            else:
                response = "💪 Фитнес-центры в радиусе 1.5 км не найдены."
    
    elif 'аптек' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            pharmacies = poi.get('pharmacies', [])[:3]
            if pharmacies:
                response = "💊 *Аптеки рядом:*\n"
                for p in pharmacies:
                    response += f"• {p['name']} — {p['distance']} м\n"
            else:
                response = "💊 Аптеки в радиусе 1.5 км не найдены."
    
    elif 'кафе' in text or 'ресторан' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            cafes = poi.get('cafes', [])[:3]
            if cafes:
                response = "☕ *Кафе и рестораны рядом:*\n"
                for c in cafes:
                    response += f"• {c['name']} — {c['distance']} м\n"
            else:
                response = "☕ Кафе в радиусе 1.5 км не найдены."
    
    elif 'банк' in text:
        if lat and lon:
            poi = find_nearby_pois_cached(lat, lon)
            banks = poi.get('banks', [])[:3]
            if banks:
                response = "🏦 *Банки рядом:*\n"
                for b in banks:
                    response += f"• {b['name']} — {b['distance']} м\n"
            else:
                response = "🏦 Банки в радиусе 1.5 км не найдены."
    
    else:
        response = "Я могу ответить на вопросы о:\n• магазинах и ТЦ\n• школах и детских садах\n• парках\n• метро\n• фитнесе\n• аптеках\n• кафе\n• банках"
    
    await thinking.edit_text(response, parse_mode="Markdown")
    
    # Не удаляем сообщение с вариантами — оно остается
    await update.message.reply_text(
        "💡 *Варианты остались выше* — вы можете продолжить просмотр.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Вернуться к вариантам", callback_data="back_to_results")
        ]])
    )

# Flask приложение
app = Flask(__name__)
telegram_app = Application.builder().token(BOT_TOKEN).build()

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(next_flats, pattern="next_flats"))
telegram_app.add_handler(CallbackQueryHandler(ask_question_start, pattern="ask_question"))
telegram_app.add_handler(CallbackQueryHandler(back_to_results, pattern="back_to_results"))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_flats))
telegram_app.add_handler(MessageHandler(filters.Regex(r'^(магазин|тц|школ|сад|парк|метро|фитнес|аптек|кафе|банк)'), answer_question))

@app.route('/')
def health_check():
    return "🤖 Бот работает!"

@app.route(f'/webhook/{BOT_TOKEN}', methods=['POST'])
async def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        await telegram_app.process_update(update)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error"}), 500

if __name__ == "__main__":
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    webhook_url = f"https://minsk-realty-bot.onrender.com/webhook/{BOT_TOKEN}"
    loop.run_until_complete(telegram_app.bot.set_webhook(webhook_url))
    logger.info(f"✅ Webhook set to {webhook_url}")
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)