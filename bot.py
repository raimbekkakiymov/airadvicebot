import telebot
from telebot import types
import requests
import os

# ============ ТОКЕН ============
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Берем из переменных окружения Render

if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден в переменных окружения")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# ============ КОМАНДА /start ============
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    geo_button = types.KeyboardButton("📍 Отправить местоположение", request_location=True)
    markup.add(geo_button)

    bot.reply_to(
        message,
        "🌍 **Добро пожаловать в AirHealth!**\n\n"
        "Я анализирую качество воздуха и даю персональные рекомендации "
        "по физической активности и питанию.\n\n"
        "Для начала работы отправьте ваше местоположение.",
        reply_markup=markup,
        parse_mode='Markdown'
    )

# ============ ОБРАБОТКА ГЕОЛОКАЦИИ ============
@bot.message_handler(content_types=['location'])
def handle_location(message):
    lat = message.location.latitude
    lon = message.location.longitude

    bot.send_chat_action(message.chat.id, 'typing')

    # Здесь будет логика сбора данных
    # Пока просто отвечаем, что координаты получены
    response = (
        f"📍 **Координаты получены:**\n"
        f"Широта: {lat:.4f}\n"
        f"Долгота: {lon:.4f}\n\n"
        f"🔍 Анализирую качество воздуха...\n"
        f"_(функция анализа будет добавлена в следующем обновлении)_"
    )

    bot.send_message(message.chat.id, response, parse_mode='Markdown')

# ============ ЗАПУСК ============
if __name__ == "__main__":
    print("✅ Бот запущен...")
    bot.polling(none_stop=True)
