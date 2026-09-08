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
# 2. НАСТРОЙКИ И ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WAQI_API_KEY = os.getenv("WAQI_API_KEY") or "demo"

PID_FILE = "bot.pid"
USER_LANG_FILE = "user_languages.json"

if not BOT_TOKEN:
    logging.error("❌ Ошибка: Переменная BOT_TOKEN не задана!")

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
            logging.error(f"Ошибка загрузки user_languages: {e}")

def save_user_languages():
    try:
        with open(USER_LANG_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_languages, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения user_languages: {e}")

# ==========================================
# 3. ЗАЩИТА ОТ ДУБЛИРОВАНИЯ ПРОЦЕССА (PID LOCK)
# ==========================================

def acquire_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print(f"❌ Бот уже запущен с PID {old_pid}. Завершение работы.")
            sys.exit(1)
        except (OSError, ValueError):
            pass

    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def release_pid_lock():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

# ==========================================
# 4. МАТЕМАТИКА И ГЕОДАННЫЕ
# ==========================================

def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360
    return compass_bearing

def check_wind_from_source(wind_deg, source_bearing, tolerance=25):
    diff = abs(wind_deg - source_bearing)
    if diff > 180:
        diff = 360 - diff
    return diff <= tolerance

# ==========================================
# 5. ПОЛУЧЕНИЕ ДАННЫХ ИЗ ВНЕШНИХ API
# ==========================================

def get_weather(lat, lon):
    if not WEATHER_API_KEY:
        return None
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
    try:
        r = requests.get(url, timeout=10)
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
        logging.error(f"Ошибка OpenWeatherMap: {e}")
    return None

def get_best_air_data(lat, lon):
    # Всегда пробуем WAQI с demo токеном
    try:
        token = WAQI_API_KEY or "demo"
        url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={token}"
            r = requests.get(url, timeout=10).json()
            if r.get('status') == 'ok':
                data = r['data']
                iaqi = data.get('iaqi', {})
                return {
                    'aqi': data.get('aqi'),
                    'pm25': iaqi.get('pm25', {}).get('v'),
                    'pm10': iaqi.get('pm10', {}).get('v'),
                    'no2': iaqi.get('no2', {}).get('v'),
                    'so2': iaqi.get('so2', {}).get('v'),
                    'co': iaqi.get('co', {}).get('v'),
                    'o3': iaqi.get('o3', {}).get('v')
                }, "WAQI"
        except Exception as e:
            logging.error(f"Ошибка WAQI: {e}")

    try:
        url = f"https://api.openaq.org/v2/latest?coordinates={lat},{lon}&radius=25000"
        r = requests.get(url, timeout=10).json()
        if r.get('results'):
            measurements = r['results'][0].get('measurements', [])
            res = {}
            for m in measurements:
                if m['parameter'] in ['pm25', 'pm10', 'no2', 'so2', 'co', 'o3']:
                    res[m['parameter']] = m['value']
            return res, "OpenAQ"
    except Exception as e:
        logging.error(f"Ошибка OpenAQ: {e}")

    return None, "None"

def get_nearby_sources(lat, lon):
    overpass_urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter"
    ]
    
    query = f"""
    [out:json][timeout:15];
    (
      node["landuse"="industrial"](around:7000,{lat:.6f},{lon:.6f});
      way["landuse"="industrial"](around:7000,{lat:.6f},{lon:.6f});
      node["man_made"="chimney"](around:7000,{lat:.6f},{lon:.6f});
      node["amenity"="waste_disposal"](around:7000,{lat:.6f},{lon:.6f});
      way["landuse"="landfill"](around:7000,{lat:.6f},{lon:.6f});
      way["landuse"="quarry"](around:7000,{lat:.6f},{lon:.6f});
    );
    out center;
    """

    for url in overpass_urls:
        try:
            r = requests.post(url, data={'data': query}, timeout=15)
            if r.status_code == 200:
                elements = r.json().get('elements', [])
                sources = []
                for el in elements:
                    s_lat = el.get('lat') or el.get('center', {}).get('lat')
                    s_lon = el.get('lon') or el.get('center', {}).get('lon')
                    tags = el.get('tags', {})
                    name = tags.get('name') or tags.get('landuse') or tags.get('man_made') or "Промзона/Объект"
                    if s_lat and s_lon:
                        sources.append({'name': name, 'lat': s_lat, 'lon': s_lon})
                return sources
        except Exception as e:
            logging.error(f"Ошибка Overpass API ({url}): {e}")
    return []

# ==========================================
# 6. АНАЛИТИЧЕСКИЙ ДВИЖОК
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

