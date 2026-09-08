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

# ==========================================
# 1. СЕРВЕР ДЛЯ RENDER (HEALTH CHECK)
# ==========================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def start_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ==========================================
# 2. НАСТРОЙКИ
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WAQI_API_KEY = os.getenv("WAQI_API_KEY") or "demo"

PID_FILE = "bot.pid"
USER_LANG_FILE = "user_languages.json"

bot = telebot.TeleBot(BOT_TOKEN if BOT_TOKEN else "DUMMY_TOKEN")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

user_languages = {}
user_ids = set()

def load_user_languages():
    global user_languages
    if os.path.exists(USER_LANG_FILE):
        try:
            with open(USER_LANG_FILE, 'r', encoding='utf-8') as f:
                user_languages = json.load(f)
        except Exception as e:
            logging.error(f"Ошибка загрузки: {e}")

def save_user_languages():
    try:
        with open(USER_LANG_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_languages, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения: {e}")

# ==========================================
# 3. PID LOCK
# ==========================================

def acquire_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print(f"Бот уже запущен с PID {old_pid}. Выход.", flush=True)
            sys.exit(1)
        except (OSError, ValueError):
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def release_pid_lock():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

# ==========================================
# 4. МАТЕМАТИКА
# ==========================================

def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def check_wind_from_source(wind_deg, source_bearing, tolerance=25):
    diff = abs(wind_deg - source_bearing)
    if diff > 180:
        diff = 360 - diff
    return diff <= tolerance

def get_wind_direction_text(deg, lang='ru'):
    directions_ru = ["Северный", "Северо-восточный", "Восточный", "Юго-восточный",
                     "Южный", "Юго-западный", "Западный", "Северо-западный"]
    directions_kk = ["Солтүстік", "Солтүстік-шығыс", "Шығыс", "Оңтүстік-шығыс",
                     "Оңтүстік", "Оңтүстік-батыс", "Батыс", "Солтүстік-батыс"]
    directions_en = ["North", "Northeast", "East", "Southeast",
                     "South", "Southwest", "West", "Northwest"]
    
    index = round(deg / 45) % 8
    
    if lang == 'kk':
        return directions_kk[index]
    elif lang == 'en':
        return directions_en[index]
    return directions_ru[index]

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
        logging.error(f"OpenWeatherMap error: {e}")
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
    
    try:
        url = f"https://api.openaq.org/v2/latest?coordinates={lat},{lon}&radius=25000&limit=10"
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        data = r.json()
        
        if data.get('results'):
            components = {}
            for measurement in data['results']:
                param = measurement.get('parameter', '')
                value = measurement.get('value', 0)
                if param in ['pm25', 'pm10', 'no2', 'so2', 'co', 'o3']:
                    components[param] = value
            
            if components:
                pm25 = components.get('pm25', 0)
                if pm25:
                    components['aqi'] = calculate_aqi_from_pm25(pm25)
                print(f"✅ OpenAQ: {components}", flush=True)
                return components, "OpenAQ"
    except Exception as e:
        logging.error(f"OpenAQ error: {e}")
    
    print("❌ Нет данных о воздухе", flush=True)
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

def get_nearby_sources(lat, lon):
    print("🏭 Поиск объектов...", flush=True)
    return []

# ==========================================
# 6. АНАЛИЗ
# ==========================================

def analyze_wind_and_sources(weather, sources, lat, lon):
    if not weather or not sources:
        return {'active_sources': [], 'nearby_sources_count': len(sources)}

    wind_deg = weather['wind_deg']
    active_sources = []

    for src in sources:
        bearing = calculate_bearing(lat, lon, src['lat'], src['lon'])
        if check_wind_from_source(wind_deg, bearing):
            active_sources.append({
                'name': src['name'],
                'bearing': round(bearing, 1)
            })

    return {
        'active_sources': active_sources,
        'nearby_sources_count': len(sources)
    }

def analyze_pollution(air_data, wind_analysis, lang='ru'):
    aqi = air_data.get('aqi') if air_data else None
    has_active_sources = len(wind_analysis.get('active_sources', [])) > 0

    levels = {
        'ru': {
            1: "Чистый воздух", 2: "Умеренное качество", 3: "Вредно для чувствительных групп",
            4: "Вредный уровень", 5: "Опасный уровень",
            'risk': "Повышенный риск (ветер с промзоны)", 'normal': "Норма (косвенная оценка)"
        },
        'kk': {
            1: "Таза ауа", 2: "Орташа сапа", 3: "Сезімтал топтар үшін зиянды",
            4: "Зиянды деңгей", 5: "Қауіпті деңгей",
            'risk': "Жоғары қауіп (өнеркәсіп аймағынан жел)", 'normal': "Қалыпты (жанама бағалау)"
        },
        'en': {
            1: "Clean air", 2: "Moderate quality", 3: "Unhealthy for sensitive groups",
            4: "Unhealthy level", 5: "Hazardous level",
            'risk': "Increased risk (wind from industrial zone)", 'normal': "Normal (indirect assessment)"
        }
    }
    
    t = levels.get(lang, levels['ru'])
    
    if aqi:
        if aqi <= 50: level, level_code = t[1], 1
        elif aqi <= 100: level, level_code = t[2], 2
        elif aqi <= 150: level, level_code = t[3], 3
        elif aqi <= 200: level, level_code = t[4], 4
        else: level, level_code = t[5], 5
    else:
        if has_active_sources:
            level, level_code = t['risk'], 3
        else:
            level, level_code = t['normal'], 1

    return {'level_str': level, 'level_code': level_code}

# ==========================================
# 7. ИИ: АНАЛИЗ ИСТОЧНИКОВ ПО ВЕТРУ
# ==========================================

def get_ai_source_analysis(lat, lon, wind_deg, wind_dir_text, air_data, lang='ru'):
    """
    ИИ определяет, что находится с наветренной стороны, и какие элементы могут быть
    """
    if not GEMINI_API_KEY:
        return None
    
    lang_names = {
        'ru': 'Русский',
        'kk': 'Казахский (Қазақша)',
        'en': 'English'
    }
    lang_name = lang_names.get(lang, 'Русский')
    
    try:
        prompt = f"""
Ты — эксперт по экологии и промышленной безопасности.

ПОЛЬЗОВАТЕЛЬ НАХОДИТСЯ:
- Координаты: {lat}, {lon}
- Ветер дует с: {wind_dir_text} (градус: {wind_deg}°)

ТЕКУЩИЕ ПОКАЗАТЕЛИ ВОЗДУХА:
- AQI: {air_data.get('aqi', 'Нет данных') if air_data else 'Нет данных'}
- Мелкие частицы: {air_data.get('pm25', 'Нет данных') if air_data else 'Нет данных'} µg/m3
- Крупная пыль: {air_data.get('pm10', 'Нет данных') if air_data else 'Нет данных'} µg/m3
- Диоксид азота: {air_data.get('no2', 'Нет данных') if air_data else 'Нет данных'} µg/m3
- Диоксид серы: {air_data.get('so2', 'Нет данных') if air_data else 'Нет данных'} µg/m3

ЗАДАЧА:
1. Используя свои знания о географии и промышленности, определи, какие промышленные объекты могут находиться с НАВЕТРЕННОЙ стороны (ветер дует ОТТУДА к пользователю)
2. Сопоставь показатели воздуха с возможными источниками
3. Сделай вывод: какие сопутствующие элементы могут быть в воздухе

ФОРМАТ ОТВЕТА:
🏭 **Вероятные источники:**
• [Название объекта] — [что выделяет]

⚠️ **Сопутствующие элементы:**
• [Элемент 1] — [опасность]
• [Элемент 2] — [опасность]

Ответь на языке: {lang_name}
"""
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        
        r = requests.post(url, headers=headers, json=body, timeout=10)
        data = r.json()
        
        if 'candidates' in data and data['candidates']:
            result = data['candidates'][0]['content']['parts'][0]['text']
            print("✅ ИИ определил источники", flush=True)
            return result
    
    except Exception as e:
        logging.error(f"ИИ анализ источников: {e}")
    
    return None

# ==========================================
# 8. ИИ РЕКОМЕНДАЦИИ
# ==========================================

def build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    wind_dir = get_wind_direction_text(weather['wind_deg'], lang) if weather else 'Н/Д'
    active_names = [s['name'] for s in wind_analysis.get('active_sources', [])]
    pollutants = ['Сажа', 'Пыль', 'Тяжёлые металлы']
    
    lang_names = {
        'ru': 'Русский',
        'kk': 'Казахский (Қазақша)',
        'en': 'English'
    }
    lang_name = lang_names.get(lang, 'Русский')
    
    prompt = f"""
Ты — эксперт по экологии, токсикологии и нутрициологии.

ДАННЫЕ:
- AQI: {air_data.get('aqi') if air_data else 'Нет данных'}
- Мелкие частицы: {air_data.get('pm25') if air_data else 'Нет данных'} µg/m3
- Крупная пыль: {air_data.get('pm10') if air_data else 'Нет данных'} µg/m3
- Диоксид азота: {air_data.get('no2') if air_data else 'Нет данных'} µg/m3
- Диоксид серы: {air_data.get('so2') if air_data else 'Нет данных'} µg/m3
- Температура: {weather.get('temp') if weather else 'Н/Д'}°C
- Влажность: {weather.get('humidity') if weather else 'Н/Д'}%
- Ветер: {wind_dir}, {weather.get('wind_speed') if weather else 'Н/Д'} м/с

Дай РАЗВЕРНУТЫЕ рекомендации:

1. ФИЗИЧЕСКАЯ АКТИВНОСТЬ
2. ПИТАНИЕ: 5-7 продуктов
3. ПИТЬЕВОЙ РЕЖИМ
4. ВИТАМИНЫ

КРИТИЧЕСКИ ВАЖНО: Отвечай ТОЛЬКО на {lang_name}. Названия продуктов пиши на {lang_name}.
"""
    return prompt

def get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    if not GEMINI_API_KEY:
        return None
    print("🤖 Запрос рекомендаций к Gemini...", flush=True)
    try:
        prompt = build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        r = requests.post(url, headers=headers, json=body, timeout=8)
        data = r.json()
        if 'candidates' in data and data['candidates']:
            print("✅ Gemini ответил", flush=True)
            return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        logging.error(f"Gemini error: {e}")
    return None

def get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    if not DEEPSEEK_API_KEY:
        return None
    print("🤖 Запрос рекомендаций к DeepSeek...", flush=True)
    try:
        prompt = build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang)
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.5}
        r = requests.post(url, headers=headers, json=body, timeout=8)
        data = r.json()
        if 'choices' in data and data['choices']:
            print("✅ DeepSeek ответил", flush=True)
            return data['choices'][0]['message']['content']
    except Exception as e:
        logging.error(f"DeepSeek error: {e}")
    return None

