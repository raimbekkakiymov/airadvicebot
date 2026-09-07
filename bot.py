import telebot
from telebot import types
import requests
import os
from datetime import datetime

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)

# ============ БАЗА ЗНАНИЙ (3 языка) ============
TRANSLATIONS = {
    "ru": {
        "welcome": "🌍 **Добро пожаловать в AirAdvice!**\n\nВыберите язык:",
        "choose_lang": "Выберите язык / Тілді таңдаңыз / Choose language:",
        "send_location": "📍 Отправить местоположение",
        "air_quality": "Качество воздуха",
        "no_data": "Не могу получить данные о воздухе. Проверьте подключение.",
        "sport_title": "🏃‍♂️ **Активность:**",
        "food_title": "🥗 **Питание:**",
        "weather_title": "💨 **Погода:**",
        "wind": "Ветер",
        "temp": "Температура",
        "updated": "Обновлено автоматически",
        "clean": "Чистый воздух",
        "moderate": "Умеренное загрязнение",
        "high": "Повышенное загрязнение",
        "dangerous": "Опасный уровень",
        "source_traffic": " (вероятный источник: транспорт)",
        "source_industry": " (вероятный источник: промышленность/ТЭЦ)",
        "source_burning": " (возможно горение отходов)",
        "sport_clean": "✅ Воздух чистый! Отличное время для пробежки или прогулки.",
        "sport_moderate": "🏃‍♂️ Можно гулять, но интенсивные тренировки лучше перенести в зал.",
        "sport_high": "⚠️ Лучше тренироваться только в помещении. На улице используйте маску.",
        "sport_dangerous": "⛔ Не выходите на улицу без необходимости. Спорт только дома.",
        "food_clean": "🥗 Обычный сбалансированный рацион. Сезонные овощи и фрукты.",
        "food_moderate": "🥦 Добавьте антиоксиданты: зелёный чай, яблоки, брокколи.",
        "food_high": "💊 Пейте больше воды. Добавьте витамин C и Омега-3. Исключите жареное.",
        "food_dangerous": "🍵 Сорбенты (активированный уголь), кинза, морская капуста. Обильное питьё."
    },
    "kz": {
        "welcome": "🌍 **AirAdvice-қа қош келдіңіз!**\n\nТілді таңдаңыз:",
        "choose_lang": "Выберите язык / Тілді таңдаңыз / Choose language:",
        "send_location": "📍 Орналасқан жерді жіберу",
        "air_quality": "Ауа сапасы",
        "no_data": "Ауа туралы деректерді ала алмадым. Байланысты тексеріңіз.",
        "sport_title": "🏃‍♂️ **Белсенділік:**",
        "food_title": "🥗 **Тамақтану:**",
        "weather_title": "💨 **Ауа райы:**",
        "wind": "Жел",
        "temp": "Температура",
        "updated": "Автоматты түрде жаңартылды",
        "clean": "Таза ауа",
        "moderate": "Орташа ластану",
        "high": "Жоғары ластану",
        "dangerous": "Қауіпті деңгей",
        "source_traffic": " (ықтимал көз: көлік)",
        "source_industry": " (ықтимал көз: өнеркәсіп/ЖЭО)",
        "source_burning": " (қалдықтардың жануы мүмкін)",
        "sport_clean": "✅ Ауа таза! Жүгіру немесе серуендеу үшін тамаша уақыт.",
        "sport_moderate": "🏃‍♂️ Серуендеуге болады, бірақ қарқынды жаттығуларды залға ауыстырған жөн.",
        "sport_high": "⚠️ Тек үй ішінде жаттығу ұсынылады. Сыртта маска қолданыңыз.",
        "sport_dangerous": "⛔ Қажетсіз сыртқа шықпаңыз. Спортпен тек үйде айналысыңыз.",
        "food_clean": "🥗 Кәдімгі теңдестірілген тамақтану. Маусымдық көкөністер мен жемістер.",
        "food_moderate": "🥦 Антиоксиданттар қосыңыз: көк шай, алма, брокколи.",
        "food_high": "💊 Көбірек су ішіңіз. С дәрумені мен Омега-3 қосыңыз. Қуырылған тағамнан бас тартыңыз.",
        "food_dangerous": "🍵 Сорбенттер (белсендірілген көмір), кинза, теңіз балдыры. Көп сұйықтық ішіңіз."
    },
    "en": {
        "welcome": "🌍 **Welcome to AirAdvice!**\n\nChoose language:",
        "choose_lang": "Выберите язык / Тілді таңдаңыз / Choose language:",
        "send_location": "📍 Send location",
        "air_quality": "Air Quality",
        "no_data": "Cannot get air quality data. Check connection.",
        "sport_title": "🏃‍♂️ **Activity:**",
        "food_title": "🥗 **Nutrition:**",
        "weather_title": "💨 **Weather:**",
        "wind": "Wind",
        "temp": "Temperature",
        "updated": "Updated automatically",
        "clean": "Clean air",
        "moderate": "Moderate pollution",
        "high": "High pollution",
        "dangerous": "Dangerous level",
        "source_traffic": " (likely source: traffic)",
        "source_industry": " (likely source: industry/power plant)",
        "source_burning": " (possible waste burning)",
        "sport_clean": "✅ Air is clean! Great time for running or walking.",
        "sport_moderate": "🏃‍♂️ You can walk, but intense workouts better move to gym.",
        "sport_high": "⚠️ Better to exercise only indoors. Use mask outside.",
        "sport_dangerous": "⛔ Don't go outside without necessity. Sports only at home.",
        "food_clean": "🥗 Normal balanced diet. Seasonal vegetables and fruits.",
        "food_moderate": "🥦 Add antioxidants: green tea, apples, broccoli.",
        "food_high": "💊 Drink more water. Add Vitamin C and Omega-3. Avoid fried food.",
        "food_dangerous": "🍵 Sorbents (activated charcoal), cilantro, seaweed. Drink plenty."
    }
}