def analyze_pollution(air_data, wind_analysis):
    aqi = air_data.get('aqi') if air_data else None
    has_active_sources = len(wind_analysis.get('active_sources', [])) > 0

    if aqi:
        if aqi <= 50: level, level_code = "Чистый воздух", 1
        elif aqi <= 100: level, level_code = "Умеренное качество", 2
        elif aqi <= 150: level, level_code = "Вредно для чувствительных групп", 3
        elif aqi <= 200: level, level_code = "Вредный уровень", 4
        else: level, level_code = "Опасный уровень", 5
    else:
        if has_active_sources:
            level, level_code = "Повышенный риск (ветер с промзоны)", 3
        else:
            level, level_code = "Норма (косвенная оценка)", 1

    return {'level_str': level, 'level_code': level_code}

# ==========================================
# 7. РЕКОМЕНДАТЕЛЬНЫЕ ДВИЖКИ
# ==========================================

def get_wind_direction_text(deg):
    directions = [
        (0, "Северный"), (45, "Северо-восточный"), (90, "Восточный"),
        (135, "Юго-восточный"), (180, "Южный"), (225, "Юго-западный"),
        (270, "Западный"), (315, "Северо-западный")
    ]
    closest = min(directions, key=lambda x: abs(x[0] - deg))
    return closest[1]

def build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    wind_dir = get_wind_direction_text(weather['wind_deg']) if weather else 'Н/Д'
    active_names = [s['name'] for s in wind_analysis.get('active_sources', [])]
    
    prompt = f"""
Проанализируй экологическую обстановку и дай рекомендации.

ДАННЫЕ:
- AQI: {air_data.get('aqi') if air_data else 'Нет данных'}
- PM2.5: {air_data.get('pm25') if air_data else 'Нет данных'} µg/m³
- PM10: {air_data.get('pm10') if air_data else 'Нет данных'} µg/m³
- NO₂: {air_data.get('no2') if air_data else 'Нет данных'} µg/m³
- SO₂: {air_data.get('so2') if air_data else 'Нет данных'} µg/m³
- Температура: {weather.get('temp') if weather else 'Н/Д'}°C
- Влажность: {weather.get('humidity') if weather else 'Н/Д'}%
- Ветер: {wind_dir}, {weather.get('wind_speed') if weather else 'Н/Д'} м/с
- Наветренные объекты: {', '.join(active_names) if active_names else 'Не обнаружены'}

Дай рекомендации по:
1. ФИЗИЧЕСКАЯ АКТИВНОСТЬ (можно ли гулять, бегать)
2. ПИТАНИЕ (конкретные продукты)
3. ПИТЬЕВОЙ РЕЖИМ
4. ВИТАМИНЫ

Ответь на языке: {lang}
Форматируй кратко, с эмодзи и списками.
"""
    return prompt

def get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    if not GEMINI_API_KEY:
        return None
    try:
        prompt = build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        body = {"contents": [{"parts": [{"text": prompt}]}]}

        r = requests.post(url, headers=headers, json=body, timeout=15)
        data = r.json()
        
        logging.info(f"Gemini статус: {r.status_code}")
        
        if 'candidates' in data and data['candidates']:
            return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        logging.error(f"Gemini API Error: {e}")
    return None

def get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    if not DEEPSEEK_API_KEY:
        return None
    try:
        prompt = build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang)
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        body = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5
        }
        r = requests.post(url, headers=headers, json=body, timeout=15)
        data = r.json()
        if 'choices' in data and data['choices']:
            return data['choices'][0]['message']['content']
    except Exception as e:
        logging.error(f"DeepSeek API Error: {e}")
    return None

