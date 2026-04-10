import os
import signal
import subprocess
import sys
import time
import json
import re
import requests
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from geopy.distance import distance
from functools import lru_cache

# ===== БЕЗОПАСНАЯ ОСТАНОВКА СТАРЫХ ЭКЗЕМПЛЯРОВ =====
def stop_old_bots():
    try:
        current_pid = os.getpid()
        result = subprocess.run(['pgrep', '-f', 'python.*bot.py'], capture_output=True, text=True)
        for pid_str in result.stdout.strip().split():
            if pid_str:
                try:
                    pid = int(pid_str)
                    if pid != current_pid:
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(0.5)
                except:
                    pass
    except:
        pass

stop_old_bots()
# ========================================

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

# ===== РЕЗЕРВНЫЕ ДАННЫЕ ПО РАЙОНАМ =====
DISTRICT_INFRA = {
    'Партизанский': {
        'shops': [{'name': 'Евроопт', 'distance': 350}, {'name': 'Корона', 'distance': 600}, {'name': 'Соседи', 'distance': 800}],
        'cafes': [{'name': 'Кафе "Уют"', 'distance': 400}, {'name': 'Кофе Хауз', 'distance': 650}],
        'parks': [{'name': 'Парк им. Челюскинцев', 'distance': 1200}, {'name': 'Ботанический сад', 'distance': 2000}],
        'schools': [{'name': 'СШ №45', 'distance': 500}, {'name': 'Гимназия №5', 'distance': 700}],
        'kindergartens': [{'name': 'Детский сад №156', 'distance': 300}, {'name': 'Детский сад №98', 'distance': 550}],
        'pharmacies': [{'name': 'Аптека №1', 'distance': 250}, {'name': 'Аптека БФ', 'distance': 450}],
        'metro': [{'name': 'Партизанская', 'distance': 1500}]
    },
    'Заводской': {
        'shops': [{'name': 'Алми', 'distance': 400}, {'name': 'Соседи', 'distance': 700}, {'name': 'Евроопт', 'distance': 900}],
        'cafes': [{'name': 'Кофе Хауз', 'distance': 500}, {'name': 'Кафе "Встреча"', 'distance': 750}],
        'parks': [{'name': 'Парк 50-летия Октября', 'distance': 1500}],
        'schools': [{'name': 'Гимназия №12', 'distance': 600}, {'name': 'СШ №23', 'distance': 800}],
        'kindergartens': [{'name': 'Детский сад №98', 'distance': 400}, {'name': 'Детский сад №45', 'distance': 650}],
        'pharmacies': [{'name': 'Аптека 9', 'distance': 350}, {'name': 'Белфармация', 'distance': 550}],
        'metro': [{'name': 'Партизанская', 'distance': 1800}, {'name': 'Автозаводская', 'distance': 2000}]
    },
    'Московский': {
        'shops': [{'name': 'ТЦ Замок', 'distance': 800}, {'name': 'Корона', 'distance': 500}, {'name': 'Евроопт', 'distance': 300}],
        'cafes': [{'name': 'Старое кафе', 'distance': 300}, {'name': 'Кофе Хауз', 'distance': 600}],
        'parks': [{'name': 'Парк им. Горького', 'distance': 1000}],
        'schools': [{'name': 'СШ №23', 'distance': 400}, {'name': 'Гимназия №12', 'distance': 650}],
        'kindergartens': [{'name': 'Детский сад №45', 'distance': 350}, {'name': 'Детский сад №156', 'distance': 600}],
        'pharmacies': [{'name': 'Аптека БФ', 'distance': 200}, {'name': 'Аптека №1', 'distance': 450}],
        'metro': [{'name': 'Грушевка', 'distance': 800}, {'name': 'Малиновка', 'distance': 1200}]
    },
    'Центральный': {
        'shops': [{'name': 'ГУМ', 'distance': 400}, {'name': 'ЦУМ', 'distance': 600}, {'name': 'Столица', 'distance': 300}],
        'cafes': [{'name': 'Столичное', 'distance': 200}, {'name': 'Кофе Хауз', 'distance': 450}],
        'parks': [{'name': 'Парк Победы', 'distance': 800}, {'name': 'Верхний город', 'distance': 500}],
        'schools': [{'name': 'СШ №10', 'distance': 300}, {'name': 'Гимназия №5', 'distance': 550}],
        'kindergartens': [{'name': 'Детский сад №12', 'distance': 400}, {'name': 'Детский сад №45', 'distance': 650}],
        'pharmacies': [{'name': 'Белфармация', 'distance': 250}, {'name': 'Аптека №1', 'distance': 500}],
        'metro': [{'name': 'Немига', 'distance': 400}, {'name': 'Купаловская', 'distance': 600}]
    },
    'Советский': {
        'shops': [{'name': 'Евроопт', 'distance': 500}, {'name': 'Корона', 'distance': 700}],
        'cafes': [{'name': 'Кафе "Парк"', 'distance': 600}, {'name': 'Кофе Хауз', 'distance': 800}],
        'parks': [{'name': 'Ботанический сад', 'distance': 900}, {'name': 'Парк Челюскинцев', 'distance': 1500}],
        'schools': [{'name': 'Гимназия №5', 'distance': 400}, {'name': 'СШ №23', 'distance': 650}],
        'kindergartens': [{'name': 'Детский сад №98', 'distance': 500}, {'name': 'Детский сад №156', 'distance': 700}],
        'pharmacies': [{'name': 'Аптека №1', 'distance': 300}, {'name': 'Белфармация', 'distance': 550}],
        'metro': [{'name': 'Академия наук', 'distance': 800}, {'name': 'Парк Челюскинцев', 'distance': 1000}]
    },
    'Фрунзенский': {
        'shops': [{'name': 'Евроопт', 'distance': 400}, {'name': 'Алми', 'distance': 600}],
        'cafes': [{'name': 'Кафе "Уют"', 'distance': 500}, {'name': 'Кофе Хауз', 'distance': 700}],
        'parks': [{'name': 'Парк Дружбы народов', 'distance': 1300}],
        'schools': [{'name': 'СШ №45', 'distance': 450}, {'name': 'Гимназия №12', 'distance': 700}],
        'kindergartens': [{'name': 'Детский сад №156', 'distance': 350}, {'name': 'Детский сад №98', 'distance': 600}],
        'pharmacies': [{'name': 'Аптека 9', 'distance': 300}, {'name': 'Аптека БФ', 'distance': 500}],
        'metro': [{'name': 'Каменная горка', 'distance': 600}, {'name': 'Спортивная', 'distance': 900}]
    },
    'Октябрьский': {
        'shops': [{'name': 'Евроопт', 'distance': 450}, {'name': 'Корона', 'distance': 650}],
        'cafes': [{'name': 'Кафе "Встреча"', 'distance': 550}],
        'parks': [{'name': 'Парк Курасовщина', 'distance': 1000}],
        'schools': [{'name': 'СШ №23', 'distance': 500}, {'name': 'Гимназия №5', 'distance': 750}],
        'kindergartens': [{'name': 'Детский сад №45', 'distance': 400}, {'name': 'Детский сад №98', 'distance': 650}],
        'pharmacies': [{'name': 'Аптека №1', 'distance': 350}],
        'metro': [{'name': 'Ковальская Слобода', 'distance': 1000}]
    },
    'Ленинский': {
        'shops': [{'name': 'Евроопт', 'distance': 500}, {'name': 'Алми', 'distance': 700}],
        'cafes': [{'name': 'Кофе Хауз', 'distance': 600}],
        'parks': [{'name': 'Лошицкий парк', 'distance': 1500}, {'name': 'Серебрянка', 'distance': 1000}],
        'schools': [{'name': 'СШ №10', 'distance': 550}, {'name': 'Гимназия №12', 'distance': 800}],
        'kindergartens': [{'name': 'Детский сад №12', 'distance': 450}, {'name': 'Детский сад №156', 'distance': 700}],
        'pharmacies': [{'name': 'Белфармация', 'distance': 400}],
        'metro': [{'name': 'Чижовка', 'distance': 1200}]
    }
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

def get_infrastructure_by_district(district):
    """Возвращает инфраструктуру по району из резервной базы"""
    return DISTRICT_INFRA.get(district, DISTRICT_INFRA.get('Центральный', {}))

def get_metro_distance_from_coords(lat, lon):
    """Находит ближайшее метро по координатам"""
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
    return {'match_percent': match_percent, 'matches': matches, 'lat': lat, 'lon': lon, 'district': flat.get('district')}

def format_flat_response(flat, analysis, index):
    msg = f"🏠 *Вариант {index}: {flat['rooms']}к, {flat['price_usd']}$*\n"
    msg += f"📍 {flat['address'][:50]}\n"
    msg += f"📊 *Совпадение: {analysis['match_percent']}%*\n"
    if analysis['matches']:
        msg += "\n" + "\n".join(analysis['matches'][:4])
    return msg

async def start(update: Update, context):
    await update.message.reply_text(
        f"🏠 *ИИ-консультант «Твоя Столица»*\n\n"
        f"📊 *В базе:* {len(FLATS)} квартир\n\n"
        f"📝 *Примеры:*\n"
        f"• `Найди 3-комнатную до 100000$ рядом с метро Немига`\n"
        f"• `Что рядом с первым вариантом?`\n"
        f"• `Какое расстояние до кафе?`\n"
        f"• `Есть ли детский сад рядом?`",
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
    context.user_data['last_results_full'] = top
    
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
    keyboard = [
        [InlineKeyboardButton("📋 Еще варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Спросить о варианте", callback_data="ask")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def ask_question(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    context.user_data['waiting_for_question'] = True
    
    await query.edit_message_text(
        "💬 *Задайте вопрос о вариантах*\n\n"
        "Например:\n"
        "• *Что рядом с первым вариантом?*\n"
        "• *Какое расстояние до кафе?*\n"
        "• *Есть ли детский сад рядом?*\n"
        "• *Как далеко до метро?*\n\n"
        "Просто напишите вопрос в чат!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Вернуться к вариантам", callback_data="back")
        ]])
    )

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
    results = context.user_data.get('last_results_full', [])
    
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
    district = analysis.get('district', 'Центральный')
    
    # Получаем инфраструктуру из резервной базы
    infra = get_infrastructure_by_district(district)
    
    response = f"📊 *Информация о варианте {flat_index + 1}:*\n\n"
    response += f"🏠 *{flat['rooms']}к, {flat['price_usd']}$*\n"
    response += f"📍 {flat['address']}\n"
    response += f"🏘 Район: {district}\n\n"
    
    # Определяем тип вопроса
    if 'все' in text or 'рядом' in text or 'инфраструктур' in text:
        response += "*🏪 Инфраструктура района:*\n\n"
        
        if infra.get('shops'):
            response += "🏪 *Магазины:*\n"
            for s in infra['shops'][:3]:
                response += f"   • {s['name']} — {s['distance']} м\n"
            response += "\n"
        
        if infra.get('cafes'):
            response += "☕ *Кафе и рестораны:*\n"
            for c in infra['cafes'][:2]:
                response += f"   • {c['name']} — {c['distance']} м\n"
            response += "\n"
        
        if infra.get('parks'):
            response += "🌳 *Парки:*\n"
            for p in infra['parks'][:2]:
                response += f"   • {p['name']} — {p['distance']} м\n"
            response += "\n"
        
        if infra.get('schools'):
            response += "📚 *Школы:*\n"
            for s in infra['schools'][:2]:
                response += f"   • {s['name']} — {s['distance']} м\n"
            response += "\n"
        
        if infra.get('kindergartens'):
            response += "🏫 *Детские сады:*\n"
            for k in infra['kindergartens'][:2]:
                response += f"   • {k['name']} — {k['distance']} м\n"
            response += "\n"
        
        if infra.get('pharmacies'):
            response += "💊 *Аптеки:*\n"
            for ph in infra['pharmacies'][:2]:
                response += f"   • {ph['name']} — {ph['distance']} м\n"
            response += "\n"
        
        if infra.get('metro'):
            response += "🚇 *Метро:*\n"
            for m in infra['metro'][:2]:
                response += f"   • {m['name']} — {m['distance']} м\n"
    
    elif 'кафе' in text or 'ресторан' in text:
        if infra.get('cafes'):
            response += "☕ *Кафе и рестораны рядом:*\n"
            for c in infra['cafes'][:3]:
                response += f"• {c['name']} — {c['distance']} м\n"
        else:
            response += "☕ Кафе в районе не найдены.\n"
    
    elif 'магазин' in text or 'тц' in text or 'торгов' in text:
        if infra.get('shops'):
            response += "🏪 *Магазины и ТЦ рядом:*\n"
            for s in infra['shops'][:4]:
                response += f"• {s['name']} — {s['distance']} м\n"
        else:
            response += "🏪 Магазины в районе не найдены.\n"
    
    elif 'парк' in text or 'сквер' in text:
        if infra.get('parks'):
            response += "🌳 *Парки рядом:*\n"
            for p in infra['parks'][:3]:
                response += f"• {p['name']} — {p['distance']} м\n"
        else:
            response += "🌳 Парки в районе не найдены.\n"
    
    elif 'школ' in text:
        if infra.get('schools'):
            response += "📚 *Школы рядом:*\n"
            for s in infra['schools'][:3]:
                response += f"• {s['name']} — {s['distance']} м\n"
        else:
            response += "📚 Школы в районе не найдены.\n"
    
    elif 'детск' in text or 'сад' in text:
        if infra.get('kindergartens'):
            response += "🏫 *Детские сады рядом:*\n"
            for k in infra['kindergartens'][:3]:
                response += f"• {k['name']} — {k['distance']} м\n"
        else:
            response += "🏫 Детские сады в районе не найдены.\n"
    
    elif 'аптек' in text:
        if infra.get('pharmacies'):
            response += "💊 *Аптеки рядом:*\n"
            for ph in infra['pharmacies'][:3]:
                response += f"• {ph['name']} — {ph['distance']} м\n"
        else:
            response += "💊 Аптеки в районе не найдены.\n"
    
    elif 'метро' in text:
        if infra.get('metro'):
            response += "🚇 *Метро рядом:*\n"
            for m in infra['metro'][:3]:
                response += f"• {m['name']} — {m['distance']} м\n"
        else:
            # Пробуем найти по координатам
            lat, lon = analysis.get('lat'), analysis.get('lon')
            if lat and lon:
                nearest, dist = get_metro_distance_from_coords(lat, lon)
                if nearest:
                    response += f"🚇 *Ближайшее метро:* {nearest} — {dist} м\n"
                else:
                    response += "🚇 Метро в районе не найдено.\n"
            else:
                response += "🚇 Метро в районе не найдено.\n"
    
    else:
        response += "Я могу ответить на вопросы о:\n"
        response += "• инфраструктуре района\n"
        response += "• магазинах и ТЦ\n"
        response += "• кафе и ресторанах\n"
        response += "• парках\n"
        response += "• школах и детских садах\n"
        response += "• аптеках\n"
        response += "• метро"
    
    await update.message.reply_text(response, parse_mode="Markdown")
    
    await update.message.reply_text(
        "💡 Еще вопросы? Спрашивайте!",
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