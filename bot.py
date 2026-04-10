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

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота
BOT_TOKEN = "7227182736:AAHs6widEwBl6AJUebqaA_-z7x6XACi39BE"

# Путь к файлу с данными
current_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(current_dir, 'flats_data.json')

# Загрузка данных о квартирах
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

# Координаты районов Минска (для будущего гео-анализа)
DISTRICT_COORDS = {
    'Заводской': (53.85, 27.60),
    'Московский': (53.88, 27.53),
    'Октябрьский': (53.85, 27.55),
    'Первомайский': (53.92, 27.62),
    'Советский': (53.92, 27.58),
    'Центральный': (53.90, 27.56),
    'Фрунзенский': (53.89, 27.48),
    'Ленинский': (53.86, 27.57),
    'Партизанский': (53.85, 27.65)
}

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER (чтобы порт был открыт) ==========
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

# Запускаем веб-сервер в отдельном потоке
Thread(target=run_web, daemon=True).start()
logger.info("🌐 Веб-сервер запущен на порту " + os.environ.get('PORT', '10000'))
# ===================================================================

async def start(update: Update, context):
    """Обработчик команды /start"""
    await update.message.reply_text(
        f"🏠 *ИИ-помощник «Твоя Столица»*\n\n"
        f"📊 В базе {len(FLATS)} квартир\n\n"
        f"📝 *Примеры запросов:*\n"
        f"• `1 комнату до 70000`\n"
        f"• `2 комнаты до 90000`\n"
        f"• `все квартиры`\n\n"
        f"🔗 Ссылка на сайт: [t-s.by](https://www.t-s.by)",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

async def search(update: Update, context):
    """Поиск квартир по запросу пользователя"""
    text = update.message.text.lower()
    await update.message.chat.send_action(action="typing")
    
    # Определяем количество комнат
    rooms = None
    if '1' in text or 'одно' in text or 'однушк' in text:
        rooms = 1
    elif '2' in text or 'двух' in text or 'двушк' in text:
        rooms = 2
    
    # Определяем максимальную цену
    max_price = None
    for p in re.findall(r'(\d{4,6})', text):
        price = int(p)
        if 30000 < price < 300000:
            max_price = price
            break
    
    # Фильтрация
    results = []
    for flat in FLATS:
        if rooms is not None and flat['rooms'] != rooms:
            continue
        if max_price is not None and flat['price_usd'] > max_price:
            continue
        results.append(flat)
    
    # Сортировка по цене
    results = sorted(results, key=lambda x: x['price_usd'])[:5]
    
    if not results:
        await update.message.reply_text(
            "😔 *Ничего не найдено*\n\n"
            "Попробуйте другие критерии:\n"
            "• `1 комнату до 70000`\n"
            "• `2 комнаты до 90000`\n"
            "• `все квартиры`",
            parse_mode="Markdown"
        )
        return
    
    # Формируем ответ
    msg = f"🔍 *Найдено {len(results)} вариантов:*\n\n"
    for i, flat in enumerate(results, 1):
        msg += f"{i}. *{flat['rooms']}к*, {flat['price_usd']}$\n"
        msg += f"   🏠 {flat['address']}\n"
        msg += f"   🏘 Район: {flat['district']}\n"
        msg += f"   🔗 [Смотреть на сайте]({flat['url']})\n\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def all_flats(update: Update, context):
    """Показать все квартиры"""
    flats_sorted = sorted(FLATS, key=lambda x: x['price_usd'])
    msg = f"🏠 *Все квартиры ({len(flats_sorted)}):*\n\n"
    
    for i, flat in enumerate(flats_sorted[:20], 1):
        msg += f"{i}. *{flat['rooms']}к*, {flat['price_usd']}$\n"
        msg += f"   📍 {flat['address'][:45]}\n"
        msg += f"   🔗 [Смотреть]({flat['url']})\n\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def help_command(update: Update, context):
    """Справка"""
    await update.message.reply_text(
        "📖 *Как пользоваться ботом:*\n\n"
        "Просто напишите, что ищете:\n"
        "• `1 комнату до 70000`\n"
        "• `однушку до 65000`\n"
        "• `2 комнаты до 90000`\n"
        "• `двушку в Московском районе`\n"
        "• `все квартиры`\n\n"
        "Команды:\n"
        "/start - начать работу\n"
        "/help - эта справка\n"
        "/all - показать все квартиры",
        parse_mode="Markdown"
    )

async def error_handler(update: Update, context):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    if update and update.message:
        await update.message.reply_text(
            "⚠️ Произошла ошибка. Пожалуйста, попробуйте позже."
        )

def main():
    """Запуск бота"""
    logger.info("🚀 Запуск бота...")
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("all", all_flats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))
    app.add_error_handler(error_handler)
    
    logger.info(f"✅ Бот успешно запущен! В базе {len(FLATS)} квартир")
    
    # Запускаем polling
    app.run_polling()

if __name__ == "__main__":
    main()