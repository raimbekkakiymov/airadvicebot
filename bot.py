import telebot
from telebot import types
import requests
import os
from datetime import datetime

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")  # Добавим позже

bot = telebot.TeleBot(BOT_TOKEN)

# ============ БАЗА ЗНАНИЙ ============
def get_pollution_type(air_data, wind_deg, lat, lon):
    """Определяем тип загрязнения на основе данных"""
    
    if not air_data:
        return {
            "name": "Нет данных",
            "emoji": "⚪",
            "sport": "Не могу получить данные о воздухе. Проверьте подключение.",
            "food": "Рекомендации по питанию недоступны.",
            "danger": "unknown"
        }
    
    pm25 = air_data.get('pm25', 0)
    pm10 = air_data.get('pm10', 0)
    no2 = air_data.get('no2', 0)
    so2 = air_data.get('so2', 0)
    co = air_data.get('co', 0)
    
    # Определяем уровень опасности по PM2.5
    if pm25 <= 15:
        level = "low"
        emoji = "🟢"
        name = "Чистый воздух"
    elif pm25 <= 35:
        level = "medium"
        emoji = "🟡"
        name = "Умеренное загрязнение"
    elif pm25 <= 75:
        level = "high"
        emoji = "🟠"
        name = "Повышенное загрязнение"
    else:
        level = "critical"
        emoji = "🔴"
        name = "Опасный уровень"
    
    # Определяем тип источника
    source = ""
    if no2 > 80 and co > 1.5:
        source = " (вероятный источник: транспорт)"
    elif so2 > 50:
        source = " (вероятный источник: промышленность/ТЭЦ)"
    elif pm25 > 100 and so2 > 30:
        source = " (возможно горение отходов)"
    
    # Рекомендации по спорту
    if level == "low":
        sport = "✅ Воздух чистый! Отличное время для пробежки или прогулки."
    elif level == "medium":
        sport = "🏃‍♂️ Можно гулять, но интенсивные тренировки лучше перенести в зал."
    elif level == "high":
        sport = "⚠️ Лучше тренироваться только в помещении. На улице используйте маску."
    else:
        sport = "⛔ Не выходите на улицу без необходимости. Спорт только дома."
    
    # Рекомендации по питанию
    if level == "low":
        food = "🥗 Обычный сбалансированный рацион. Сезонные овощи и фрукты."
    elif level == "medium":
        food = "🥦 Добавьте антиоксиданты: зелёный чай, яблоки, брокколи."
    elif level == "high":
        food = "💊 Пейте больше воды. Добавьте витамин C и Омега-3. Исключите жареное."
    else:
        food = "🍵 Сорбенты (активированный уголь), кинза, морская капуста. Обильное питьё."
    
    return {
        "name": name + source,
        "emoji": emoji,
        "sport": sport,
        "food": food,
        "danger": level,
        "pm25": pm25,
        "pm10": pm10,
        "no2": no2,
        "so2": so2,
        "co": co
    }


def get_wind_direction(deg):
    """Переводим градусы в направление ветра"""
    directions = ['Северный', 'Северо-восточный', 'Восточный', 'Юго-восточный',
                  'Южный', 'Юго-западный', 'Западный', 'Северо-западный']
    index = round(deg / 45) % 8
    return directions[index]


def get_air_quality(lat, lon):
    """Получаем данные о качестве воздуха"""
    # Если нет API ключа, возвращаем None
    if not WEATHER_API_KEY:
        return None
    
    try:
        url = f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        components = data['list'][0]['components']
        return {
            'pm25': components.get('pm2_5', 0),
            'pm10': components.get('pm10', 0),
            'no2': components.get('no2', 0),
            'so2': components.get('so2', 0),
            'co': components.get('co', 0),
            'o3': components.get('o3', 0)
        }
    except Exception as e:
        print(f"Ошибка получения данных о воздухе: {e}")
        return None


def get_weather(lat, lon):
    """Получаем данные о погоде"""
    if not WEATHER_API_KEY:
        return None
    
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        return {
            'temp': data['main']['temp'],
            'humidity': data['main']['humidity'],
            'wind_speed': data['wind']['speed'],
            'wind_deg': data['wind'].get('deg', 0),
            'description': data['weather'][0]['description']
        }
    except Exception as e:
        print(f"Ошибка получения погоды: {e}")
        return None


# ============ КОМАНДА /start ============
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    geo_button = types.KeyboardButton("📍 Отправить местоположение", request_location=True)
    markup.add(geo_button)
    
    bot.reply_to(
        message,
        "🌍 **Добро пожаловать в AirAdvice!**\n\n"
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
    
    # Отправляем "печатает..."
    bot.send_chat_action(message.chat.id, 'typing')
    
    # Собираем данные
    air_data = get_air_quality(lat, lon)
    weather = get_weather(lat, lon)
    
    # Анализируем
    wind_deg = weather['wind_deg'] if weather else 0
    result = get_pollution_type(air_data, wind_deg, lat, lon)
    
    # Формируем ответ
    response_text = format_response(result, weather)
    
    # Отправляем
    bot.send_message(message.chat.id, response_text, parse_mode='Markdown')


def format_response(result, weather):
    """Формируем красивый текст ответа"""
    
    text = f"{result['emoji']} **Качество воздуха: {result['name']}**\n\n"
    
    # Показатели
    if 'pm25' in result:
        text += "📊 **Показатели:**\n"
        text += f"• PM2.5: {result['pm25']:.1f} µg/m³\n"
        text += f"• PM10: {result['pm10']:.1f} µg/m³\n"
        text += f"• NO₂: {result['no2']:.1f} µg/m³\n"
        text += f"• SO₂: {result['so2']:.1f} µg/m³\n\n"
    
    # Погода
    if weather:
        wind_dir = get_wind_direction(weather['wind_deg'])
        text += f"💨 **Погода:** {weather['description']}\n"
        text += f"Ветер: {wind_dir}, {weather['wind_speed']} м/с\n"
        text += f"Температура: {weather['temp']}°C\n\n"
    
    # Рекомендации
    text += f"🏃‍♂️ **Активность:**\n{result['sport']}\n\n"
    text += f"🥗 **Питание:**\n{result['food']}\n\n"
    
    text += "---\n"
    text += f"_{datetime.now().strftime('%H:%M')} • Обновлено автоматически_"
    
    return text


# ============ ЗАПУСК ============
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ Ошибка: BOT_TOKEN не найден")
        exit(1)
    
    print("✅ Бот запущен...")
    bot.polling(none_stop=True)
