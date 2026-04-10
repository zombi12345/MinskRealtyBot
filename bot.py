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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from geopy.distance import distance
from functools import lru_cache
from cachetools import TTLCache
from difflib import get_close_matches

# ===== НАСТРОЙКИ =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

# API КЛЮЧИ
YANDEX_GEO_KEY = "ac332495-30ba-43ef-a119-e842e8fe23b2"
ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImI0ZTcxNDQ2ZjU4ZjQwNDY5NDM4OTIyNGZjMjQzZWRmIiwiaCI6Im11cm11cjY0In0="

# Кэш
api_cache = TTLCache(maxsize=500, ttl=86400)

# Словарь для исправления опечаток
CORRECTIONS = {
    'немега': 'Немига', 'нимига': 'Немига', 'немего': 'Немига',
    'купаловская': 'Купаловская', 'октябрьская': 'Октябрьская',
    'площадь ленина': 'Площадь Ленина', 'институт культуры': 'Институт культуры',
    'грушевка': 'Грушевка', 'малиновка': 'Малиновка',
    'каменная горка': 'Каменная горка', 'спортивная': 'Спортивная',
    'пушкинская': 'Пушкинская', 'партизанская': 'Партизанская',
    'автозаводская': 'Автозаводская', 'могилевская': 'Могилевская',
    'уручье': 'Уручье', 'восток': 'Восток', 'московская': 'Московская',
    'детскй сад': 'детский сад', 'детски сад': 'детский сад',
    'школа': 'школа', 'школы': 'школа', 'школу': 'школа',
    'тц': 'торговый центр', 'трц': 'торговый центр',
    'аптека': 'аптека', 'аптеки': 'аптека',
    'кафе': 'кафе', 'кофейня': 'кафе', 'ресторан': 'кафе',
    'парк': 'парк', 'парки': 'парк', 'сквер': 'парк',
    'магазин': 'магазин', 'магазины': 'магазин', 'супермаркет': 'магазин'
}

# КООРДИНАТЫ СТАНЦИЙ МЕТРО
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

# Загрузка данных
current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')

try:
    with open(json_path, 'r', encoding='utf-8') as f:
        FLATS = json.load(f)
    logger.info(f"✅ Загружено {len(FLATS)} квартир")
except Exception as e:
    logger.error(f"❌ Ошибка загрузки: {e}")
    FLATS = []

# ===== ФУНКЦИИ ДЛЯ ИСПРАВЛЕНИЯ ОПЕЧАТОК =====
def correct_text(text):
    """Исправляет опечатки и сокращения в тексте"""
    text_lower = text.lower()
    for wrong, correct in CORRECTIONS.items():
        if wrong in text_lower:
            text_lower = text_lower.replace(wrong, correct.lower())
    return text_lower

