import telebot
from telebot import types
import requests
import os
import math
from datetime import datetime
import threading
import time
import json
import fcntl
import sys
import signal

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)

# ============ ХРАНЕНИЕ ЯЗЫКОВ ============
user_languages = {}
user_ids = set()

# ============ ПЕРЕВОДЫ ============
TRANSLATIONS = {
    "ru": {
        "welcome": "🌍 **AirAdvice**\n\nВыберите язык:",
        "send_location": "📍 Отправить местоположение",
        "air_quality": "Качество воздуха",
        "weather_title": "Погода",
        "wind": "Ветер",
        "temp": "Температура",
        "humidity": "Влажность",
        "upwind_sources": "Объекты с наветренной стороны",
        "possible_pollutants": "Возможные сопутствующие элементы",
        "ai_error": "Не удалось получить рекомендации ИИ.",
        "updated": "Обновлено автоматически",
        "data_source": "Данные",
        "reminder": "⏰ **Проверьте качество воздуха!**\n\nПрошло 12 часов с последней проверки.\nНажмите кнопку ниже, чтобы получить свежие данные."
    },
    "kz": {
        "welcome": "🌍 **AirAdvice**\n\nТілді таңдаңыз:",
        "send_location": "📍 Орналасқан жерді жіберу",
        "air_quality": "Ауа сапасы",
        "weather_title": "Ауа райы",
        "wind": "Жел",
        "temp": "Температура",
        "humidity": "Ылғалдылық",
        "upwind_sources": "Жел жақтағы нысандар",
        "possible_pollutants": "Ықтимал қосымша элементтер",
        "ai_error": "ИИ ұсыныстарын алу мүмкін болмады.",
        "updated": "Автоматты түрде жаңартылды",
        "data_source": "Дереккөз",
        "reminder": "⏰ **Ауа сапасын тексеріңіз!**\n\nСоңғы тексеруден 12 сағат өтті.\nЖаңа деректер алу үшін төмендегі түймені басыңыз."
    },
    "en": {
        "welcome": "🌍 **AirAdvice**\n\nChoose language:",
        "send_location": "📍 Send location",
        "air_quality": "Air Quality",
        "weather_title": "Weather",
        "wind": "Wind",
        "temp": "Temperature",
        "humidity": "Humidity",
        "upwind_sources": "Upwind sources",
        "possible_pollutants": "Possible additional pollutants",
        "ai_error": "Failed to get AI recommendations.",
        "updated": "Updated automatically",
        "data_source": "Data source",
        "reminder": "⏰ **Check air quality!**\n\n12 hours have passed since your last check.\nTap the button below to get fresh air quality data."
    }
}

def get_text(user_id, key):
    lang = user_languages.get(user_id, "ru")
    return TRANSLATIONS[lang].get(key, TRANSLATIONS["ru"][key])

# ============ ЗАЩИТА ОТ ДВОЙНОГО ЗАПУСКА ============

def ensure_single_instance():
    try:
        lock_file = open('/tmp/airbot.lock', 'w')
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("🔒 Блокировка установлена", flush=True)
        return lock_file
    except IOError:
        print("❌ Бот уже запущен! Выход...", flush=True)
        sys.exit(1)
    except Exception as e:
        print(f"⚠️ Ошибка блокировки: {e}", flush=True)
        return None

# ============ ОБРАБОТЧИКИ КОМАНД ============

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
        "🌍 **AirAdvice**\n\nВыберите язык / Тілді таңдаңыз / Choose language:",
        reply_markup=markup,
        parse_mode='Markdown'
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('lang_'))
def handle_language(call):
    user_id = call.from_user.id
    lang = call.data.split('_')[1]
    user_languages[user_id] = lang
    user_ids.add(user_id)
    
    bot.answer_callback_query(call.id)
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton(get_text(user_id, "send_location"), request_location=True))
    
    bot.send_message(
        call.message.chat.id,
        get_text(user_id, "send_location"),
        reply_markup=markup,
        parse_mode='Markdown'
    )


