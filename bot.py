import telebot
from telebot import types
import requests
import os
from datetime import datetime

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")  # Только для погоды

bot = telebot.TeleBot(BOT_TOKEN)

# ============ ПЕРЕВОДЫ (RU/KZ/EN) ============
TRANSLATIONS = {
    "ru": {
        "welcome": "🌍 **Добро пожаловать в AirAdvice!**\n\nОтправьте ваше местоположение, чтобы получить рекомендации.",
        "choose_lang": "Выберите язык / Тілді таңдаңыз / Choose language:",
        "send_location": "📍 Отправить местоположение",
        "air_quality": "Качество воздуха",
        "no_data": "Не могу получить данные о воздухе. Попробуйте позже.",
        "sport_title": "🏃‍♂️ **Активность:**",
        "food_title": "🥗 **Питание:**",
        "weather_title": "💨 **Погода:**",
        "wind": "Ветер",
        "temp": "Температура",
        "humidity": "Влажность",
        "updated": "Обновлено автоматически",
        "source_found": "Обнаружен объект рядом",
        "data_source": "Источник данных",
        "clean": "Чистый воздух",
        "moderate": "Умеренное загрязнение",
        "high": "Повышенное загрязнение",
        "dangerous": "Опасный уровень",
        "sport_clean": "✅ Воздух чистый! Отличное время для пробежки или прогулки.",
        "sport_moderate": "🏃‍♂️ Можно гулять, но интенсивные тренировки лучше перенести в зал.",
        "sport_high": "⚠️ Лучше тренироваться только в помещении. На улице используйте маску.",
        "sport_dangerous": "⛔ Не выходите на улицу без необходимости. Спорт только дома.",
        "food_clean": "🥗 Обычный сбалансированный рацион. Сезонные овощи и фрукты.",
        "food_moderate": "🥦 Добавьте антиоксиданты: зелёный чай, яблоки, брокколи.",
        "food_high": "💊 Пейте больше воды. Добавьте витамин C и Омега-3. Исключите жареное.",
        "food_dangerous": "🍵 Сорбенты (активированный уголь), кинза, морская капуста. Обильное питьё.",
        "landfill": "Свалка",
        "industrial": "Промзона",
        "power_plant": "ТЭЦ",
        "traffic": "Оживлённая трасса"
    },
    "kz": {
        "welcome": "🌍 **AirAdvice-қа қош келдіңіз!**\n\nҰсыныстар алу үшін орналасқан жеріңізді жіберіңіз.",
        "choose_lang": "Выберите язык / Тілді таңдаңыз / Choose language:",
        "send_location": "📍 Орналасқан жерді жіберу",
        "air_quality": "Ауа сапасы",
        "no_data": "Ауа туралы деректерді ала алмадым. Кейінірек көріңіз.",
        "sport_title": "🏃‍♂️ **Белсенділік:**",
        "food_title": "🥗 **Тамақтану:**",
        "weather_title": "💨 **Ауа райы:**",
        "wind": "Жел",
        "temp": "Температура",
        "humidity": "Ылғалдылық",
        "updated": "Автоматты түрде жаңартылды",
        "source_found": "Жақын жерде нысан табылды",
        "data_source": "Дереккөз",
        "clean": "Таза ауа",
        "moderate": "Орташа ластану",
        "high": "Жоғары ластану",
        "dangerous": "Қауіпті деңгей",
        "sport_clean": "✅ Ауа таза! Жүгіру немесе серуендеу үшін тамаша уақыт.",
        "sport_moderate": "🏃‍♂️ Серуендеуге болады, бірақ қарқынды жаттығуларды залға ауыстырған жөн.",
        "sport_high": "⚠️ Тек үй ішінде жаттығу ұсынылады. Сыртта маска қолданыңыз.",
        "sport_dangerous": "⛔ Қажетсіз сыртқа шықпаңыз. Спортпен тек үйде айналысыңыз.",
        "food_clean": "🥗 Кәдімгі теңдестірілген тамақтану. Маусымдық көкөністер мен жемістер.",
        "food_moderate": "🥦 Антиоксиданттар қосыңыз: көк шай, алма, брокколи.",
        "food_high": "💊 Көбірек су ішіңіз. С дәрумені мен Омега-3 қосыңыз.",
        "food_dangerous": "🍵 Сорбенттер (белсендірілген көмір), кинза, теңіз балдыры.",
        "landfill": "Қоқыс үйіндісі",
        "industrial": "Өнеркәсіп аймағы",
        "power_plant": "ЖЭО",
        "traffic": "Көлік жолы"
    },
    "en": {
        "welcome": "🌍 **Welcome to AirAdvice!**\n\nSend your location to get recommendations.",
        "choose_lang": "Выберите язык / Тілді таңдаңыз / Choose language:",
        "send_location": "📍 Send location",
        "air_quality": "Air Quality",
        "no_data": "Cannot get air quality data. Try later.",
        "sport_title": "🏃‍♂️ **Activity:**",
        "food_title": "🥗 **Nutrition:**",
        "weather_title": "💨 **Weather:**",
        "wind": "Wind",
        "temp": "Temperature",
        "humidity": "Humidity",
        "updated": "Updated automatically",
        "source_found": "Found nearby object",
        "data_source": "Data source",
        "clean": "Clean air",
        "moderate": "Moderate pollution",
        "high": "High pollution",
        "dangerous": "Dangerous level",
        "sport_clean": "✅ Air is clean! Great time for running or walking.",
        "sport_moderate": "🏃‍♂️ You can walk, but intense workouts better move to gym.",
        "sport_high": "⚠️ Better to exercise only indoors. Use mask outside.",
        "sport_dangerous": "⛔ Don't go outside without necessity. Sports only at home.",
        "food_clean": "🥗 Normal balanced diet. Seasonal vegetables and fruits.",
        "food_moderate": "🥦 Add antioxidants: green tea, apples, broccoli.",
        "food_high": "💊 Drink more water. Add Vitamin C and Omega-3.",
        "food_dangerous": "🍵 Sorbents (activated charcoal), cilantro, seaweed.",
        "landfill": "Landfill",
        "industrial": "Industrial zone",
        "power_plant": "Power plant",
        "traffic": "Busy road"
    }
}