# ===== ФУНКЦИИ ПОИСКА POI =====
def get_osm_pois(lat, lon, radius=1000):
    cache_key = f"osm_{lat}_{lon}_{radius}"
    if cache_key in api_cache:
        return api_cache[cache_key]
    
    query = f"""
    [out:json][timeout:15];
    (
      node["shop"~"supermarket|convenience|mall"](around:{radius},{lat},{lon});
      node["amenity"~"kindergarten|school|college|university|pharmacy|cafe|restaurant|fast_food"](around:{radius},{lat},{lon});
      node["leisure"="park"](around:{radius},{lat},{lon});
      node["highway"="bus_stop"](around:{radius},{lat},{lon});
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
    results = {
        'shops': [], 'cafes': [], 'parks': [], 
        'schools': [], 'universities': [], 'kindergartens': [], 
        'pharmacies': [], 'bus_stops': [], 'malls': []
    }
    
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
        elif tags.get('amenity') in ['college', 'university']:
            results['universities'].append({'name': name or 'Университет', 'distance': dist})
        elif tags.get('amenity') == 'pharmacy':
            results['pharmacies'].append({'name': name or 'Аптека', 'distance': dist})
        elif tags.get('amenity') in ['cafe', 'restaurant', 'fast_food']:
            results['cafes'].append({'name': name or 'Кафе', 'distance': dist})
        elif tags.get('leisure') == 'park':
            results['parks'].append({'name': name or 'Парк', 'distance': dist})
        elif tags.get('highway') == 'bus_stop':
            results['bus_stops'].append({'name': name or 'Остановка', 'distance': dist})
    
    for cat in results:
        results[cat] = sorted(results[cat], key=lambda x: x['distance'])[:5]
    return results

def calculate_distance_meters(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999999
    return int(distance((lat1, lon1), (lat2, lon2)).meters)

def check_poi_nearby(lat, lon, poi_type, max_distance=1000):
    """Проверяет наличие POI рядом"""
    if not lat or not lon:
        return False, None
    poi = get_osm_pois(lat, lon)
    if poi.get(poi_type):
        nearest = poi[poi_type][0]
        if nearest['distance'] <= max_distance:
            return True, nearest
    return False, None

def check_metro_nearby(lat, lon, metro_name, max_distance=1500):
    if not lat or not lon:
        return False, None
    if metro_name not in METRO_STATIONS:
        return False, None
    station_coord = METRO_STATIONS[metro_name]
    dist = calculate_distance_meters(lat, lon, station_coord[0], station_coord[1])
    if dist <= max_distance:
        return True, dist
    return False, dist

def extract_user_needs(text):
    text_corrected = correct_text(text)
    needs = {
        'rooms': None,
        'max_price': None,
        'floor': None,
        'metro_station': None,
        'want_kindergarten': False,
        'want_school': False,
        'want_university': False,
        'want_shop': False,
        'want_mall': False,
        'want_cafe': False,
        'want_park': False,
        'want_bus_stop': False,
        'want_pharmacy': False,
        'explanation': []
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
    price_match = re.search(r'до\s*(\d{4,6})', text_corrected)
    if price_match:
        needs['max_price'] = int(price_match.group(1))
        needs['explanation'].append(f"💰 до {needs['max_price']}$")
    
    # Этаж
    floor_match = re.search(r'(\d+)\s*этаж', text_corrected)
    if floor_match:
        needs['floor'] = int(floor_match.group(1))
        needs['explanation'].append(f"📌 на {needs['floor']} этаже")
    
    # Станция метро
    for station in METRO_STATIONS.keys():
        if station.lower() in text_corrected:
            needs['metro_station'] = station
            needs['explanation'].append(f"🚇 рядом с метро {station}")
            break
    
    # Учебные заведения
    if 'детский сад' in text_corrected or 'сад' in text_corrected:
        needs['want_kindergarten'] = True
        needs['explanation'].append("🏫 рядом детский сад")
    if 'школ' in text_corrected:
        needs['want_school'] = True
        needs['explanation'].append("📚 рядом школа")
    if 'университет' in text_corrected or 'универ' in text_corrected or 'вуз' in text_corrected:
        needs['want_university'] = True
        needs['explanation'].append("🎓 рядом университет")
    
    # Магазины и ТЦ
    if 'магазин' in text_corrected:
        needs['want_shop'] = True
        needs['explanation'].append("🏪 рядом магазин")
    if 'торговый центр' in text_corrected or 'тц' in text_corrected or 'трц' in text_corrected:
        needs['want_mall'] = True
        needs['explanation'].append("🏬 рядом ТЦ")
    
    # Кафе
    if 'кафе' in text_corrected or 'кофейн' in text_corrected or 'ресторан' in text_corrected:
        needs['want_cafe'] = True
        needs['explanation'].append("☕ рядом кафе")
    
    # Парк
    if 'парк' in text_corrected or 'сквер' in text_corrected:
        needs['want_park'] = True
        needs['explanation'].append("🌳 рядом парк")
    
    # Остановка
    if 'остановк' in text_corrected:
        needs['want_bus_stop'] = True
        needs['explanation'].append("🚌 рядом остановка")
    
    # Аптека
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
    
    # Комнаты (20 баллов)
    max_score += 20
    if needs['rooms'] is not None:
        if flat['rooms'] == needs['rooms']:
            score += 20
            matched_criteria.append(f"✅ {flat['rooms']}-комнатная")
        else:
            failed_criteria.append(f"❌ {flat['rooms']}-комнатная")
    else:
        score += 20
        matched_criteria.append(f"ℹ️ {flat['rooms']}-комнатная")
    
    # Цена (20 баллов)
    max_score += 20
    if needs['max_price'] is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 20
            matched_criteria.append(f"✅ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
        else:
            failed_criteria.append(f"❌ {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 20
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
        is_near, dist = check_metro_nearby(lat, lon, needs['metro_station'])
        if is_near:
            score += 15
            matched_criteria.append(f"✅ метро {needs['metro_station']}: {dist} м")
        else:
            failed_criteria.append(f"❌ метро {needs['metro_station']}: {dist} м")
    else:
        score += 15
    
    # Детский сад (5 баллов)
    if needs['want_kindergarten']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'kindergartens')
        if has:
            score += 5
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
            failed_criteria.append(f"❌ Школа не найдена в радиусе 1 км")
    
    # Университет (5 баллов)
    if needs['want_university']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'universities')
        if has:
            score += 5
            matched_criteria.append(f"✅ Университет \"{info['name']}\": {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Университет не найден в радиусе 1 км")
    
    # Магазин (5 баллов)
    if needs['want_shop']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'shops')
        if has:
            score += 5
            matched_criteria.append(f"✅ Магазин \"{info['name']}\": {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Магазин не найден в радиусе 1 км")
    
    # ТЦ (5 баллов)
    if needs['want_mall']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'malls')
        if has:
            score += 5
            matched_criteria.append(f"✅ ТЦ \"{info['name']}\": {info['distance']} м")
        else:
            failed_criteria.append(f"❌ ТЦ не найден в радиусе 1 км")
    
    # Кафе (5 баллов)
    if needs['want_cafe']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'cafes')
        if has:
            score += 5
            matched_criteria.append(f"✅ Кафе \"{info['name']}\": {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Кафе не найдено в радиусе 1 км")
    
    # Парк (5 баллов)
    if needs['want_park']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'parks')
        if has:
            score += 5
            matched_criteria.append(f"✅ Парк \"{info['name']}\": {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Парк не найден в радиусе 1 км")
    
    # Остановка (5 баллов)
    if needs['want_bus_stop']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'bus_stops')
        if has:
            score += 5
            matched_criteria.append(f"✅ Остановка \"{info['name']}\": {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Остановка не найдена в радиусе 1 км")
    
    # Аптека (5 баллов)
    if needs['want_pharmacy']:
        max_score += 5
        has, info = check_poi_nearby(lat, lon, 'pharmacies')
        if has:
            score += 5
            matched_criteria.append(f"✅ Аптека \"{info['name']}\": {info['distance']} м")
        else:
            failed_criteria.append(f"❌ Аптека не найдена в радиусе 1 км")
    
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
    msg += f"📊 *Совпадение с запросом: {analysis['match_percent']}%*\n\n"
    
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

async def start(update: Update, context):
    await update.message.reply_text(
        "🏠 *Добро пожаловать в ИИ-консультанта «Твоя Столица»!*\n\n"
        "📊 *В базе:* " + str(len(FLATS)) + " квартир\n\n"
        "📖 *Как пользоваться ботом:*\n\n"
        "1️⃣ *Опишите желаемую квартиру* простыми словами\n"
        "2️⃣ *Укажите важные параметры:*\n"
        "   • Количество комнат (1, 2, 3)\n"
        "   • Бюджет (до 70000$)\n"
        "   • Этаж (на 3 этаже)\n"
        "   • Район или станцию метро (Немига)\n"
        "   • Что должно быть рядом (детский сад, школа, парк, ТЦ, кафе)\n\n"
        "📝 *Примеры запросов:*\n"
        "• `Найди 1 комнату до 70000$ рядом с метро Немига и чтобы был детский сад`\n"
        "• `2 комнаты до 90000$ рядом школа и парк`\n"
        "• `Квартиру рядом с ТЦ и кафе`\n\n"
        "💡 *Бот понимает опечатки и сокращения!*\n"
        "   Например: «немега», «детскй сад», «тц»\n\n"
        "🔍 *После результатов можно спросить:*\n"
        "   • «Какие магазины рядом с первым вариантом?»\n"
        "   • «Что есть из инфраструктуры?»\n\n"
        "👇 *Нажмите кнопку ниже, чтобы начать поиск*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Начать поиск квартиры", switch_inline_query_current_chat="")],
            [InlineKeyboardButton("❓ Помощь", callback_data="help")],
            [InlineKeyboardButton("📋 Все квартиры", callback_data="all_flats")]
        ])
    )

async def help_command(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "❓ *Помощь по боту*\n\n"
        "📝 *Как составить запрос:*\n"
        "• Укажите количество комнат: 1, 2, 3\n"
        "• Укажите цену: до 70000$\n"
        "• Укажите этаж: на 3 этаже\n"
        "• Укажите метро: Немига, Каменная горка и т.д.\n"
        "• Укажите что нужно рядом: детский сад, школа, парк, ТЦ, кафе, магазин, аптека\n\n"
        "📋 *Доступные фильтры:*\n"
        "🏠 Комнатность\n"
        "💰 Цена\n"
        "📌 Этаж\n"
        "🚇 Станция метро\n"
        "🏫 Детский сад\n"
        "📚 Школа\n"
        "🎓 Университет\n"
        "🏪 Магазин\n"
        "🏬 Торговый центр\n"
        "☕ Кафе/ресторан\n"
        "🌳 Парк\n"
        "🚌 Остановка\n"
        "💊 Аптека\n\n"
        "💡 *Бот исправляет опечатки!* Например:\n"
        "• «немега» → Немига\n"
        "• «детскй сад» → детский сад\n"
        "• «тц» → торговый центр\n\n"
        "🔗 *Ссылка на сайт:* [t-s.by](https://www.t-s.by)",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_start")]
        ])
    )

async def all_flats_command(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    sorted_flats = sorted(FLATS, key=lambda x: x['price_usd'])
    msg = "🏠 *Все квартиры в базе:*\n\n"
    for i, flat in enumerate(sorted_flats[:15], 1):
        msg += f"{i}. *{flat['rooms']}к*, {flat['price_usd']}$\n"
        msg += f"   📍 {flat['address'][:45]}\n"
        msg += f"   🔗 [Смотреть]({flat['url']})\n\n"
    
    await query.edit_message_text(
        msg,
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_start")]
        ])
    )

async def back_to_start(update: Update, context):
    query = update.callback_query
    await query.answer()
    await start(update, context)

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
    
    if not top:
        await thinking.edit_text(
            "😔 *Ничего не найдено*\n\n"
            "Попробуйте изменить параметры поиска:\n"
            "• Увеличить бюджет\n"
            "• Убрать некоторые фильтры\n"
            "• Расширить географию поиска\n\n"
            "Например: `1 комнату до 80000$`",
            parse_mode="Markdown"
        )
        return
    
    msg = f"🔍 *Результаты поиска*\n\n"
    msg += f"📋 *Ваш запрос:*\n"
    for exp in needs['explanation']:
        msg += f"{exp}\n"
    msg += f"\n{'─' * 40}\n\n"
    
    if top[0][1]['match_percent'] >= 70:
        msg += f"✨ *Найдено {len(top)} отличных вариантов:*\n\n"
    elif top[0][1]['match_percent'] >= 50:
        msg += f"📌 *Найдено {len(top)} частично подходящих вариантов:*\n\n"
    else:
        msg += f"⚠️ *Найдено {len(top)} вариантов с минимальным совпадением:*\n\n"
    
    for i, (flat, analysis) in enumerate(top[:3], 1):
        msg += format_flat_response(flat, analysis, i, needs)
        msg += "\n\n" + "─" * 35 + "\n\n"
    
    if len(top) > 3:
        msg += f"_Показаны топ-3 из {len(top)}. Нажмите кнопку для просмотра следующих вариантов._"
    
    keyboard = [
        [InlineKeyboardButton("📋 Следующие варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("🔍 Новый поиск", callback_data="new_search")]
    ]
    
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
    keyboard = [
        [InlineKeyboardButton("📋 Еще варианты", callback_data="next")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("🔍 Новый поиск", callback_data="new_search")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(keyboard))

async def new_search(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 *Напишите новый запрос*\n\n"
        "Например:\n"
        "• `1 комнату до 70000$`\n"
        "• `2 комнаты рядом с метро Немига`\n"
        "• `Квартиру с детским садом и парком`",
        parse_mode="Markdown"
    )

# ===== ВЕБ-СЕРВЕР ДЛЯ RENDER =====
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
    flask_app.run(host='0.0.0.0', port=port)

web_thread = Thread(target=run_web, daemon=True)
web_thread.start()

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(next_flats, pattern="next"))
    app.add_handler(CallbackQueryHandler(help_command, pattern="help"))
    app.add_handler(CallbackQueryHandler(all_flats_command, pattern="all_flats"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="back_to_start"))
    app.add_handler(CallbackQueryHandler(new_search, pattern="new_search"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_flats))
    
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    app.run_polling()

if __name__ == "__main__":
    main()