# ============ ХРАНЕНИЕ ЯЗЫКОВ ПОЛЬЗОВАТЕЛЕЙ ============
user_languages = {}  # {user_id: "ru"}

# ============ ФУНКЦИИ ============
def get_text(user_id, key):
    """Получаем текст на языке пользователя"""
    lang = user_languages.get(user_id, "ru")
    return TRANSLATIONS[lang].get(key, TRANSLATIONS["ru"][key])


def get_pollution_type(air_data, user_id):
    """Определяем тип загрязнения"""
    if not air_data:
        return {
            "name": get_text(user_id, "no_data"),
            "emoji": "⚪",
            "sport": get_text(user_id, "no_data"),
            "food": get_text(user_id, "no_data"),
            "danger": "unknown"
        }
    
    pm25 = air_data.get('pm25', 0)
    no2 = air_data.get('no2', 0)
    so2 = air_data.get('so2', 0)
    
    if pm25 <= 15:
        level = "clean"
        emoji = "🟢"
        name = get_text(user_id, "clean")
    elif pm25 <= 35:
        level = "moderate"
        emoji = "🟡"
        name = get_text(user_id, "moderate")
    elif pm25 <= 75:
        level = "high"
        emoji = "🟠"
        name = get_text(user_id, "high")
    else:
        level = "dangerous"
        emoji = "🔴"
        name = get_text(user_id, "dangerous")
    
    # Источник загрязнения
    if no2 > 80:
        name += get_text(user_id, "source_traffic")
    elif so2 > 50:
        name += get_text(user_id, "source_industry")
    
    return {
        "name": name,
        "emoji": emoji,
        "sport": get_text(user_id, f"sport_{level}"),
        "food": get_text(user_id, f"food_{level}"),
        "danger": level,
        "pm25": pm25,
        "pm10": air_data.get('pm10', 0),
        "no2": no2,
        "so2": so2,
        "co": air_data.get('co', 0)
    }


