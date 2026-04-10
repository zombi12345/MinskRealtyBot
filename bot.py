import os
import signal
import subprocess
import sys

# ===== ОСТАНОВКА СТАРЫХ ЭКЗЕМПЛЯРОВ =====
def stop_old_bots():
    """Останавливает все старые экземпляры бота"""
    try:
        # Находим все процессы
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        current_pid = os.getpid()
        
        lines = result.stdout.split('\n')
        killed = 0
        
        for line in lines:
            if 'bot.py' in line and 'python' in line:
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                        if pid != current_pid:
                            print(f"🔪 Останавливаем старый процесс PID: {pid}")
                            os.kill(pid, signal.SIGTERM)
                            killed += 1
                    except:
                        pass
        
        if killed > 0:
            print(f"✅ Остановлено {killed} старых процессов")
        else:
            print("✅ Нет старых процессов для остановки")
            
    except Exception as e:
        print(f"⚠️ Ошибка при остановке: {e}")

# Вызываем перед запуском
stop_old_bots()
# ========================================

# === ДАЛЕЕ ВАШ ОСНОВНОЙ КОД БОТА ===
import json
import re
import requests
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from geopy.distance import distance
from functools import lru_cache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

# Координаты станций метро
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

def calculate_distance_meters(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999999
    return int(distance((lat1, lon1), (lat2, lon2)).meters)

@lru_cache(maxsize=200)
def get_metro_distance(lat, lon):
    min_dist = 999999
    nearest = None
    for station, coord in METRO_STATIONS.items():
        dist = calculate_distance_meters(lat, lon, coord[0], coord[1])
        if dist < min_dist:
            min_dist = dist
            nearest = station
    return nearest, min_dist

def extract_user_needs(text):
    text_lower = text.lower()
    needs = {'rooms': None, 'max_price': None, 'floor': None, 'metro_station': None}
    
    if 'трёхкомнатн' in text_lower or 'трехкомнатн' in text_lower or '3-комнатн' in text_lower:
        needs['rooms'] = 3
    elif '2' in text_lower or 'двух' in text_lower:
        needs['rooms'] = 2
    elif '1' in text_lower or 'одно' in text_lower:
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
        score += 30
    
    max_score += 30
    if needs['max_price'] is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 30
            matches.append(f"✅ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 30
    
    max_score += 15
    if needs['floor'] is not None:
        flat_floor = flat.get('floor')
        if flat_floor and flat_floor == needs['floor']:
            score += 15
            matches.append(f"✅ Этаж {flat_floor}")
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
        f"🏠 *ИИ-консультант «Твоя Столица»*\n\n📊 *В базе:* {len(FLATS)} квартир\n\n📝 *Пример:*\n`Найди 3-комнатную до 100000$ рядом с метро Немига`",
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
    
    msg = f"🔍 *Варианты 1-{min(3, len(top))} из {len(top)}:*\n\n"
    for i, (flat, analysis) in enumerate(top[:3], 1):
        msg += format_flat_response(flat, analysis, i)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    keyboard = [[InlineKeyboardButton("📋 Следующие варианты", callback_data="next")]]
    await thinking.edit_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def next_flats(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    results = context.user_data.get('last_results', [])
    idx = context.user_data.get('idx', 3)
    
    if not results:
        await query.edit_message_text("Нет результатов.")
        return
    
    start, end = idx, min(idx + 3, len(results))
    if start >= len(results):
        start, end = 0, 3
    
    msg = f"🔍 *Варианты {start+1}-{end} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[start:end], start + 1):
        msg += format_flat_response(flat, analysis, i)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    context.user_data['idx'] = end
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def handle_message(update: Update, context):
    await search_flats(update, context)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(next_flats, pattern="next"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    app.run_polling()

if __name__ == "__main__":
    main()