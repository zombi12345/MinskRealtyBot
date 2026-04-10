import json
import re
import requests
import logging
import os
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

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

# Словарь сокращений и синонимов
SYNONYMS = {
    'тц': ['торговый центр', 'тц', 'торгово-развлекательный', 'трц', 'молл', 'mall'],
    'школа': ['школа', 'школа', 'гимназия', 'лицей', 'учебное заведение'],
    'сад': ['детский сад', 'сад', 'детсад', 'доу', 'ясли'],
    'универ': ['университет', 'универ', 'вуз', 'институт', 'академия'],
    'аптека': ['аптека', 'аптечный пункт', 'фармация'],
    'фитнес': ['фитнес', 'спортзал', 'тренажерный зал', 'спортклуб', 'бассейн'],
    'кино': ['кинотеатр', 'кино', 'кинозал'],
    'банк': ['банк', 'банкомат', 'отделение банка', 'финансы'],
    'метро': ['метро', 'метрополитен', 'подземка', 'станция метро'],
    'парк': ['парк', 'сквер', 'зеленая зона', 'роща', 'аллея'],
    'мкад': ['мкад', 'мкаду', 'мкада', 'кольцевая', 'окружная'],
    'центр': ['центр', 'центру', 'центра', 'центре', 'в центре']
}

# Категории для поиска POI
POI_CATEGORIES = {
    'shops': {'tags': ['shop', 'supermarket', 'convenience'], 'ru': 'магазины', 'emoji': '🏪'},
    'malls': {'tags': ['mall', 'shopping_centre', 'shopping_centre'], 'ru': 'торговые центры', 'emoji': '🏬'},
    'kindergartens': {'tags': ['kindergarten'], 'ru': 'детские сады', 'emoji': '🏫'},
    'schools': {'tags': ['school'], 'ru': 'школы', 'emoji': '📚'},
    'universities': {'tags': ['university', 'college'], 'ru': 'университеты', 'emoji': '🎓'},
    'pharmacies': {'tags': ['pharmacy'], 'ru': 'аптеки', 'emoji': '💊'},
    'cafes': {'tags': ['cafe', 'restaurant', 'fast_food'], 'ru': 'кафе и рестораны', 'emoji': '☕'},
    'fitness': {'tags': ['fitness_centre', 'gym', 'sports_centre'], 'ru': 'фитнес-центры', 'emoji': '💪'},
    'cinemas': {'tags': ['cinema', 'theatre'], 'ru': 'кинотеатры', 'emoji': '🎬'},
    'banks': {'tags': ['bank'], 'ru': 'банки', 'emoji': '🏦'},
    'metro': {'tags': ['subway_entrance', 'subway'], 'ru': 'метро', 'emoji': '🚇'},
    'parks': {'tags': ['park', 'garden', 'recreation_ground'], 'ru': 'парки', 'emoji': '🌳'},
    'bus_stops': {'tags': ['bus_stop'], 'ru': 'остановки', 'emoji': '🚌'},
    'hospitals': {'tags': ['hospital', 'clinic'], 'ru': 'больницы и поликлиники', 'emoji': '🏥'}
}

def calculate_distance(lat1, lon1, lat2, lon2):
    if not all([lat1, lon1, lat2, lon2]):
        return 999
    from geopy.distance import distance
    return distance((lat1, lon1), (lat2, lon2)).km