@bot.message_handler(content_types=['location'])
def handle_location(message):
    user_id = message.from_user.id
    lat = message.location.latitude
    lon = message.location.longitude
    
    print(f"📍 Получена геолокация: {lat}, {lon}", flush=True)
    bot.send_chat_action(message.chat.id, 'typing')
    
    air_data, source_name = get_best_air_data(lat, lon)
    weather = get_weather(lat, lon)
    sources = get_nearby_sources(lat, lon)
    wind_analysis = analyze_wind_and_sources(weather, sources)
    pollution_analysis = analyze_pollution(air_data, wind_analysis)
    
    print("🔔 Вызываю get_ai_recommendations...", flush=True)
    recommendations = get_ai_recommendations(
        air_data, weather, wind_analysis, pollution_analysis
    )
    print(f"🔔 Результат ИИ: {recommendations[:50] if recommendations else 'None'}", flush=True)
    
    response = format_full_response(
        air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, user_id
    )
    
    bot.send_message(message.chat.id, response, parse_mode='Markdown')


# ============ СБОР ДАННЫХ ============

def get_air_quality_waqi(lat, lon):
    try:
        url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token=demo"
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        data = response.json()
        if data.get('status') == 'ok' and data.get('data'):
            iaqi = data['data'].get('iaqi', {})
            return {
                'pm25': iaqi.get('pm25', {}).get('v', 0),
                'pm10': iaqi.get('pm10', {}).get('v', 0),
                'no2': iaqi.get('no2', {}).get('v', 0),
                'so2': iaqi.get('so2', {}).get('v', 0),
                'co': iaqi.get('co', {}).get('v', 0),
                'o3': iaqi.get('o3', {}).get('v', 0)
            }
    except Exception as e:
        print(f"WAQI error: {e}", flush=True)
    return None


def get_air_quality_openaq(lat, lon):
    try:
        url = f"https://api.openaq.org/v2/latest?coordinates={lat},{lon}&radius=10000&limit=10"
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if data.get('results'):
            components = {'pm25': 0, 'pm10': 0, 'no2': 0, 'so2': 0, 'co': 0, 'o3': 0}
            for measurement in data['results']:
                param = measurement.get('parameter', '')
                value = measurement.get('value', 0)
                if param in components:
                    components[param] = value
            return components
    except Exception as e:
        print(f"OpenAQ error: {e}", flush=True)
    return None


def get_best_air_data(lat, lon):
    air_data = get_air_quality_waqi(lat, lon)
    if air_data and any(air_data.values()):
        return air_data, "WAQI"
    air_data = get_air_quality_openaq(lat, lon)
    if air_data and any(air_data.values()):
        return air_data, "OpenAQ"
    return None, None


def get_weather(lat, lon):
    if not WEATHER_API_KEY:
        print("❌ WEATHER_API_KEY не найден", flush=True)
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
        print(f"Weather error: {e}", flush=True)
    return None


def get_wind_direction_text(deg):
    directions = [
        (0, "Северный"), (45, "Северо-восточный"), (90, "Восточный"),
        (135, "Юго-восточный"), (180, "Южный"), (225, "Юго-западный"),
        (270, "Западный"), (315, "Северо-западный")
    ]
    closest = min(directions, key=lambda x: abs(x[0] - deg))
    return closest[1]


# ============ ПОИСК ОБЪЕКТОВ ============

def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.atan2(x, y)
    return (math.degrees(bearing) + 360) % 360


def get_nearby_sources(lat, lon):
    try:
        overpass_url = "https://overpass-api.de/api/interpreter"
        query = f"""
        [out:json];
        (
          way["landuse"="landfill"](around:7000,{lat},{lon});
          way["landuse"="industrial"](around:7000,{lat},{lon});
          way["man_made"="works"](around:7000,{lat},{lon});
          node["power"="plant"](around:7000,{lat},{lon});
        );
        out center tags;
        """
        response = requests.post(overpass_url, data=query, timeout=15)
        data = response.json()
        
        sources = []
        for element in data.get('elements', []):
            tags = element.get('tags', {})
            if 'center' in element:
                obj_lat = element['center'].get('lat', lat)
                obj_lon = element['center'].get('lon', lon)
            else:
                continue
            
            if tags.get('landuse') == 'landfill':
                src_type = 'landfill'
                name = tags.get('name', 'Свалка')
            elif tags.get('power') == 'plant':
                src_type = 'power_plant'
                name = tags.get('name', 'ТЭЦ')
            elif tags.get('landuse') == 'industrial':
                src_type = 'industrial'
                name = tags.get('name', 'Промзона')
            else:
                continue
            
            bearing = calculate_bearing(obj_lat, obj_lon, lat, lon)
            sources.append({'type': src_type, 'name': name, 'bearing': bearing})
        
        return sources[:5]
    except Exception as e:
        print(f"OSM error: {e}", flush=True)
        return []


