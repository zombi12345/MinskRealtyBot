import json
import re
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Конфигурация
BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"
API_KEY = "sk-Bylz2io6oa46zyduebiq3It5xncjfPgqGhiujd4JaCg7GSvg"

# Загружаем данные
with open('flats_final.json', 'r', encoding='utf-8') as f:
    FLATS = json.load(f)

# База знаний о районах
DISTRICT_INFO = {
    'Заводской': {'pros': 'Развитая промышленность, хорошая транспортная развязка, метро "Партизанская"', 'metro': 'Партизанская, Автозаводская'},
    'Московский': {'pros': 'Развитая инфраструктура, ТЦ "Замок", парк им. Горького', 'metro': 'Грушевка, Малиновка, Петровщина'},
    'Октябрьский': {'pros': 'Современный район, ЖК "Минск-Мир", близость к парку Курасовщина', 'metro': 'Ковальская Слобода'},
    'Первомайский': {'pros': 'Зеленый район, парк Челюскинцев, Ботанический сад', 'metro': 'Уручье, Восток'},
    'Советский': {'pros': 'Престижный центр, парк Победы', 'metro': 'Академия наук, Парк Челюскинцев'},
    'Центральный': {'pros': 'Сердце Минска, вся инфраструктура', 'metro': 'Немига, Купаловская, Октябрьская'},
    'Фрунзенский': {'pros': 'Современная застройка, много новостроек', 'metro': 'Каменная горка, Спортивная'},
    'Ленинский': {'pros': 'Спортивный район, Чижовка-Арена', 'metro': 'Чижовка, Петровщина'},
    'Партизанский': {'pros': 'Спокойный район, близость к Ботаническому саду', 'metro': 'Партизанская, Тракторный завод'}
}

def ask_ai(prompt):
    """Запрос к ИИ для анализа квартиры"""
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.7
            },
            timeout=10
        )
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        return None
    except Exception as e:
        print(f"AI Error: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏠 *ИИ-помощник «Твоя Столица»*\n\n"
        f"📊 В базе {len(FLATS)} квартир\n\n"
        "Я помогу найти идеальную квартиру и расскажу о районе!\n\n"
        "📝 *Примеры запросов:*\n"
        "• `1 комнату до 70000`\n"
        "• `однушка в Московском районе`\n"
        "• `2 комнаты до 90000`\n"
        "• `все квартиры`",
        parse_mode="Markdown"
    )

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    await update.message.chat.send_action(action="typing")
    
    # Парсинг запроса
    rooms = None
    if '2' in text or 'двух' in text or 'двушк' in text:
        rooms = 2
    elif '1' in text or 'одно' in text or 'однушк' in text:
        rooms = 1
    elif '3' in text or 'трёх' in text:
        rooms = 3
    
    max_price = None
    prices = re.findall(r'(\d{4,6})', text)
    for p in prices:
        price = int(p)
        if 30000 < price < 300000:
            max_price = price
            break
    
    district_filter = None
    for district in DISTRICT_INFO.keys():
        if district.lower() in text:
            district_filter = district
            break
    
    # Фильтрация
    results = []
    for flat in FLATS:
        if rooms and flat.get('rooms') != rooms:
            continue
        if max_price and flat.get('price_usd', 0) > max_price:
            continue
        if district_filter and district_filter != flat.get('district'):
            continue
        results.append(flat)
    
    results = sorted(results, key=lambda x: x.get('price_usd', 0))
    
    if not results:
        await update.message.reply_text(
            f"😔 *Ничего не найдено*\n\n"
            f"В базе {len(FLATS)} квартир.\n"
            f"Попробуйте: `1 комнату до 70000`\n"
            f"или `все квартиры`",
            parse_mode="Markdown"
        )
        return
    
    # Формируем ответ
    message = f"🔍 *Найдено {len(results)} вариантов:*\n\n"
    
    for i, flat in enumerate(results[:3], 1):
        district = flat.get('district', '')
        district_data = DISTRICT_INFO.get(district, {})
        
        message += f"{i}. *{flat['rooms']}к*, {flat['price_usd']}$\n"
        message += f"   📍 {flat.get('address', 'Минск')}\n"
        if district:
            message += f"   🏘 Район: {district}\n"
            message += f"   🚇 Метро: {district_data.get('metro', 'информация уточняется')}\n"
        message += f"   🔗 [Смотреть на сайте]({flat['url']})\n\n"
    
    if len(results) > 3:
        message += f"_Показаны топ-3 из {len(results)}. Уточните запрос для лучших результатов._"
    
    await update.message.reply_text(message, parse_mode="Markdown", disable_web_page_preview=True)

async def all_flats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sorted_flats = sorted(FLATS, key=lambda x: x.get('price_usd', 0))
    message = f"🏠 *Все квартиры ({len(sorted_flats)}):*\n\n"
    
    for i, flat in enumerate(sorted_flats[:15], 1):
        message += f"{i}. *{flat['rooms']}к*, {flat['price_usd']}$\n"
        message += f"   📍 {flat.get('address', 'Минск')[:40]}\n"
        message += f"   🔗 [Смотреть]({flat['url']})\n\n"
    
    await update.message.reply_text(message, parse_mode="Markdown", disable_web_page_preview=True)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Как пользоваться:*\n\n"
        "Просто напишите, что ищете:\n"
        "• `1 комнату до 70000`\n"
        "• `однушка в Московском районе`\n"
        "• `2 комнаты до 90000`\n"
        "• `все квартиры`\n\n"
        "Команды:\n"
        "/start - начать\n"
        "/help - помощь\n"
        "/all - все квартиры",
        parse_mode="Markdown"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("all", all_flats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))
    
    print(f"✅ Бот запущен! В базе {len(FLATS)} квартир")
    print(f"🤖 ИИ-анализ подключен (OpenAI)")
    app.run_polling()

if __name__ == "__main__":
    main()