def get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    risk_level = pollution_analysis.get('level_code', 1)
    has_active_sources = len(wind_analysis.get('active_sources', [])) > 0
    wind_speed = weather.get('wind_speed', 0) if weather else 0

    templates = {
        'ru': {
            'title': "💡 **Рекомендации:**",
            'status_1': "🟢 **Воздух чистый.** Отличные условия для прогулок.",
            'status_2': "🟡 **Умеренное качество.** Воздух приемлемый.",
            'status_3': "🟠 **Вредно для чувствительных групп.** Повышена концентрация частиц.",
            'status_4': "🔴 **Вредный уровень загрязнения.** Неблагоприятная обстановка.",
            'status_5': "🟣 **Опасный уровень!** Высокий риск для здоровья.",
            'wind_threat': f"⚠️ Ветер ({wind_speed} м/с) дует со стороны промзоны.",
            'wind_clear': "🍃 Ветер дует в сторону от промышленных объектов.",
            'actions': {
                1: ["• Можно бегать и гулять.", "• Откройте окна для проветривания."],
                2: ["• Можно гулять, но без интенсивных нагрузок.", "• Проветривайте помещения."],
                3: ["• Держите окна закрытыми.", "• Включите очиститель воздуха."],
                4: ["• Не выходите без необходимости.", "• Используйте маску на улице."],
                5: ["• Оставайтесь дома.", "• Проведите влажную уборку."]
            }
        },
        'kk': {
            'title': "💡 **Ұсыныстар:**",
            'status_1': "🟢 **Ауа таза.** Серуендеуге тамаша.",
            'status_2': "🟡 **Орташа сапа.** Ауа қалыпты.",
            'status_3': "🟠 **Сезімтал топтар үшін зиянды.**",
            'status_4': "🔴 **Зиянды деңгей.**",
            'status_5': "🟣 **Өте қауіпті!**",
            'wind_threat': f"⚠️ Жел ({wind_speed} м/с) өнеркәсіп аймағынан соғып тұр.",
            'wind_clear': "🍃 Жел өнеркәсіп нысандарынан қарама-қарсы соғып тұр.",
            'actions': {
                1: ["• Серуендеуге болады.", "• Бөлмелерді желдетіңіз."],
                2: ["• Жеңіл серуендеңіз.", "• Бөлмені желдетіңіз."],
                3: ["• Терезелерді жабыңыз.", "• Ауа тазартқышты қосыңыз."],
                4: ["• Далаға шықпаңыз.", "• Маска тағыңыз."],
                5: ["• Үйде болыңыз.", "• Ылғалды тазалау жасаңыз."]
            }
        },
        'en': {
            'title': "💡 **Recommendations:**",
            'status_1': "🟢 **Clean air.** Great for outdoor activities.",
            'status_2': "🟡 **Moderate quality.** Acceptable.",
            'status_3': "🟠 **Unhealthy for sensitive groups.**",
            'status_4': "🔴 **Unhealthy level.**",
            'status_5': "🟣 **Hazardous!**",
            'wind_threat': f"⚠️ Wind ({wind_speed} m/s) blows from industrial zone.",
            'wind_clear': "🍃 Wind blows away from industrial objects.",
            'actions': {
                1: ["• Enjoy outdoor activities.", "• Open windows."],
                2: ["• Light walks are fine.", "• Ventilate rooms."],
                3: ["• Keep windows closed.", "• Use air purifier."],
                4: ["• Stay indoors.", "• Wear mask outdoors."],
                5: ["• Stay home.", "• Do wet cleaning."]
            }
        }
    }

    t = templates.get(lang, templates['ru'])
    res = [t['title'], t[f'status_{risk_level}']]

    if has_active_sources:
        res.append(t['wind_threat'])
    elif wind_analysis.get('nearby_sources_count', 0) > 0:
        res.append(t['wind_clear'])

    res.append("\n📋 **Действия:**")
    for act in t['actions'].get(risk_level, []):
        res.append(act)

    return "\n".join(res)

def get_ai_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    rec = get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    if rec: return rec

    rec = get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    if rec: return rec

    return get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)

# ==========================================
# 8. ФОРМАТИРОВАНИЕ ОТВЕТА
# ==========================================