def get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    risk_level = pollution_analysis.get('level_code', 1)
    
    activity_map = {
        'ru': {1: "✅ Можно бегать", 2: "🏃‍♂️ Можно гулять", 3: "⚠️ Лучше в зал", 4: "⛔ Только дома", 5: "🚫 Оставайтесь дома"},
        'kk': {1: "✅ Жүгіруге болады", 2: "🏃‍♂️ Серуендеуге болады", 3: "⚠️ Залға барыңыз", 4: "⛔ Тек үйде", 5: "🚫 Үйде болыңыз"},
        'en': {1: "✅ You can run", 2: "🏃‍♂️ You can walk", 3: "⚠️ Better go to gym", 4: "⛔ Indoor only", 5: "🚫 Stay home"}
    }
    
    titles_rule = {
        'ru': {'activity': "Физическая активность", 'food': "Питание", 'water': "Питьевой режим", 'vitamins': "Витамины"},
        'kk': {'activity': "Дене белсенділігі", 'food': "Тамақтану", 'water': "Су ішу режимі", 'vitamins': "Дәрумендер"},
        'en': {'activity': "Physical Activity", 'food': "Nutrition", 'water': "Water Intake", 'vitamins': "Vitamins"}
    }
    
    tr = titles_rule.get(lang, titles_rule['ru'])
    activity = activity_map.get(lang, activity_map['ru']).get(risk_level, "✅ OK")
    
    msg = f"🏃‍♂️ **{tr['activity']}:**\n{activity}\n\n"
    msg += f"🥗 **{tr['food']}:**\n• Овощи и фрукты\n• Зелёный чай\n• Брокколи\n\n"
    msg += f"💧 **{tr['water']}:**\n• 2 литра в день\n\n"
    msg += f"💊 **{tr['vitamins']}:**\n• Витамин C\n• Омега-3\n"
    
    return msg