# ============ АНАЛИЗ ============

def analyze_wind_and_sources(weather, sources):
    if not weather:
        return {
            'wind_direction_text': "Неизвестно",
            'wind_speed': 0,
            'upwind_sources': [],
            'all_sources': sources
        }
    
    wind_deg = weather['wind_deg']
    wind_speed = weather['wind_speed']
    upwind_sources = []
    
    for src in sources:
        if check_wind_from_source(wind_deg, src['bearing']):
            src_copy = src.copy()
            src_copy['wind_from'] = True
            upwind_sources.append(src_copy)
    
    return {
        'wind_direction_text': get_wind_direction_text(wind_deg),
        'wind_speed': wind_speed,
        'upwind_sources': upwind_sources,
        'all_sources': sources
    }


def check_wind_from_source(wind_deg, source_bearing):
    diff = abs(wind_deg - source_bearing)
    if diff > 180:
        diff = 360 - diff
    return diff < 45


def analyze_pollution(air_data, wind_analysis):
    if not air_data:
        return {
            'level': 'unknown', 'emoji': '⚪',
            'elevated': [], 'possible_pollutants': [],
            'pm25': 0, 'pm10': 0, 'no2': 0, 'so2': 0, 'co': 0, 'o3': 0
        }
    
    pm25 = air_data.get('pm25', 0)
    pm10 = air_data.get('pm10', 0)
    no2 = air_data.get('no2', 0)
    so2 = air_data.get('so2', 0)
    co = air_data.get('co', 0)
    o3 = air_data.get('o3', 0)
    
    if pm25 <= 15:
        level = "Чистый воздух"
        emoji = "🟢"
    elif pm25 <= 35:
        level = "Умеренное загрязнение"
        emoji = "🟡"
    elif pm25 <= 75:
        level = "Повышенное загрязнение"
        emoji = "🟠"
    else:
        level = "Опасный уровень"
        emoji = "🔴"
    
    elevated = []
    if pm25 > 35: elevated.append('PM2.5')
    if pm10 > 60: elevated.append('PM10')
    if no2 > 80: elevated.append('NO₂')
    if so2 > 50: elevated.append('SO₂')
    if co > 5: elevated.append('CO')
    if o3 > 100: elevated.append('O₃')
    
    possible_pollutants = []
    if 'PM2.5' in elevated:
        possible_pollutants.extend(['Сажа', 'Пыль', 'Тяжёлые металлы'])
    if 'NO₂' in elevated:
        possible_pollutants.extend(['Бенз(а)пирен', 'Угарный газ'])
    if 'SO₂' in elevated:
        possible_pollutants.extend(['Сульфаты', 'Кислотные аэрозоли'])
    
    for src in wind_analysis.get('upwind_sources', []):
        if src['type'] == 'landfill':
            possible_pollutants.extend(['Метан', 'Сероводород', 'Аммиак'])
        elif src['type'] == 'power_plant':
            possible_pollutants.extend(['Зола', 'Диоксид серы', 'Оксиды азота'])
        elif src['type'] == 'industrial':
            possible_pollutants.extend(['Промышленная пыль', 'Летучие соединения'])
    
    possible_pollutants = list(set(possible_pollutants))
    
    return {
        'level': level, 'emoji': emoji,
        'elevated': elevated,
        'possible_pollutants': possible_pollutants,
        'pm25': pm25, 'pm10': pm10, 'no2': no2, 'so2': so2, 'co': co, 'o3': o3
    }


# ============ ИИ РЕКОМЕНДАЦИИ ============