# ============ ХРАНЕНИЕ ЯЗЫКОВ ============
user_languages = {}  # {user_id: "ru"}

# ============ ФУНКЦИИ СБОРА ДАННЫХ ============

def get_text(user_id, key):
    """Получаем текст на языке пользователя"""
    lang = user_languages.get(user_id, "ru")
    return TRANSLATIONS[lang].get(key, TRANSLATIONS["ru"][key])


def get_air_quality_waqi(lat, lon):
    """Получаем данные с WAQI (бесплатный demo token)"""
    try:
        url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token=demo"
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        data = response.json()
        
        if data.get('status') == 'ok' and data.get('data'):
            iaqi = data['data'].get('iaqi', {})
            result = {
                'pm25': iaqi.get('pm25', {}).get('v', 0),
                'pm10': iaqi.get('pm10', {}).get('v', 0),
                'no2': iaqi.get('no2', {}).get('v', 0),
                'so2': iaqi.get('so2', {}).get('v', 0),
                'co': iaqi.get('co', {}).get('v', 0),
                'o3': iaqi.get('o3', {}).get('v', 0)
            }
            return result
    except Exception as e:
        print(f"WAQI error: {e}")
    return None


def get_air_quality_openaq(lat, lon):
    """Получаем данные с OpenAQ (бесплатно, без ключа)"""
    try:
        url = f"https://api.openaq.org/v2/latest?coordinates={lat},{lon}&radius=10000&limit=10"
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        
        if data.get('results'):
            components = {
                'pm25': 0,
                'pm10': 0,
                'no2': 0,
                'so2': 0,
                'co': 0,
                'o3': 0
            }
            
            for measurement in data['results']:
                param = measurement.get('parameter', '')
                value = measurement.get('value', 0)
                if param in components:
                    components[param] = value
            
            return components
    except Exception as e:
        print(f"OpenAQ error: {e}")
    return None


def get_best_air_data(lat, lon):
    """Пробуем все источники по очереди"""
    
    # 1. WAQI (бесплатный, глобальный)
    air_data = get_air_quality_waqi(lat, lon)
    if air_data and any(air_data.values()):
        return air_data, "WAQI"
    
    # 2. OpenAQ (бесплатный, без ключа)
    air_data = get_air_quality_openaq(lat, lon)
    if air_data and any(air_data.values()):
        return air_data, "OpenAQ"
    
    # 3. Ничего не нашли
    return None, None


def get_nearby_sources(lat, lon):
    """Ищем ближайшие заводы, свалки через OpenStreetMap"""
    try:
        overpass_url = "https://overpass-api.de/api/interpreter"
        query = f"""
        [out:json];
        (
          way["landuse"="landfill"](around:5000,{lat},{lon});
          way["landuse"="industrial"](around:5000,{lat},{lon});
          way["man_made"="works"](around:5000,{lat},{lon});
          node["power"="plant"](around:5000,{lat},{lon});
        );
        out center tags;
        """
        response = requests.post(overpass_url, data=query, timeout=15)
        data = response.json()
        
        sources = []
        for element in data.get('elements', []):
            tags = element.get('tags', {})
            
            if tags.get('landuse') == 'landfill':
                sources.append({'type': 'landfill', 'name': tags.get('name', 'Landfill')})
            elif tags.get('landuse') == 'industrial':
                sources.append({'type': 'industrial', 'name': tags.get('name', 'Industrial zone')})
            elif tags.get('man_made') == 'works':
                sources.append({'type': 'industrial', 'name': tags.get('name', 'Factory')})
            elif tags.get('power') == 'plant':
                sources.append({'type': 'power_plant', 'name': tags.get('name', 'Power plant')})
        
        return sources[:3]  # Максимум 3 объекта
    except Exception as e:
        print(f"OSM error: {e}")
        return []


def get_weather(lat, lon):
    """Получаем погоду из OpenWeatherMap"""
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
        print(f"Weather error: {e}")
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


