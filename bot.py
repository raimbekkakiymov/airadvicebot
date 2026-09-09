import os
import sys
import math
import time
import json
import logging
import threading
import requests
import telebot
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ==========================================
# 1. СЕРВЕР ДЛЯ RENDER (HEALTH CHECK)
# ==========================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    
    def log_message(self, format, *args):
        pass

def start_health_check_server():
    try:
        port = int(os.getenv("PORT", 8080))
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        print(f"✅ Health check server запущен на порту {port}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"❌ Ошибка health check server: {e}", flush=True)

def keep_alive():
    """Поддерживаем сервис активным"""
    while True:
        time.sleep(240)
        try:
            print(f"✅ Бот активен: {datetime.now()}", flush=True)
        except:
            pass

# ==========================================
# 2. НАСТРОЙКИ
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
WAQI_API_KEY = os.getenv("WAQI_API_KEY") or "demo"

# Настройки DeepSeek
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

PID_FILE = "bot.pid"
USER_LANG_FILE = "user_languages.json"

if not BOT_TOKEN:
    print("❌ ВНИМАНИЕ: BOT_TOKEN не установлен!", flush=True)
    BOT_TOKEN = "DUMMY_TOKEN"

bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

user_languages = {}
user_ids = set()

def load_user_languages():
    global user_languages
    if os.path.exists(USER_LANG_FILE):
        try:
            with open(USER_LANG_FILE, 'r', encoding='utf-8') as f:
                user_languages = json.load(f)
            print(f"✅ Загружено {len(user_languages)} языков пользователей", flush=True)
        except Exception as e:
            logging.error(f"Ошибка загрузки: {e}")

def save_user_languages():
    try:
        with open(USER_LANG_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_languages, f, ensure_ascii=False, indent=2)
        print(f"✅ Языки сохранены: {user_languages}", flush=True)
    except Exception as e:
        logging.error(f"Ошибка сохранения: {e}")

# ==========================================
# 3. DEEPSEEK API
# ==========================================

def call_deepseek(prompt, system_prompt=None, max_tokens=1000, temperature=0.7):
    """Универсальная функция для вызова DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        print("❌ DEEPSEEK_API_KEY не установлен", flush=True)
        return None
    
    try:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        body = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        print(f"🤖 Запрос к DeepSeek...", flush=True)
        r = requests.post(DEEPSEEK_URL, headers=headers, json=body, timeout=30)
        
        if r.status_code == 200:
            data = r.json()
            if 'choices' in data and data['choices']:
                result = data['choices'][0]['message']['content']
                print("✅ DeepSeek ответил", flush=True)
                return result
        else:
            print(f"❌ DeepSeek error {r.status_code}: {r.text}", flush=True)
    
    except Exception as e:
        print(f"❌ DeepSeek exception: {e}", flush=True)
    
    return None

# ==========================================
# 4. МАТЕМАТИКА
# ==========================================

def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def get_wind_direction_text(deg, lang='ru'):
    directions = {
        'ru': ["Северный", "Северо-восточный", "Восточный", "Юго-восточный",
               "Южный", "Юго-западный", "Западный", "Северо-западный"],
        'kk': ["Солтүстік", "Солтүстік-шығыс", "Шығыс", "Оңтүстік-шығыс",
               "Оңтүстік", "Оңтүстік-батыс", "Батыс", "Солтүстік-батыс"],
        'en': ["North", "Northeast", "East", "Southeast",
               "South", "Southwest", "West", "Northwest"]
    }
    index = round(deg / 45) % 8
    return directions.get(lang, directions['ru'])[index]

# ==========================================
# 5. ВНЕШНИЕ API
# ==========================================

def get_weather(lat, lon):
    if not WEATHER_API_KEY:
        return None
    print("💨 Запрос погоды...", flush=True)
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json()
            return {
                'temp': data['main']['temp'],
                'humidity': data['main']['humidity'],
                'wind_speed': data['wind']['speed'],
                'wind_deg': data['wind'].get('deg', 0),
                'description': data['weather'][0]['description']
            }
    except Exception as e:
        logging.error(f"Weather error: {e}")
    return None

def get_best_air_data(lat, lon):
    print("📊 Запрос качества воздуха...", flush=True)
    try:
        token = WAQI_API_KEY or "demo"
        url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={token}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        
        if data.get('status') == 'ok' and data.get('data'):
            iaqi = data['data'].get('iaqi', {})
            result = {
                'aqi': data['data'].get('aqi'),
                'pm25': iaqi.get('pm25', {}).get('v'),
                'pm10': iaqi.get('pm10', {}).get('v'),
                'no2': iaqi.get('no2', {}).get('v'),
                'so2': iaqi.get('so2', {}).get('v'),
                'co': iaqi.get('co', {}).get('v'),
                'o3': iaqi.get('o3', {}).get('v')
            }
            result = {k: v for k, v in result.items() if v is not None}
            if result.get('aqi') or result.get('pm25'):
                print(f"✅ WAQI: AQI={result.get('aqi')}", flush=True)
                return result, "WAQI"
    except Exception as e:
        logging.error(f"WAQI error: {e}")
    
    return None, "None"

def calculate_aqi_from_pm25(pm25):
    if pm25 <= 12:
        return round((50 / 12) * pm25)
    elif pm25 <= 35.4:
        return round(((100 - 51) / (35.4 - 12.1)) * (pm25 - 12.1) + 51)
    elif pm25 <= 55.4:
        return round(((150 - 101) / (55.4 - 35.5)) * (pm25 - 35.5) + 101)
    elif pm25 <= 150.4:
        return round(((200 - 151) / (150.4 - 55.5)) * (pm25 - 55.5) + 151)
    else:
        return 200

# ==========================================
# 6. АНАЛИЗ
# ==========================================

def analyze_pollution(air_data, lang='ru'):
    aqi = air_data.get('aqi') if air_data else None
    pm25 = air_data.get('pm25') if air_data else None
    
    if pm25 and pm25 > 25 and (not aqi or aqi < 50):
        print(f"⚠️ Пересчет AQI из PM2.5={pm25}", flush=True)
        aqi = calculate_aqi_from_pm25(pm25)
    
    levels = {
        'ru': {1: "Чистый воздух", 2: "Умеренное качество", 3: "Вредно для чувствительных групп",
               4: "Вредный уровень", 5: "Опасный уровень"},
        'kk': {1: "Таза ауа", 2: "Орташа сапа", 3: "Сезімтал топтар үшін зиянды",
               4: "Зиянды деңгей", 5: "Қауіпті деңгей"},
        'en': {1: "Clean air", 2: "Moderate quality", 3: "Unhealthy for sensitive groups",
               4: "Unhealthy level", 5: "Hazardous level"}
    }
    
    t = levels.get(lang, levels['ru'])
    
    if aqi:
        if aqi <= 50:
            return {'level_str': t[1], 'level_code': 1}
        elif aqi <= 100:
            return {'level_str': t[2], 'level_code': 2}
        elif aqi <= 150:
            return {'level_str': t[3], 'level_code': 3}
        elif aqi <= 200:
            return {'level_str': t[4], 'level_code': 4}
        else:
            return {'level_str': t[5], 'level_code': 5}
    
    return {'level_str': t[1], 'level_code': 1}

# ==========================================
# 7. DEEPSEEK АНАЛИЗ ИСТОЧНИКОВ
# ==========================================

def get_ai_source_analysis(lat, lon, wind_deg, wind_dir_text, air_data, lang='ru'):
    """Короткий анализ источников через DeepSeek"""
    if not DEEPSEEK_API_KEY:
        return None
    
    lang_name = {'ru': 'Русский', 'kk': 'Казахский', 'en': 'English'}.get(lang, 'Русский')
    
    # Получаем данные кратко
    aqi = air_data.get('aqi', 'N/A') if air_data else 'N/A'
    pm25 = air_data.get('pm25', 'N/A') if air_data else 'N/A'
    so2 = air_data.get('so2', 'N/A') if air_data else 'N/A'
    no2 = air_data.get('no2', 'N/A') if air_data else 'N/A'
    
    prompt = (
        f"Ты эксперт по экологии. ОТВЕЧАЙ КОРОТКО (2-3 предложения).\n\n"
        f"Данные: AQI={aqi}, PM2.5={pm25}, SO2={so2}, NO2={no2}\n"
        f"Ветер: {wind_dir_text}\n"
        f"Координаты: {lat}, {lon}\n\n"
        f"ФОРМАТ (строго):\n"
        f"🏭 Источники: [1-2 вероятных источника]\n"
        f"⚠️ Элементы: [2-3 элемента]\n\n"
        f"Язык: {lang_name}"
    )
    
    system_prompt = f"Отвечай кратко, без лишних слов. Язык: {lang_name}"
    
    print("🏭 Короткий анализ источников...", flush=True)
    return call_deepseek(prompt, system_prompt, max_tokens=200, temperature=0.3)

# ==========================================
# 8. DEEPSEEK РЕКОМЕНДАЦИИ
# ==========================================

def get_ai_recommendations(air_data, weather, pollution_analysis, lang='ru', source_analysis=None):
    """Рекомендации на основе анализа источников"""
    if not DEEPSEEK_API_KEY:
        return get_rule_based_recommendations(pollution_analysis, lang)
    
    wind_dir = get_wind_direction_text(weather['wind_deg'], lang) if weather else 'N/A'
    lang_name = {'ru': 'Русский', 'kk': 'Казахский', 'en': 'English'}.get(lang, 'Русский')
    
    # Краткие данные
    aqi = air_data.get('aqi', 'N/A') if air_data else 'N/A'
    pm25 = air_data.get('pm25', 'N/A') if air_data else 'N/A'
    temp = weather.get('temp', 'N/A') if weather else 'N/A'
    
    # Базовый промпт
    prompt = (
        f"Ты эксперт по здоровью. ОТВЕЧАЙ КОРОТКО.\n\n"
        f"Воздух: AQI={aqi}, PM2.5={pm25}, Температура={temp}°C\n"
        f"Ветер: {wind_dir}\n"
    )
    
    # Добавляем анализ источников если есть
    if source_analysis:
        prompt += f"\nАнализ источников:\n{source_analysis}\n"
    
    prompt += (
        f"\nДай КОРОТКИЕ рекомендации:\n"
        f"1. 🏃‍♂️ Активность: [1 предложение]\n"
        f"2. 🥗 Питание: [3-4 продукта]\n"
        f"3. 💧 Вода: [1 предложение]\n"
        f"4. 💊 Витамины: [2-3 штуки]\n\n"
        f"Без лишних объяснений. Язык: {lang_name}"
    )
    
    system_prompt = f"Отвечай кратко, по делу. Язык: {lang_name}"
    
    print("🤖 Короткие рекомендации...", flush=True)
    result = call_deepseek(prompt, system_prompt, max_tokens=300, temperature=0.4)
    
    if result:
        return result
    
    return get_rule_based_recommendations(pollution_analysis, lang)

def get_rule_based_recommendations(pollution_analysis, lang='ru'):
    risk_level = pollution_analysis.get('level_code', 1)
    
    activity_map = {
        'ru': {1: "✅ Можно бегать", 2: "🏃‍♂️ Можно гулять", 3: "⚠️ Лучше в зал", 4: "⛔ Только дома", 5: "🚫 Оставайтесь дома"},
        'kk': {1: "✅ Жүгіруге болады", 2: "🏃‍♂️ Серуендеуге болады", 3: "⚠️ Залға барыңыз", 4: "⛔ Тек үйде", 5: "🚫 Үйде болыңыз"},
        'en': {1: "✅ You can run", 2: "🏃‍♂️ You can walk", 3: "⚠️ Better go to gym", 4: "⛔ Indoor only", 5: "🚫 Stay home"}
    }
    
    activity = activity_map.get(lang, activity_map['ru']).get(risk_level, "✅ OK")
    
    return (
        f"🏃‍♂️ Физическая активность:\n{activity}\n\n"
        f"🥗 Питание:\n• Овощи и фрукты\n• Зелёный чай\n• Брокколи\n\n"
        f"💧 Питьевой режим:\n• 2 литра в день\n\n"
        f"💊 Витамины:\n• Витамин C\n• Омега-3"
    )

# ==========================================
# 9. ФОРМАТИРОВАНИЕ
# ==========================================

def format_full_response(air_data, weather, pollution_analysis, recommendations, source_name, lang='ru', ai_source_analysis=None):
    if not lang:
        lang = 'ru'
    
    titles = {
        'ru': {'report': "Экологический отчет", 'air_quality': "Качество воздуха", 'status': "Статус",
               'weather': "Погода", 'temp': "Температура", 'humidity': "Влажность", 'wind': "Ветер",
               'no_data': "Нет данных", 'source': "Источник"},
        'kk': {'report': "Экологиялық есеп", 'air_quality': "Ауа сапасы", 'status': "Статус",
               'weather': "Ауа райы", 'temp': "Температура", 'humidity': "Ылғалдылық", 'wind': "Жел",
               'no_data': "Деректер жоқ", 'source': "Дереккөз"},
        'en': {'report': "Environmental Report", 'air_quality': "Air Quality", 'status': "Status",
               'weather': "Weather", 'temp': "Temperature", 'humidity': "Humidity", 'wind': "Wind",
               'no_data': "No data", 'source': "Source"}
    }
    
    t = titles.get(lang, titles['ru'])
    
    msg = f"🌍 **{t['report']}**\n"
    msg += "───────────────────────\n\n"
    
    if air_data:
        msg += f"📊 **{t['air_quality']}:**\n"
        msg += f"• AQI: {air_data.get('aqi', t['no_data'])}\n"
        msg += f"• PM2.5: {air_data.get('pm25', t['no_data'])} µg/m3\n"
        msg += f"• PM10: {air_data.get('pm10', t['no_data'])} µg/m3\n"
        msg += f"• NO2: {air_data.get('no2', t['no_data'])} µg/m3\n"
        msg += f"• SO2: {air_data.get('so2', t['no_data'])} µg/m3\n"
        
        pm25 = air_data.get('pm25')
        if pm25 and pm25 > 25:
            if lang == 'ru':
                msg += f"⚠️ PM2.5 превышает норму ВОЗ (25 µg/m3)\n"
            elif lang == 'kk':
                msg += f"⚠️ PM2.5 ДДҰ нормасынан асып түсті (25 µg/m3)\n"
            else:
                msg += f"⚠️ PM2.5 exceeds WHO limit (25 µg/m3)\n"
        
        msg += f"{t['status']}: **{pollution_analysis['level_str']}**\n\n"
    else:
        msg += f"📊 **{t['air_quality']}:** {t['no_data']}\n\n"
    
    if weather:
        msg += f"💨 **{t['weather']}:**\n"
        msg += f"• {t['temp']}: {weather['temp']} C\n"
        msg += f"• {t['humidity']}: {weather['humidity']}%\n"
        msg += f"• {t['wind']}: {get_wind_direction_text(weather['wind_deg'], lang)}, {weather['wind_speed']} м/с\n\n"
    
    if ai_source_analysis:
        msg += f"{ai_source_analysis}\n\n"
    
    msg += "───────────────────────\n"
    msg += f"{recommendations}"
    msg += f"\n\n📡 _{t['source']}: {source_name}_"
    msg += f"\n🤖 _AI: DeepSeek_"
    
    return msg

def safe_send_message(chat_id, text):
    """Отправка с кнопками"""
    max_length = 4000
    
    # Создаем клавиатуру с кнопками
    lang = user_languages.get(str(chat_id), 'ru')
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    location_text = {
        'ru': "📍 Новая локация",
        'kk': "📍 Жаңа орын",
        'en': "📍 New location"
    }.get(lang, "📍 New location")
    
    refresh_text = {
        'ru': "🔄 Обновить",
        'kk': "🔄 Жаңарту",
        'en': "🔄 Refresh"
    }.get(lang, "🔄 Refresh")
    
    markup.add(types.KeyboardButton(location_text, request_location=True))
    markup.add(types.KeyboardButton(refresh_text))
    
    # Остальной код отправки...
    
    try:
        if len(text) <= max_length:
            try:
                bot.send_message(chat_id, text, parse_mode='Markdown')
            except:
                bot.send_message(chat_id, text)
            return
        
        print(f"📤 Разбиваю длинное сообщение ({len(text)} символов)...", flush=True)
        
        # Разбиваем по строкам
        parts = []
        current = ""
        
        for line in text.split('\n'):
            if len(current) + len(line) + 1 > max_length:
                if current:
                    parts.append(current)
                current = line
            else:
                current = (current + '\n' + line) if current else line
        
        if current:
            parts.append(current)
        
        # Отправляем части
        for i, part in enumerate(parts):
            if len(parts) > 1:
                part += f"\n\n📄 Часть {i+1}/{len(parts)}"
            
            try:
                bot.send_message(chat_id, part, parse_mode='Markdown')
            except:
                bot.send_message(chat_id, part)
            
            time.sleep(0.5)
        
        print(f"✅ Отправлено {len(parts)} частей", flush=True)
        
    except Exception as e:
        logging.error(f"Send error: {e}")

# ==========================================
# 10. ОБРАБОТЧИКИ
# ==========================================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_ids.add(message.chat.id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English')
    bot.send_message(message.chat.id, "Выберите язык / Тілді таңдаңыз / Choose language:", reply_markup=markup)

@bot.message_handler(commands=['lang'])
def change_language(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English')
    bot.send_message(message.chat.id, "Выберите язык / Choose language:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English'])
def set_language(message):
    if 'Русский' in message.text:
        lang = 'ru'
    elif 'Қазақша' in message.text:
        lang = 'kk'
    else:
        lang = 'en'
    
    user_languages[str(message.chat.id)] = lang
    save_user_languages()
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    # Кнопка локации
    location_text = {
        'ru': "📍 Отправить локацию",
        'kk': "📍 Орынды жіберу",
        'en': "📍 Send Location"
    }.get(lang, "📍 Send Location")
    markup.add(types.KeyboardButton(location_text, request_location=True))
    
    # Кнопка Старт
    start_text = {
        'ru': "🚀 Старт",
        'kk': "🚀 Бастау",
        'en': "🚀 Start"
    }.get(lang, "🚀 Start")
    markup.add(types.KeyboardButton(start_text))
    
    confirm = {
        'ru': "Язык сохранен! Выберите действие:",
        'kk': "Тіл сақталды! Әрекетті таңдаңыз:",
        'en': "Language saved! Choose action:"
    }.get(lang, "Language saved!")
    
    bot.send_message(message.chat.id, confirm, reply_markup=markup)
    
@bot.message_handler(func=lambda m: m.text in ['🚀 Старт', '🚀 Бастау', '🚀 Start'])
def start_button(message):
    """Обработка кнопки Старт"""
    user_id = str(message.chat.id)
    lang = user_languages.get(user_id, 'ru')
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    welcome_text = {
        'ru': (
            "👋 Добро пожаловать!\n\n"
            "Я помогу узнать качество воздуха в вашем районе.\n\n"
            "📊 Что я умею:\n"
            "• Показывать AQI и загрязнители\n"
            "• Определять источники загрязнения\n"
            "• Давать рекомендации по здоровью\n\n"
            "📍 Отправьте вашу геолокацию, чтобы начать!"
        ),
        'kk': (
            "👋 Қош келдіңіз!\n\n"
            "Мен сіздің аймағыңыздағы ауа сапасын білуге көмектесемін.\n\n"
            "📊 Не істей аламын:\n"
            "• AQI және ластаушыларды көрсету\n"
            "• Ластану көздерін анықтау\n"
            "• Денсаулық бойынша ұсыныстар беру\n\n"
            "📍 Бастау үшін геолокацияңызды жіберіңіз!"
        ),
        'en': (
            "👋 Welcome!\n\n"
            "I'll help you check air quality in your area.\n\n"
            "📊 What I can do:\n"
            "• Show AQI and pollutants\n"
            "• Identify pollution sources\n"
            "• Give health recommendations\n\n"
            "📍 Send your location to start!"
        )
    }.get(lang, "Welcome!")
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    location_text = {
        'ru': "📍 Отправить локацию",
        'kk': "📍 Орынды жіберу",
        'en': "📍 Send Location"
    }.get(lang, "📍 Send Location")
    markup.add(types.KeyboardButton(location_text, request_location=True))
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)
@bot.message_handler(func=lambda m: m.text in ['🔄 Обновить', '🔄 Жаңарту', '🔄 Refresh'])
def refresh_data(message):
    """Обновление данных"""
    user_id = str(message.chat.id)
    lang = user_languages.get(user_id, 'ru')
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    location_text = {
        'ru': "📍 Отправить локацию",
        'kk': "📍 Орынды жіберу",
        'en': "📍 Send Location"
    }.get(lang, "📍 Send Location")
    markup.add(types.KeyboardButton(location_text, request_location=True))
    
    msg = {
        'ru': "Отправьте новую локацию для обновления данных.",
        'kk': "Деректерді жаңарту үшін жаңа геолокация жіберіңіз.",
        'en': "Send new location to update data."
    }.get(lang, "Send location")
    
    bot.send_message(message.chat.id, msg, reply_markup=markup)

@bot.message_handler(content_types=['location'])
def handle_location(message):
    print("📍 Геолокация получена", flush=True)
    user_id = str(message.chat.id)
    user_ids.add(message.chat.id)
    
    lang = user_languages.get(user_id, 'ru')
    
    if user_id not in user_languages:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row('🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English')
        bot.send_message(message.chat.id, "Сначала выберите язык / First choose language:", reply_markup=markup)
        return
    
    lat = float(message.location.latitude)
    lon = float(message.location.longitude)
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    air_data, source_name = get_best_air_data(lat, lon)
    weather = get_weather(lat, lon)
    
    wind_deg = weather.get('wind_deg', 0) if weather else 0
    wind_dir_text = get_wind_direction_text(wind_deg, lang)
    
    # ИЗМЕНЕНИЕ 1: Получаем короткий анализ источников
    ai_source_analysis = get_ai_source_analysis(lat, lon, wind_deg, wind_dir_text, air_data, lang)
    
    pollution_analysis = analyze_pollution(air_data, lang)
    
    # ИЗМЕНЕНИЕ 2: Передаем анализ источников в рекомендации
    recommendations = get_ai_recommendations(air_data, weather, pollution_analysis, lang, ai_source_analysis)
    
    response = format_full_response(
        air_data, weather, pollution_analysis,
        recommendations, source_name, lang, ai_source_analysis
    )
    
    safe_send_message(message.chat.id, response)

# ==========================================
# 11. ФОНОВЫЕ ЗАДАЧИ
# ==========================================

def background_notifier():
    while True:
        time.sleep(21600)
        for uid in list(user_ids):
            try:
                lang = user_languages.get(str(uid), 'ru')
                remind_text = {
                    'ru': "🔔 Проверьте качество воздуха!",
                    'kk': "🔔 Ауа сапасын тексеріңіз!",
                    'en': "🔔 Check air quality!"
                }.get(lang, "🔔 Check air quality!")
                bot.send_message(uid, remind_text)
            except Exception as e:
                logging.error(f"Ошибка уведомления: {e}")

# ==========================================
# 12. ЗАПУСК
# ==========================================

if __name__ == '__main__':
    print("=" * 50, flush=True)
    print("🚀 ЗАПУСК БОТА...", flush=True)
    print(f"🔑 BOT_TOKEN: {'✅' if BOT_TOKEN and BOT_TOKEN != 'DUMMY_TOKEN' else '❌'}", flush=True)
    print(f"🤖 DEEPSEEK: {'✅' if DEEPSEEK_API_KEY else '❌'}", flush=True)
    print(f"🌤 WEATHER: {'✅' if WEATHER_API_KEY else '❌'}", flush=True)
    print("=" * 50, flush=True)
    
    if not BOT_TOKEN or BOT_TOKEN == "DUMMY_TOKEN":
        print("❌ Установите BOT_TOKEN!", flush=True)
        sys.exit(1)
    
    print("🔄 Очистка старых процессов...", flush=True)
    current_pid = os.getpid()
    os.system(f"ps aux | grep 'bot.py' | grep -v grep | grep -v {current_pid} | awk '{{print $2}}' | xargs -r kill -9 2>/dev/null || true")
    time.sleep(3)
    
    print("🔄 Удаление webhook...", flush=True)
    try:
        bot.remove_webhook()
        time.sleep(2)
        print("✅ Webhook удален", flush=True)
    except:
        pass
    
    load_user_languages()
    
    threading.Thread(target=start_health_check_server, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=background_notifier, daemon=True).start()
    
    print("🤖 БОТ ГОТОВ К РАБОТЕ!", flush=True)
    print("📡 Начинаю polling...", flush=True)
    
    try:
        bot.polling(none_stop=True, interval=1, timeout=30)
    except Exception as e:
        print(f"❌ Ошибка polling: {e}", flush=True)
        logging.error(f"Polling error: {e}")
        time.sleep(10)
        os.execv(sys.executable, ['python'] + sys.argv)