def get_ai_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    rec = get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    if rec: return rec
    rec = get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    if rec: return rec
    print("📋 Использую rule-based", flush=True)
    return get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)

# ==========================================
# 9. ФОРМАТИРОВАНИЕ ОТВЕТА
# ==========================================

def format_full_response(air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, lang='ru', ai_source_analysis=None):
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
    msg += f"───────────────────────\n\n"
    
    if air_data:
        msg += f"📊 **{t['air_quality']}:**\n"
        msg += f"• AQI: {air_data.get('aqi', t['no_data'])}\n"
        msg += f"• PM2.5: {air_data.get('pm25', t['no_data'])} µg/m3\n"
        msg += f"• PM10: {air_data.get('pm10', t['no_data'])} µg/m3\n"
        msg += f"• NO2: {air_data.get('no2', t['no_data'])} µg/m3\n"
        msg += f"• SO2: {air_data.get('so2', t['no_data'])} µg/m3\n"
        msg += f"{t['status']}: **{pollution_analysis['level_str']}**\n\n"
    else:
        msg += f"📊 **{t['air_quality']}:** {t['no_data']}\n\n"
    
    if weather:
        msg += f"💨 **{t['weather']}:**\n"
        msg += f"• {t['temp']}: {weather['temp']}°C\n"
        msg += f"• {t['humidity']}: {weather['humidity']}%\n"
        msg += f"• {t['wind']}: {get_wind_direction_text(weather['wind_deg'], lang)}, {weather['wind_speed']} м/с\n\n"
    
    # ИИ-анализ источников
    if ai_source_analysis:
        msg += f"{ai_source_analysis}\n\n"
    
    msg += f"───────────────────────\n"
    msg += f"{recommendations}"
    msg += f"\n\n📡 _{t['source']}: {source_name}_"
    
    return msg