def get_seasonal_products(month):
    """Возвращаем сезонные продукты по месяцу"""
    if month in [12, 1, 2]:
        return ["капуста", "морковь", "свёкла", "хурма"]
    elif month in [3, 4, 5]:
        return ["зелень", "редис", "щавель", "клубника"]
    elif month in [6, 7, 8]:
        return ["огурцы", "помидоры", "арбуз", "дыня"]
    else:
        return ["тыква", "яблоки", "облепиха", "гранат"]


def analyze_air_quality(air_data, user_id):
    """Анализируем качество воздуха"""
    if not air_data:
        return {
            "name": get_text(user_id, "no_data"),
            "emoji": "⚪",
            "sport": get_text(user_id, "no_data"),
            "food": get_text(user_id, "no_data"),
            "danger": "unknown",
            "pm25": 0, "pm10": 0, "no2": 0, "so2": 0
        }
    
    pm25 = air_data.get('pm25', 0)
    pm10 = air_data.get('pm10', 0)
    no2 = air_data.get('no2', 0)
    so2 = air_data.get('so2', 0)
    
    if pm25 <= 15:
        level = "clean"
        emoji = "🟢"
    elif pm25 <= 35:
        level = "moderate"
        emoji = "🟡"
    elif pm25 <= 75:
        level = "high"
        emoji = "🟠"
    else:
        level = "dangerous"
        emoji = "🔴"
    
    return {
        "name": get_text(user_id, level),
        "emoji": emoji,
        "sport": get_text(user_id, f"sport_{level}"),
        "food": get_text(user_id, f"food_{level}"),
        "danger": level,
        "pm25": pm25,
        "pm10": pm10,
        "no2": no2,
        "so2": so2
    }


# ============ КОМАНДЫ ============

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    user_languages[user_id] = "ru"
    
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
        types.InlineKeyboardButton("🇰🇿 Қазақша", callback_data="lang_kz"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
    )
    
    bot.send_message(
        message.chat.id,
        "🌍 **AirAdvice**\n\n" + get_text(user_id, "choose_lang"),
        reply_markup=markup,
        parse_mode='Markdown'
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('lang_'))
def handle_language(call):
    user_id = call.from_user.id
    lang = call.data.split('_')[1]
    user_languages[user_id] = lang
    
    bot.answer_callback_query(call.id)
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton(get_text(user_id, "send_location"), request_location=True))
    
    bot.send_message(
        call.message.chat.id,
        get_text(user_id, "welcome"),
        reply_markup=markup,
        parse_mode='Markdown'
    )


@bot.message_handler(content_types=['location'])
def handle_location(message):
    user_id = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    # 1. Получаем данные о воздухе
    air_data, source_name = get_best_air_data(lat, lon)
    
    # 2. Получаем погоду
    weather = get_weather(lat, lon)
    
    # 3. Ищем ближайшие источники загрязнения
    sources = get_nearby_sources(lat, lon)
    
    # 4. Анализируем
    result = analyze_air_quality(air_data, user_id)
    
    # 5. Сезонные продукты
    current_month = datetime.now().month
    seasonal = get_seasonal_products(current_month)
    
    # Формируем ответ
    text = f"{result['emoji']} **{get_text(user_id, 'air_quality')}: {result['name']}**\n\n"
    
    # Показатели
    if result['pm25'] > 0:
        text += "📊 **Показатели:**\n"
        text += f"• PM2.5: {result['pm25']:.1f} µg/m³\n"
        text += f"• PM10: {result['pm10']:.1f} µg/m³\n"
        text += f"• NO₂: {result['no2']:.1f} µg/m³\n"
        text += f"• SO₂: {result['so2']:.1f} µg/m³\n\n"
    
    # Погода
    if weather:
        wind_dir = get_wind_direction(weather['wind_deg'], user_languages.get(user_id, "ru"))
        text += f"{get_text(user_id, 'weather_title')}\n"
        text += f"{get_text(user_id, 'temp')}: {weather['temp']:.0f}°C\n"
        text += f"{get_text(user_id, 'humidity')}: {weather['humidity']}%\n"
        text += f"{get_text(user_id, 'wind')}: {wind_dir}, {weather['wind_speed']} м/с\n\n"
    
    # Ближайшие источники загрязнения
    if sources:
        text += f"🏭 **{get_text(user_id, 'source_found')}:**\n"
        for src in sources:
            src_type = get_text(user_id, src['type'])
            text += f"• {src_type}: {src['name']}\n"
        text += "\n"
    
    # Рекомендации
    text += f"{get_text(user_id, 'sport_title')}\n{result['sport']}\n\n"
    text += f"{get_text(user_id, 'food_title')}\n{result['food']}\n"
    
    # Сезонные продукты
    text += f"\n🛒 **{get_text(user_id, 'data_source')}:** {source_name or 'Unknown'}\n"
    
    text += f"\n---\n_{get_text(user_id, 'updated')}_"
    
    bot.send_message(message.chat.id, text, parse_mode='Markdown')


# ============ ЗАПУСК ============
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ Ошибка: BOT_TOKEN не найден")
        exit(1)
    
    print("✅ Бот запущен...")
    bot.polling(none_stop=True)