def get_air_quality(lat, lon):
    """Получаем данные о качестве воздуха"""
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
            'co': components.get('co', 0)
        }
    except:
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
            'wind_speed': data['wind']['speed'],
            'wind_deg': data['wind'].get('deg', 0),
            'description': data['weather'][0]['description']
        }
    except:
        return None


def get_wind_direction(deg, lang="ru"):
    """Переводим градусы в направление ветра"""
    directions_ru = ['Северный', 'Северо-восточный', 'Восточный', 'Юго-восточный',
                     'Южный', 'Юго-западный', 'Западный', 'Северо-западный']
    directions_kz = ['Солтүстік', 'Солтүстік-шығыс', 'Шығыс', 'Оңтүстік-шығыс',
                     'Оңтүстік', 'Оңтүстік-батыс', 'Батыс', 'Солтүстік-батыс']
    directions_en = ['North', 'Northeast', 'East', 'Southeast',
                     'South', 'Southwest', 'West', 'Northwest']
    
    index = round(deg / 45) % 8
    if lang == "kz":
        return directions_kz[index]
    elif lang == "en":
        return directions_en[index]
    return directions_ru[index]


# ============ КОМАНДА /start ============
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    user_languages[user_id] = "ru"  # По умолчанию русский
    
    # Кнопки выбора языка
    markup = types.InlineKeyboardMarkup(row_width=3)
    ru_btn = types.InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")
    kz_btn = types.InlineKeyboardButton("🇰🇿 Қазақша", callback_data="lang_kz")
    en_btn = types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    markup.add(ru_btn, kz_btn, en_btn)
    
    bot.send_message(
        message.chat.id,
        "🌍 **AirAdvice**\n\nВыберите язык / Тілді таңдаңыз / Choose language:",
        reply_markup=markup,
        parse_mode='Markdown'
    )


# ============ ОБРАБОТКА ВЫБОРА ЯЗЫКА ============
@bot.callback_query_handler(func=lambda call: call.data.startswith('lang_'))
def handle_language(call):
    user_id = call.from_user.id
    lang = call.data.split('_')[1]
    user_languages[user_id] = lang
    
    # Убираем кнопки
    bot.answer_callback_query(call.id)
    
    # Показываем кнопку геолокации
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    geo_button = types.KeyboardButton(get_text(user_id, "send_location"), request_location=True)
    markup.add(geo_button)
    
    bot.send_message(
        call.message.chat.id,
        get_text(user_id, "welcome"),
        reply_markup=markup,
        parse_mode='Markdown'
    )


# ============ ОБРАБОТКА ГЕОЛОКАЦИИ ============
@bot.message_handler(content_types=['location'])
def handle_location(message):
    user_id = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    air_data = get_air_quality(lat, lon)
    weather = get_weather(lat, lon)
    result = get_pollution_type(air_data, user_id)
    
    # Формируем ответ
    lang = user_languages.get(user_id, "ru")
    text = f"{result['emoji']} **{get_text(user_id, 'air_quality')}: {result['name']}**\n\n"
    
    if 'pm25' in result:
        text += f"📊 **PM2.5:** {result['pm25']:.1f} µg/m³\n"
        text += f"**PM10:** {result['pm10']:.1f} µg/m³\n"
        text += f"**NO₂:** {result['no2']:.1f} µg/m³\n\n"
    
    if weather:
        wind_dir = get_wind_direction(weather['wind_deg'], lang)
        text += f"{get_text(user_id, 'weather_title')}\n"
        text += f"{get_text(user_id, 'wind')}: {wind_dir}, {weather['wind_speed']} м/с\n"
        text += f"{get_text(user_id, 'temp')}: {weather['temp']}°C\n\n"
    
    text += f"{get_text(user_id, 'sport_title')}\n{result['sport']}\n\n"
    text += f"{get_text(user_id, 'food_title')}\n{result['food']}\n\n"
    text += f"---\n_{get_text(user_id, 'updated')}_"
    
    bot.send_message(message.chat.id, text, parse_mode='Markdown')


# ============ ЗАПУСК ============
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден")
        exit(1)
    print("✅ Бот запущен...")
    bot.polling(none_stop=True)