def get_ai_recommendations(air_data, weather, wind_analysis, pollution_analysis):
    if GEMINI_API_KEY:
        print("🔍 Пробую Gemini...", flush=True)
        result = get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis)
        if result:
            return result
    
    if DEEPSEEK_API_KEY:
        print("🔍 Пробую DeepSeek...", flush=True)
        result = get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis)
        if result:
            return result
    
    print("❌ Нет доступных ИИ ключей", flush=True)
    return None


def get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis):
    if not GEMINI_API_KEY:
        return None
    
    try:
        context = build_ai_context(air_data, weather, wind_analysis, pollution_analysis)
        
        print("📤 Отправляю запрос к Gemini...", flush=True)
        
        url = f"https://generativelanguage.googleapis.com/v1/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
        body = {"contents": [{"parts": [{"text": context}]}]}
        
        response = requests.post(url, headers=headers, json=body, timeout=15)
        print(f"📥 Статус Gemini: {response.status_code}", flush=True)
        
        data = response.json()
        print(f"📋 Ответ Gemini: {json.dumps(data, ensure_ascii=False)[:300]}", flush=True)
        
        if 'candidates' in data:
            result = data['candidates'][0]['content']['parts'][0]['text']
            print(f"✅ Ответ Gemini получен", flush=True)
            return result
        else:
            print(f"❌ Ошибка Gemini: {data}", flush=True)
    
    except Exception as e:
        print(f"❌ Gemini error: {e}", flush=True)
    
    return None


def get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis):
    if not DEEPSEEK_API_KEY:
        return None
    
    try:
        context = build_ai_context(air_data, weather, wind_analysis, pollution_analysis)
        
        print("📤 Отправляю запрос к DeepSeek...", flush=True)
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        body = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Ты — эксперт по экологии и нутрициологии."},
                {"role": "user", "content": context}
            ],
            "temperature": 0.3,
            "max_tokens": 2000
        }
        
        response = requests.post(url, headers=headers, json=body, timeout=10)
        print(f"📥 Статус DeepSeek: {response.status_code}", flush=True)
        
        data = response.json()
        print(f"📋 Ответ DeepSeek: {json.dumps(data, ensure_ascii=False)[:300]}", flush=True)
        
        if 'choices' in data:
            result = data['choices'][0]['message']['content']
            print(f"✅ Ответ DeepSeek получен", flush=True)
            return result
        else:
            print(f"❌ Ошибка DeepSeek: {data}", flush=True)
    
    except Exception as e:
        print(f"❌ DeepSeek error: {e}", flush=True)
    
    return None


def build_ai_context(air_data, weather, wind_analysis, pollution_analysis):
    context = f"""
Ты — эксперт по экологии, токсикологии и нутрициологии.

ДАННЫЕ О ВОЗДУХЕ:
- PM2.5: {pollution_analysis.get('pm25', 0)} µg/m³
- PM10: {pollution_analysis.get('pm10', 0)} µg/m³
- NO₂: {pollution_analysis.get('no2', 0)} µg/m³
- SO₂: {pollution_analysis.get('so2', 0)} µg/m³

ПОГОДА:
- Температура: {weather.get('temp', 0) if weather else 'Нет данных'}°C
- Влажность: {weather.get('humidity', 0) if weather else 'Нет данных'}%
- Ветер: {wind_analysis.get('wind_direction_text', 'Нет данных')}, {wind_analysis.get('wind_speed', 0)} м/с

ОБЪЕКТЫ С НАВЕТРЕННОЙ СТОРОНЫ:
"""
    if wind_analysis.get('upwind_sources'):
        for src in wind_analysis['upwind_sources']:
            context += f"- {src['name']} (тип: {src['type']})\n"
    else:
        context += "- Не обнаружены\n"
    
    context += f"""
ВОЗМОЖНЫЕ СОПУТСТВУЮЩИЕ ЭЛЕМЕНТЫ:
{', '.join(pollution_analysis.get('possible_pollutants', [])) if pollution_analysis.get('possible_pollutants') else 'Не определены'}

Дай рекомендации по:
1. ФИЗИЧЕСКАЯ АКТИВНОСТЬ
2. ПИТАНИЕ (конкретные продукты)
3. ПИТЬЕВОЙ РЕЖИМ
4. ВИТАМИНЫ

Учитывай конкретные загрязнители и сопутствующие элементы.
"""
    return context