def format_full_response(air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, lang='ru'):
    """Формируем полный ответ пользователю"""
    
    msg = f"🌍 **Экологический отчет**\n"
    msg += f"───────────────────────\n\n"
    
    # Качество воздуха
    if air_data:
        aqi = air_data.get('aqi', 'Н/Д')
        pm25 = air_data.get('pm25', 'Н/Д')
        pm10 = air_data.get('pm10', 'Н/Д')
        no2 = air_data.get('no2', 'Н/Д')
        so2 = air_data.get('so2', 'Н/Д')
        
        msg += f"📊 **Качество воздуха:**\n"
        msg += f"• AQI: {aqi}\n"
        msg += f"• PM2.5: {pm25} µg/m³\n"
        msg += f"• PM10: {pm10} µg/m³\n"
        msg += f"• NO₂: {no2} µg/m³\n"
        msg += f"• SO₂: {so2} µg/m³\n"
        msg += f"Статус: **{pollution_analysis['level_str']}**\n\n"
    else:
        msg += f"📊 **Качество воздуха:** Нет данных\n"
        msg += f"Статус: **{pollution_analysis['level_str']}**\n\n"
    
    # Погода
    if weather:
        temp = weather.get('temp', 'Н/Д')
        humidity = weather.get('humidity', 'Н/Д')
        wind_speed = weather.get('wind_speed', 'Н/Д')
        wind_dir = get_wind_direction_text(weather.get('wind_deg', 0))
        
        msg += f"💨 **Погода:**\n"
        msg += f"• Температура: {temp}°C\n"
        msg += f"• Влажность: {humidity}%\n"
        msg += f"• Ветер: {wind_dir}, {wind_speed} м/с\n\n"
    else:
        msg += f"💨 **Погода:** Нет данных\n\n"
    
    # Объекты с наветренной стороны
    active_sources = wind_analysis.get('active_sources', [])
    total_sources = wind_analysis.get('nearby_sources_count', 0)
    
    if active_sources:
        msg += f"🏭 **Объекты с наветренной стороны:**\n"
        for src in active_sources:
            msg += f"• {src['name']}\n"
        msg += "\n"
    elif total_sources > 0:
        msg += f"🏭 **Промышленных объектов рядом:** {total_sources}\n"
        msg += f"Ветер дует в сторону от объектов.\n\n"
    
    # Сопутствующие элементы
    if active_sources:
        msg += f"⚠️ **Возможные сопутствующие элементы:**\n"
        pollutants = set()
        for src in active_sources:
            name = src['name'].lower()
            if 'свалка' in name or 'landfill' in name or 'waste' in name:
                pollutants.update(['Метан', 'Сероводород', 'Аммиак'])
            elif 'тэц' in name or 'power' in name or 'электро' in name:
                pollutants.update(['Зола', 'Диоксид серы', 'Оксиды азота'])
            elif 'завод' in name or 'industrial' in name or 'пром' in name:
                pollutants.update(['Промышленная пыль', 'Летучие соединения'])
            else:
                pollutants.add('Промышленные выбросы')
        msg += ", ".join(pollutants)
        msg += "\n\n"
    
    msg += f"───────────────────────\n"
    msg += f"{recommendations}"
    msg += f"\n\n📡 _Источник: {source_name}_"
    
    return msg

def safe_send_message(chat_id, text):
    try:
        bot.send_message(chat_id, text, parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException:
        try:
            bot.send_message(chat_id, text, parse_mode=None)
        except Exception as e:
            logging.error(f"Ошибка отправки: {e}")

# ==========================================
# 9. ОБРАБОТЧИКИ ТЕЛЕГРАМ-БОТА
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
        'ru': "Язык сохранен! Нажмите кнопку ниже, чтобы проверить воздух.",
        'kk': "Тіл сақталды! Ауа сапасын тексеру үшін төмендегі батырманы басыңыз.",
        'en': "Language saved! Press the button below to check air quality."
    }.get(lang)

    bot.send_message(message.chat.id, confirm_msg, reply_markup=loc_markup)

@bot.message_handler(content_types=['location'])
def handle_location(message):
    user_id = str(message.chat.id)
    user_ids.add(message.chat.id)
    lang = user_languages.get(user_id, 'ru')

    lat = float(message.location.latitude)
    lon = float(message.location.longitude)

    bot.send_chat_action(message.chat.id, 'typing')

    air_data, source_name = get_best_air_data(lat, lon)
    weather = get_weather(lat, lon)
    sources = get_nearby_sources(lat, lon)

    wind_analysis = analyze_wind_and_sources(weather, sources, lat, lon)
    pollution_analysis = analyze_pollution(air_data, wind_analysis)

    recommendations = get_ai_recommendations(
        air_data, weather, wind_analysis, pollution_analysis, lang
    )

    response = format_full_response(
        air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, lang
    )
    safe_send_message(message.chat.id, response)

# ==========================================
# 10. ФОНОВЫЕ ЗАДАЧИ
# ==========================================

def background_notifier():
    while True:
        time.sleep(21600)  # 6 часов
        for uid in list(user_ids):
            try:
                lang = user_languages.get(str(uid), 'ru')
                remind_text = {
                    'ru': "🔔 Не забудьте обновить геолокацию, чтобы проверить качество воздуха!",
                    'kk': "🔔 Ауа сапасын тексеру үшін геолокацияны жаңартуды ұмытпаңыз!",
                    'en': "🔔 Don't forget to send your location to update air quality status!"
                }.get(lang, "🔔 Проверьте качество воздуха!")
                bot.send_message(uid, remind_text)
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления {uid}: {e}")

# ==========================================
# 11. ТОЧКА ВХОДА
# ==========================================

if __name__ == '__main__':
    acquire_pid_lock()
    load_user_languages()

    threading.Thread(target=start_health_check_server, daemon=True).start()
    threading.Thread(target=background_notifier, daemon=True).start()

    print("🚀 Бот запущен!", flush=True)

    try:
        bot.infinity_polling(timeout=20, long_polling_timeout=10)
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Остановка бота...")
    finally:
        release_pid_lock()