def safe_send_message(chat_id, text):
    try:
        bot.send_message(chat_id, text, parse_mode='Markdown')
    except:
        try:
            bot.send_message(chat_id, text, parse_mode=None)
        except Exception as e:
            logging.error(f"Ошибка отправки: {e}")

# ==========================================
# 10. ОБРАБОТЧИКИ
# ==========================================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_ids.add(message.chat.id)
    lang_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    lang_markup.row('🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English')
    bot.send_message(
        message.chat.id,
        "Выберите язык / Тілді таңдаңыз / Choose language:",
        reply_markup=lang_markup
    )

@bot.message_handler(func=lambda m: m.text in ['🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English'])
def set_language(message):
    if 'Русский' in message.text: lang = 'ru'
    elif 'Қазақша' in message.text: lang = 'kk'
    else: lang = 'en'

    user_languages[str(message.chat.id)] = lang
    save_user_languages()

    loc_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_text = {
        'ru': "📍 Отправить локацию",
        'kk': "📍 Орынды жіберу",
        'en': "📍 Send Location"
    }.get(lang, "📍 Отправить локацию")

    btn = types.KeyboardButton(btn_text, request_location=True)
    loc_markup.add(btn)

    confirm_msg = {
        'ru': "Язык сохранен! Нажмите кнопку ниже.",
        'kk': "Тіл сақталды! Төмендегі батырманы басыңыз.",
        'en': "Language saved! Press the button below."
    }.get(lang)

    bot.send_message(message.chat.id, confirm_msg, reply_markup=loc_markup)

@bot.message_handler(content_types=['location'])
def handle_location(message):
    print("📍 Геолокация получена", flush=True)
    user_id = str(message.chat.id)
    user_ids.add(message.chat.id)
    lang = user_languages.get(user_id, 'ru')

    lat = float(message.location.latitude)
    lon = float(message.location.longitude)

    bot.send_chat_action(message.chat.id, 'typing')

    print("📊 Получаю данные о воздухе...", flush=True)
    air_data, source_name = get_best_air_data(lat, lon)
    
    print("💨 Получаю погоду...", flush=True)
    weather = get_weather(lat, lon)
    
    wind_deg = weather.get('wind_deg', 0) if weather else 0
    wind_dir_text = get_wind_direction_text(wind_deg, lang)
    
    print("🤖 ИИ анализирует источники...", flush=True)
    ai_source_analysis = get_ai_source_analysis(lat, lon, wind_deg, wind_dir_text, air_data, lang)
    
    sources = get_nearby_sources(lat, lon)
    wind_analysis = analyze_wind_and_sources(weather, sources, lat, lon)
    pollution_analysis = analyze_pollution(air_data, wind_analysis, lang)
    
    print("🤖 Запрашиваю рекомендации...", flush=True)
    recommendations = get_ai_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    
    print("📤 Формирую ответ...", flush=True)
    response = format_full_response(
        air_data, weather, wind_analysis, pollution_analysis,
        recommendations, source_name, lang, ai_source_analysis
    )
    
    print("✅ Отправляю ответ...", flush=True)
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
    acquire_pid_lock()
    load_user_languages()

    threading.Thread(target=start_health_check_server, daemon=True).start()
    threading.Thread(target=background_notifier, daemon=True).start()

    print("🚀 Бот запущен!", flush=True)

    try:
        bot.polling(none_stop=True, interval=1, timeout=30)
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Остановка бота...", flush=True)
    finally:
        release_pid_lock()