# ============ ФОРМИРОВАНИЕ ОТВЕТА ============

def format_full_response(air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, user_id):
    text = f"{pollution_analysis['emoji']} **{get_text(user_id, 'air_quality')}: {pollution_analysis['level']}**\n\n"
    
    if air_data:
        text += "📊 **Показатели:**\n"
        if pollution_analysis.get('pm25', 0) > 0:
            text += f"• PM2.5: {pollution_analysis['pm25']:.1f} µg/m³\n"
        if pollution_analysis.get('pm10', 0) > 0:
            text += f"• PM10: {pollution_analysis['pm10']:.1f} µg/m³\n"
        if pollution_analysis.get('no2', 0) > 0:
            text += f"• NO₂: {pollution_analysis['no2']:.1f} µg/m³\n"
        if pollution_analysis.get('so2', 0) > 0:
            text += f"• SO₂: {pollution_analysis['so2']:.1f} µg/m³\n"
        text += "\n"
    
    if weather:
        text += f"💨 **{get_text(user_id, 'weather_title')}:**\n"
        text += f"• {get_text(user_id, 'temp')}: {weather['temp']:.0f}°C\n"
        text += f"• {get_text(user_id, 'humidity')}: {weather['humidity']}%\n"
        text += f"• {get_text(user_id, 'wind')}: {wind_analysis['wind_direction_text']}, {wind_analysis['wind_speed']} м/с\n\n"
    else:
        text += f"💨 **{get_text(user_id, 'weather_title')}:** Нет данных\n\n"
    
    if wind_analysis.get('upwind_sources'):
        text += f"🏭 **{get_text(user_id, 'upwind_sources')}:**\n"
        for src in wind_analysis['upwind_sources']:
            text += f"• {src['name']}\n"
        text += "\n"
    
    if pollution_analysis.get('possible_pollutants'):
        text += f"⚠️ **{get_text(user_id, 'possible_pollutants')}:**\n"
        text += ", ".join(pollution_analysis['possible_pollutants'])
        text += "\n\n"
    
    if recommendations:
        text += f"{recommendations}\n\n"
    else:
        text += f"⚠️ _{get_text(user_id, 'ai_error')}_\n\n"
    
    text += f"📡 {get_text(user_id, 'data_source')}: {source_name or 'Unknown'}\n"
    text += f"---\n_{get_text(user_id, 'updated')}_"
    
    return text


# ============ НАПОМИНАНИЯ ============

def send_reminders():
    while True:
        time.sleep(43200)
        for user_id in user_ids.copy():
            try:
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
                markup.add(types.KeyboardButton(get_text(user_id, "send_location"), request_location=True))
                bot.send_message(user_id, get_text(user_id, "reminder"), reply_markup=markup, parse_mode='Markdown')
            except Exception as e:
                print(f"Reminder error: {e}", flush=True)
                if "Forbidden" in str(e):
                    user_ids.discard(user_id)


# ============ ВЕБ-СЕРВЕР ============
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running')

def start_web_server():
    try:
        port = int(os.getenv('PORT', 8080))
        server = HTTPServer(('0.0.0.0', port), HealthHandler)
        print(f"🌐 Веб-сервер запущен на порту {port}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"Web server error: {e}", flush=True)


# ============ ЗАПУСК ============
if __name__ == "__main__":
    lock_file = ensure_single_instance()
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден", flush=True)
        exit(1)
    
    print("✅ Бот запущен...", flush=True)
    
    def signal_handler(sig, frame):
        print("🛑 Останавливаю бота...", flush=True)
        if lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                lock_file.close()
            except:
                pass
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        bot.delete_webhook(drop_pending_updates=True)
        print("🔄 Webhook удален, старые обновления сброшены", flush=True)
        time.sleep(5)
    except Exception as e:
        print(f"⚠️ Ошибка удаления webhook: {e}", flush=True)
        time.sleep(5)
    
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    
    reminder_thread = threading.Thread(target=send_reminders, daemon=True)
    reminder_thread.start()
    
    print("🔄 Начинаю polling...", flush=True)
    bot.skip_pending = True
    bot.polling(none_stop=True, interval=1, timeout=30)