def find_nearby_pois(lat, lon, radius=1500):
    """Поиск всех POI через Overpass API"""
    if not lat or not lon:
        return {}
    
    # Строим запрос для всех категорий
    category_queries = []
    for cat_key, cat_info in POI_CATEGORIES.items():
        for tag in cat_info['tags']:
            if tag == 'subway_entrance':
                category_queries.append(f'node["railway"="{tag}"](around:{radius},{lat},{lon});')
            elif tag == 'bus_stop':
                category_queries.append(f'node["highway"="{tag}"](around:{radius},{lat},{lon});')
            elif tag == 'park':
                category_queries.append(f'node["leisure"="{tag}"](around:{radius},{lat},{lon});')
            else:
                category_queries.append(f'node["amenity"="{tag}"](around:{radius},{lat},{lon});')
                category_queries.append(f'node["shop"="{tag}"](around:{radius},{lat},{lon});')
    
    query = f"""
    [out:json];
    (
      {chr(10).join(category_queries)}
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
    """Парсинг результатов POI"""
    results = {cat: [] for cat in POI_CATEGORIES.keys()}
    
    for el in data.get('elements', []):
        tags = el.get('tags', {})
        el_lat, el_lon = el.get('lat', 0), el.get('lon', 0)
        from geopy.distance import distance
        dist = int(distance((lat, lon), (el_lat, el_lon)).meters)
        name = tags.get('name', '')
        
        # Определяем категорию
        if tags.get('shop') == 'mall' or tags.get('shop') == 'shopping_centre':
            results['malls'].append({'name': name or 'ТЦ', 'distance': dist})
        elif tags.get('shop'):
            results['shops'].append({'name': name or 'Магазин', 'distance': dist})
        elif tags.get('amenity') == 'kindergarten':
            results['kindergartens'].append({'name': name or 'Детский сад', 'distance': dist})
        elif tags.get('amenity') == 'school':
            results['schools'].append({'name': name or 'Школа', 'distance': dist})
        elif tags.get('amenity') in ['university', 'college']:
            results['universities'].append({'name': name or 'Университет', 'distance': dist})
        elif tags.get('amenity') == 'pharmacy':
            results['pharmacies'].append({'name': name or 'Аптека', 'distance': dist})
        elif tags.get('amenity') in ['cafe', 'restaurant', 'fast_food']:
            results['cafes'].append({'name': name or 'Кафе', 'distance': dist})
        elif tags.get('leisure') == 'park':
            results['parks'].append({'name': name or 'Парк', 'distance': dist})
        elif tags.get('railway') == 'subway_entrance':
            results['metro'].append({'name': name or 'Метро', 'distance': dist})
        elif tags.get('highway') == 'bus_stop':
            results['bus_stops'].append({'name': name or 'Остановка', 'distance': dist})
        elif tags.get('amenity') in ['hospital', 'clinic']:
            results['hospitals'].append({'name': name or 'Больница', 'distance': dist})
        elif tags.get('amenity') in ['fitness_centre', 'gym', 'sports_centre']:
            results['fitness'].append({'name': name or 'Фитнес', 'distance': dist})
        elif tags.get('amenity') == 'cinema' or tags.get('amenity') == 'theatre':
            results['cinemas'].append({'name': name or 'Кинотеатр', 'distance': dist})
        elif tags.get('amenity') == 'bank':
            results['banks'].append({'name': name or 'Банк', 'distance': dist})
    
    # Сортируем и оставляем топ-3
    for cat in results:
        results[cat] = sorted(results[cat], key=lambda x: x['distance'])[:3]
    
    return results

def extract_user_needs(text):
    """Извлекает все потребности пользователя из запроса"""
    text_lower = text.lower()
    
    needs = {
        'rooms': None,
        'max_price': None,
        'floor': None,
        'geo': [],
        'infrastructure': [],
        'original': text
    }
    
    # Комнаты
    if '2-комнатн' in text_lower or 'двухкомнатн' in text_lower or '2 комнаты' in text_lower or 'двушк' in text_lower:
        needs['rooms'] = 2
    elif '1-комнатн' in text_lower or 'однокомнатн' in text_lower or '1 комнату' in text_lower or 'однушк' in text_lower:
        needs['rooms'] = 1
    
    # Цена
    price_match = re.search(r'до\s*(\d{4,6})', text_lower)
    if price_match:
        needs['max_price'] = int(price_match.group(1))
    
    # Этаж
    floor_match = re.search(r'(\d+)\s*этаж', text_lower)
    if floor_match:
        needs['floor'] = int(floor_match.group(1))
    
    # Гео-параметры
    geo_keywords = {
        'центр': 'center', 'мкад': 'mkad', 'кольцевая': 'mkad',
        'метро': 'metro', 'метра': 'metro'
    }
    for keyword, geo_type in geo_keywords.items():
        if keyword in text_lower:
            needs['geo'].append(geo_type)
    
    # Инфраструктура (по синонимам)
    infra_keywords = {
        'тц': 'malls', 'торговый центр': 'malls', 'молл': 'malls',
        'школа': 'schools', 'гимназия': 'schools',
        'сад': 'kindergartens', 'детский сад': 'kindergartens',
        'универ': 'universities', 'вуз': 'universities', 'институт': 'universities',
        'аптека': 'pharmacies', 'аптечный': 'pharmacies',
        'фитнес': 'fitness', 'спортзал': 'fitness', 'тренажерка': 'fitness',
        'кино': 'cinemas', 'кинотеатр': 'cinemas',
        'банк': 'banks', 'банкомат': 'banks',
        'парк': 'parks', 'сквер': 'parks',
        'кафе': 'cafes', 'ресторан': 'cafes',
        'больница': 'hospitals', 'поликлиника': 'hospitals'
    }
    
    for keyword, infra_type in infra_keywords.items():
        if keyword in text_lower:
            needs['infrastructure'].append(infra_type)
    
    # Убираем дубликаты
    needs['geo'] = list(set(needs['geo']))
    needs['infrastructure'] = list(set(needs['infrastructure']))
    
    return needs

def score_flat(flat, needs):
    """Оценивает квартиру по соответствию потребностям (0-100)"""
    score = 0
    max_score = 0
    matches = []
    mismatches = []
    
    # Комнаты (20 баллов)
    max_score += 20
    if needs['rooms'] is not None:
        if flat['rooms'] == needs['rooms']:
            score += 20
            matches.append(f"✅ {flat['rooms']}-комнатная — соответствует")
        else:
            mismatches.append(f"⚠️ {flat['rooms']}-комнатная (запрошена {needs['rooms']}-комнатная)")
    else:
        score += 20
    
    # Цена (20 баллов)
    max_score += 20
    if needs['max_price'] is not None:
        if flat['price_usd'] <= needs['max_price']:
            score += 20
            matches.append(f"✅ Цена {flat['price_usd']}$ — входит в бюджет")
        else:
            mismatches.append(f"⚠️ Цена {flat['price_usd']}$ (бюджет {needs['max_price']}$)")
    else:
        score += 20
    
    # Этаж (10 баллов)
    max_score += 10
    if needs['floor'] is not None:
        flat_floor = flat.get('floor')
        if flat_floor and flat_floor == needs['floor']:
            score += 10
            matches.append(f"✅ Этаж {flat_floor} — соответствует")
        elif flat_floor:
            mismatches.append(f"ℹ️ Этаж {flat_floor} (запрошен {needs['floor']})")
    else:
        score += 10
    
    # Гео и инфраструктура (50 баллов) - через POI
    lat, lon = flat.get('lat'), flat.get('lon')
    if lat and lon:
        poi = find_nearby_pois(lat, lon)
        
        # Гео-параметры (20 баллов)
        geo_score = 0
        geo_max = 20
        if 'center' in needs['geo']:
            dist_center = calculate_distance(lat, lon, CENTER_COORD[0], CENTER_COORD[1])
            if dist_center < 3:
                geo_score += 10
                matches.append(f"📍 {dist_center:.1f} км от центра — близко к центру")
            else:
                mismatches.append(f"📍 {dist_center:.1f} км от центра (далековато)")
        
        if 'mkad' in needs['geo']:
            dist_mkad = calculate_distance(lat, lon, MKAD_COORD[0], MKAD_COORD[1])
            if dist_mkad < 5:
                geo_score += 10
                matches.append(f"🛣 {dist_mkad:.1f} км от МКАД — удобный выезд")
            else:
                mismatches.append(f"🛣 {dist_mkad:.1f} км от МКАД")
        
        if 'metro' in needs['geo']:
            if poi.get('metro'):
                nearest = poi['metro'][0]
                if nearest['distance'] < 1000:
                    geo_score += 10
                    matches.append(f"🚇 Метро \"{nearest['name']}\" — {nearest['distance']} м")
                else:
                    mismatches.append(f"🚇 Метро далеко ({nearest['distance']} м)")
            else:
                mismatches.append(f"🚇 Метро в радиусе 1.5 км не найдено")
        
        score += min(geo_score, geo_max)
        max_score += geo_max
        
        # Инфраструктура (30 баллов)
        infra_score = 0
        infra_max = 30
        infra_points = 30 // max(len(needs['infrastructure']), 1) if needs['infrastructure'] else 0
        
        for infra in needs['infrastructure']:
            if poi.get(infra):
                nearest = poi[infra][0]
                infra_score += infra_points
                matches.append(f"{POI_CATEGORIES.get(infra, {}).get('emoji', '📍')} {POI_CATEGORIES.get(infra, {}).get('ru', infra)}: \"{nearest['name']}\" — {nearest['distance']} м")
            else:
                mismatches.append(f"⚠️ {POI_CATEGORIES.get(infra, {}).get('ru', infra)} в радиусе 1.5 км не найдено")
        
        score += min(infra_score, infra_max)
        max_score += infra_max
        
        # Сохраняем POI для детального ответа
        flat['cached_poi'] = poi
    
    # Процент соответствия
    match_percent = int((score / max_score) * 100) if max_score > 0 else 0
    
    return {
        'score': score,
        'max_score': max_score,
        'match_percent': match_percent,
        'matches': matches,
        'mismatches': mismatches,
        'poi': flat.get('cached_poi', {})
    }

def format_flat_response(flat, analysis, index, needs):
    """Форматирует ответ о квартире"""
    match_percent = analysis['match_percent']
    
    # Выбор эмодзи в зависимости от процента соответствия
    if match_percent >= 80:
        header_emoji = "🏆"
    elif match_percent >= 60:
        header_emoji = "👍"
    elif match_percent >= 40:
        header_emoji = "📌"
    else:
        header_emoji = "ℹ️"
    
    msg = f"{header_emoji} *Вариант {index}: {flat['rooms']}к, {flat['price_usd']}$*\n"
    msg += f"📍 {flat['address']}\n"
    msg += f"🏘 Район: {flat['district']}\n"
    msg += f"📊 *Соответствие запросу: {match_percent}%*\n\n"
    
    if analysis['matches']:
        msg += "*✅ Что подходит:*\n"
        for m in analysis['matches'][:5]:
            msg += f"{m}\n"
        msg += "\n"
    
    if analysis['mismatches']:
        msg += "*⚠️ Что не совсем подходит:*\n"
        for mm in analysis['mismatches'][:3]:
            msg += f"{mm}\n"
        msg += "\n"
    
    # Инфраструктура (все найденное)
    poi = analysis.get('poi', {})
    if poi:
        msg += "*🏪 Инфраструктура рядом:*\n"
        shown = 0
        for cat_key, cat_info in POI_CATEGORIES.items():
            if poi.get(cat_key):
                for item in poi[cat_key][:1]:
                    msg += f"{cat_info['emoji']} {cat_info['ru'].capitalize()}: \"{item['name']}\" — {item['distance']} м\n"
                    shown += 1
                    if shown >= 5:
                        break
            if shown >= 5:
                break
        msg += "\n"
    
    msg += f"🔗 [Подробнее на сайте]({flat['url']})\n"
    
    return msg

def format_no_results_message(needs, top_flats):
    """Формирует сообщение, когда нет идеальных вариантов"""
    msg = "😔 *Вариантов, полностью соответствующих вашим критериям, не найдено.*\n\n"
    msg += "🔍 *Но вот лучшие альтернативы:*\n\n"
    
    for i, (flat, analysis) in enumerate(top_flats[:3], 1):
        match_percent = analysis['match_percent']
        msg += f"{i}. *{flat['rooms']}к, {flat['price_usd']}$* (совпадение {match_percent}%)\n"
        msg += f"   📍 {flat['address']}\n"
        
        # Добавляем основные плюсы
        if analysis['matches']:
            msg += f"   ✅ {analysis['matches'][0][:50]}\n"
        msg += f"   🔗 [Смотреть]({flat['url']})\n\n"
    
    msg += "💡 *Совет:* Попробуйте расширить бюджет или снять ограничения по этажу для лучших результатов."
    
    return msg

async def start(update: Update, context):
    await update.message.reply_text(
        "🏠 *ИИ-консультант «Твоя Столица»*\n\n"
        f"📊 В базе {len(FLATS)} квартир\n\n"
        "🧠 *Я понимаю сложные запросы и сокращения!*\n\n"
        "📝 *Примеры запросов:*\n"
        "• `Найди 2-комнатную квартиру до 80000$, рядом с метро и тц, желательно на 3 этаже`\n"
        "• `1 комнату до 70000$, рядом школа и парк`\n"
        "• `Квартиру рядом с универом и фитнесом до 90000$`\n"
        "• `Жилье у МКАД с детским садом`\n\n"
        "Если идеальных вариантов нет, я предложу лучшие альтернативы с пояснениями!",
        parse_mode="Markdown"
    )

async def search_flats(update: Update, context):
    user_id = update.effective_user.id
    text = update.message.text
    await update.message.chat.send_action(action="typing")
    
    # Извлекаем потребности
    needs = extract_user_needs(text)
    logger.info(f"Запрос: {needs}")
    
    # Оцениваем все квартиры
    scored_flats = []
    for flat in FLATS:
        analysis = score_flat(flat.copy(), needs)
        scored_flats.append((flat, analysis))
    
    # Сортируем по проценту соответствия
    scored_flats.sort(key=lambda x: x[1]['match_percent'], reverse=True)
    
    # Отбираем топ-5
    top_flats = scored_flats[:5]
    best_match_percent = top_flats[0][1]['match_percent'] if top_flats else 0
    
    # Сохраняем в контекст
    context.user_data['last_results'] = [(f, a) for f, a in top_flats]
    context.user_data['last_needs'] = needs
    
    # Формируем ответ
    if best_match_percent >= 70:
        # Есть хорошие варианты
        msg = f"🔍 *Найдено {len(top_flats)} отличных вариантов:*\n\n"
        for i, (flat, analysis) in enumerate(top_flats[:3], 1):
            msg += format_flat_response(flat, analysis, i, needs)
            msg += "─" * 35 + "\n\n"
    else:
        # Нет идеальных вариантов - показываем лучшие альтернативы
        msg = format_no_results_message(needs, top_flats[:3])
    
    # Добавляем кнопки для дополнительных вопросов
    keyboard = [
        [InlineKeyboardButton("📋 Следующие варианты", callback_data="next_flats")],
        [InlineKeyboardButton("❓ Уточнить про инфраструктуру", callback_data="ask_infra")]
    ]
    
    await update.message.reply_text(
        msg, 
        parse_mode="Markdown", 
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def next_flats(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    results = context.user_data.get('last_results', [])
    current_idx = context.user_data.get('current_idx', 3)
    needs = context.user_data.get('last_needs', {})
    
    if not results:
        await query.edit_message_text("Нет сохраненных результатов. Напишите новый запрос.")
        return
    
    start_idx = current_idx
    end_idx = min(start_idx + 3, len(results))
    
    if start_idx >= len(results):
        await query.edit_message_text("Это все варианты. Напишите новый запрос для поиска.")
        return
    
    msg = f"🔍 *Варианты {start_idx+1}-{end_idx} из {len(results)}:*\n\n"
    for i, (flat, analysis) in enumerate(results[start_idx:end_idx], start_idx + 1):
        msg += format_flat_response(flat, analysis, i, needs)
        msg += "─" * 35 + "\n\n"
    
    context.user_data['current_idx'] = end_idx
    
    await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def ask_infra(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💬 *Задайте вопрос об инфраструктуре:*\n\n"
        "Например:\n"
        "• *Какие магазины рядом с первым вариантом?*\n"
        "• *Есть ли школы и детские сады?*\n"
        "• *Что есть из спортивных объектов?*\n"
        "• *Как далеко до метро?*",
        parse_mode="Markdown"
    )
    return

async def answer_infra_question(update: Update, context):
    text = update.message.text.lower()
    results = context.user_data.get('last_results', [])
    
    if not results:
        await update.message.reply_text("Сначала выполните поиск квартир.")
        return
    
    # Определяем о какой квартире спрашивают
    flat_index = 0
    if 'перв' in text or 'первая' in text or '1' in text:
        flat_index = 0
    elif 'втор' in text or 'вторая' in text or '2' in text:
        flat_index = 1 if len(results) > 1 else 0
    elif 'треть' in text or 'третья' in text or '3' in text:
        flat_index = 2 if len(results) > 2 else 0
    
    flat, analysis = results[flat_index]
    poi = analysis.get('poi', {})
    lat, lon = flat.get('lat'), flat.get('lon')
    
    # Если POI нет в кэше, получаем заново
    if not poi and lat and lon:
        poi = find_nearby_pois(lat, lon)
    
    # Определяем тип вопроса
    response = f"📊 *Инфраструктура вокруг {flat['rooms']}-комнатной квартиры на {flat['address'][:40]}...*\n\n"
    
    added = False
    
    if 'магазин' in text or 'тц' in text or 'торгов' in text:
        shops = poi.get('shops', [])
        malls = poi.get('malls', [])
        if shops or malls:
            response += "*🏪 Магазины и ТЦ:*\n"
            for s in shops[:3]:
                response += f"• {s['name']} — {s['distance']} м\n"
            for m in malls[:2]:
                response += f"• 🏬 ТЦ \"{m['name']}\" — {m['distance']} м\n"
            added = True
        else:
            response += "🏪 Магазины в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'школ' in text:
        schools = poi.get('schools', [])
        if schools:
            response += "*📚 Школы:*\n"
            for s in schools[:3]:
                response += f"• {s['name']} — {s['distance']} м\n"
            added = True
        else:
            response += "📚 Школы в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'сад' in text or 'детск' in text:
        kindergartens = poi.get('kindergartens', [])
        if kindergartens:
            response += "*🏫 Детские сады:*\n"
            for k in kindergartens[:3]:
                response += f"• {k['name']} — {k['distance']} м\n"
            added = True
        else:
            response += "🏫 Детские сады в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'универ' in text or 'вуз' in text or 'институт' in text:
        universities = poi.get('universities', [])
        if universities:
            response += "*🎓 Университеты:*\n"
            for u in universities[:3]:
                response += f"• {u['name']} — {u['distance']} м\n"
            added = True
        else:
            response += "🎓 Университеты в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'фитнес' in text or 'спорт' in text:
        fitness = poi.get('fitness', [])
        if fitness:
            response += "*💪 Фитнес-центры:*\n"
            for f in fitness[:3]:
                response += f"• {f['name']} — {f['distance']} м\n"
            added = True
        else:
            response += "💪 Фитнес-центры в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'кино' in text:
        cinemas = poi.get('cinemas', [])
        if cinemas:
            response += "*🎬 Кинотеатры:*\n"
            for c in cinemas[:3]:
                response += f"• {c['name']} — {c['distance']} м\n"
            added = True
        else:
            response += "🎬 Кинотеатры в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'банк' in text:
        banks = poi.get('banks', [])
        if banks:
            response += "*🏦 Банки:*\n"
            for b in banks[:3]:
                response += f"• {b['name']} — {b['distance']} м\n"
            added = True
        else:
            response += "🏦 Банки в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'аптек' in text:
        pharmacies = poi.get('pharmacies', [])
        if pharmacies:
            response += "*💊 Аптеки:*\n"
            for p in pharmacies[:3]:
                response += f"• {p['name']} — {p['distance']} м\n"
            added = True
        else:
            response += "💊 Аптеки в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'кафе' in text or 'ресторан' in text:
        cafes = poi.get('cafes', [])
        if cafes:
            response += "*☕ Кафе и рестораны:*\n"
            for c in cafes[:3]:
                response += f"• {c['name']} — {c['distance']} м\n"
            added = True
        else:
            response += "☕ Кафе в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'парк' in text:
        parks = poi.get('parks', [])
        if parks:
            response += "*🌳 Парки:*\n"
            for p in parks[:3]:
                response += f"• {p['name']} — {p['distance']} м\n"
            added = True
        else:
            response += "🌳 Парки в радиусе 1.5 км не найдены.\n"
            added = True
    
    elif 'метро' in text:
        metro = poi.get('metro', [])
        if metro:
            response += "*🚇 Метро:*\n"
            for m in metro[:3]:
                response += f"• {m['name']} — {m['distance']} м\n"
            added = True
        else:
            response += "🚇 Метро в радиусе 1.5 км не найдено.\n"
            added = True
    
    elif 'больниц' in text or 'поликлиник' in text:
        hospitals = poi.get('hospitals', [])
        if hospitals:
            response += "*🏥 Больницы и поликлиники:*\n"
            for h in hospitals[:3]:
                response += f"• {h['name']} — {h['distance']} м\n"
            added = True
        else:
            response += "🏥 Больницы в радиусе 1.5 км не найдены.\n"
            added = True
    
    else:
        # Общий ответ со всей инфраструктурой
        response = f"📊 *Вся инфраструктура вокруг квартиры:*\n\n"
        categories_shown = 0
        for cat_key, cat_info in POI_CATEGORIES.items():
            items = poi.get(cat_key, [])
            if items:
                response += f"{cat_info['emoji']} *{cat_info['ru'].capitalize()}:*\n"
                for item in items[:2]:
                    response += f"   • {item['name']} — {item['distance']} м\n"
                response += "\n"
                categories_shown += 1
                if categories_shown >= 5:
                    break
        
        if categories_shown == 0:
            response += "Инфраструктура в радиусе 1.5 км не найдена.\n"
        
        added = True
    
    if not added:
        response += "Инфраструктура в радиусе 1.5 км не найдена.\n"
    
    # Добавляем расстояния до центра и МКАД
    if lat and lon:
        dist_center = calculate_distance(lat, lon, CENTER_COORD[0], CENTER_COORD[1])
        dist_mkad = calculate_distance(lat, lon, MKAD_COORD[0], MKAD_COORD[1])
        response += f"\n📍 *Локация:*\n"
        response += f"• {dist_center:.1f} км от центра Минска\n"
        response += f"• {dist_mkad:.1f} км от МКАД"
    
    await update.message.reply_text(response, parse_mode="Markdown")

# Веб-сервер для Render
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "🤖 Бот для поиска квартир «Твоя Столица» работает!"

def run_web():
    port = int(os.environ.get('PORT', 10000))
    web_app.run(host='0.0.0.0', port=port)

Thread(target=run_web, daemon=True).start()

def main():
    logger.info("🚀 Запуск бота...")
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(next_flats, pattern="next_flats"))
    app.add_handler(CallbackQueryHandler(ask_infra, pattern="ask_infra"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_flats))
    app.add_handler(MessageHandler(filters.Regex(r'^(магазин|тц|школ|сад|универ|фитнес|кино|банк|аптек|кафе|парк|метро|больниц)'), answer_infra_question))
    
    logger.info(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    app.run_polling()

if __name__ == "__main__":
    main()