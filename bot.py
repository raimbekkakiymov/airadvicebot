import os
import sys
import math
import time
import json
import logging
import threading
import requests
import telebot

# ==========================================
# 1. НАСТРОЙКИ И КОНФИГУРАЦИЯ
# ==========================================

# Токены и API ключи (рекомендуется передавать через переменные окружения)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_OPENWEATHER_KEY")
WAQI_API_KEY = os.getenv("WAQI_API_KEY", "YOUR_WAQI_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "YOUR_DEEPSEEK_KEY")

PID_FILE = "bot.pid"
USER_LANG_FILE = "user_languages.json"

bot = telebot.TeleBot(TELEGRAM_TOKEN)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Хранилище языковых настроек пользователей
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
# 2. ЗАЩИТА ОТ ДУБЛИРОВАНИЯ ПРОЦЕССА (PID LOCK)
# ==========================================

def acquire_pid_lock():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            # Проверяем, существует ли еще этот процесс
            os.kill(old_pid, 0)
            print(f"❌ Бот уже запущен с PID {old_pid}. Завершение работы.")
            sys.exit(1)
        except (OSError, ValueError):
            # Процесс не найден или файл поврежден
            pass

    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def release_pid_lock():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

# ==========================================
# 3. МАТЕМАТИКА И ГЕОДАННЫЕ
# ==========================================

def calculate_bearing(lat1, lon1, lat2, lon2):
    """
    Рассчитывает азимут ОТ пользователя (lat1, lon1) К объекту (lat2, lon2)
    """
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    compass_bearing = (initial_bearing + 360) % 360
    return compass_bearing

def check_wind_from_source(wind_deg, source_bearing, tolerance=25):
    """
    Проверяет, дует ли ветер со стороны источника на пользователя.
    wind_deg: откуда дует ветер (0° = с севера).
    source_bearing: направление от человека к объекту.
    Если вектор ветра совпадает с направлением на объект (+-tolerance),
    значит выбросы несутся на человека.
    """
    diff = abs(wind_deg - source_bearing)
    if diff > 180:
        diff = 360 - diff
    return diff <= tolerance

# ==========================================
# 4. ПОЛУЧЕНИЕ ДАННЫХ ИЗ ВНЕШНИХ API
# ==========================================

def get_weather(lat, lon):
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {
                'temp': data['main']['temp'],
                'humidity': data['main']['humidity'],
                'wind_speed': data['wind']['speed'],
                'wind_deg': data['wind'].get('deg', 0)
            }
    except Exception as e:
        logging.error(f"Ошибка OpenWeatherMap: {e}")
    return None

def get_best_air_data(lat, lon):
    # 1. Попытка WAQI
    if WAQI_API_KEY:
        try:
            url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={WAQI_API_KEY}"
            r = requests.get(url, timeout=10).json()
            if r.get('status') == 'ok':
                data = r['data']
                iaqi = data.get('iaqi', {})
                return {
                    'aqi': data.get('aqi'),
                    'pm25': iaqi.get('pm25', {}).get('v'),
                    'pm10': iaqi.get('pm10', {}).get('v')
                }, "WAQI"
        except Exception as e:
            logging.error(f"Ошибка WAQI: {e}")

    # 2. Фоллбек OpenAQ
    try:
        url = f"https://api.openaq.org/v2/latest?coordinates={lat},{lon}&radius=25000"
        r = requests.get(url, timeout=10).json()
        if r.get('results'):
            measurements = r['results'][0].get('measurements', [])
            res = {}
            for m in measurements:
                if m['parameter'] in ['pm25', 'pm10']:
                    res[m['parameter']] = m['value']
            return res, "OpenAQ"
    except Exception as e:
        logging.error(f"Ошибка OpenAQ: {e}")

    return None, "None"

def get_nearby_sources(lat, lon):
    """Запрос промзон и источников выбросов через Overpass API (OSM)"""
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
# 5. АНАЛИТИЧЕСКИЙ ДВИЖОК
# ==========================================

def analyze_wind_and_sources(weather, sources, lat, lon):
    if not weather or not sources:
        return {'active_sources': [], 'nearby_sources_count': len(sources)}

    wind_deg = weather['wind_deg']
    active_sources = []

    for src in sources:
        # Корректный порядок: от человека к источнику
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
# 6. РЕКОМЕНДАТЕЛЬНЫЕ ДВИЖКИ (AI & RULE-BASED)
# ==========================================

def build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    return f"""
    Проанализируй экологическую обстановку и дай 3-4 четких, практичных совета на языке ({lang}):
    - Индекс качества воздуха (AQI): {air_data.get('aqi') if air_data else 'Нет данных'}
    - PM2.5: {air_data.get('pm25') if air_data else 'Нет данных'}
    - Температура: {weather.get('temp') if weather else 'N/A'}°C, Ветер: {weather.get('wind_speed') if weather else 'N/A'} м/с
    - Источников загрязнения наветренно: {len(wind_analysis.get('active_sources', []))}
    - Статус: {pollution_analysis.get('level_str')}
    Форматируй ответ кратко, с эмодзи и списками.
    """

def get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    if not GEMINI_API_KEY:
        return None
    try:
        prompt = build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang)
        # Исправленный эндпоинт v1beta и модель gemini-1.5-flash
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        body = {"contents": [{"parts": [{"text": prompt}]}]}

        r = requests.post(url, headers=headers, json=body, timeout=12)
        data = r.json()
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
        r = requests.post(url, headers=headers, json=body, timeout=12)
        data = r.json()
        if 'choices' in data and data['choices']:
            return data['choices'][0]['message']['content']
    except Exception as e:
        logging.error(f"DeepSeek API Error: {e}")
    return None

def get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    """Локальный генератор правил (Fallback)"""
    risk_level = pollution_analysis.get('level_code', 1)
    has_active_sources = len(wind_analysis.get('active_sources', [])) > 0
    wind_speed = weather.get('wind_speed', 0) if weather else 0

    templates = {
        'ru': {
            'title': "💡 **Рекомендации по безопасности:**",
            'status_1': "🟢 **Воздух чистый.** Отличные условия для прогулок и проветривания.",
            'status_2': "🟡 **Умеренное качество.** Воздух приемлемый, чувствительным людям соблюдать осторожность.",
            'status_3': "🟠 **Вредно для чувствительных групп.** Повышена концентрация пыли/частиц.",
            'status_4': "🔴 **Вредный уровень загрязнения.** Неблагоприятная экологическая обстановка.",
            'status_5': "🟣 **Опасный уровень!** Высокий риск для здоровья всех граждан.",
            'wind_threat': f"⚠️ **Внимание:** Ветер ({wind_speed} м/с) дует со стороны промзоны. Запах и смог могут усилиться.",
            'wind_clear': "🍃 Ветер дует в сторону от промышленных объектов.",
            'actions_title': "📋 **Рекомендуемые действия:**",
            'sensitive_title': "⚠️ **Для чувствительных групп (дети, астматики, пожилые):**",
            'actions': {
                1: ["• Наслаждайтесь активностями на открытом воздухе.", "• Откройте окна для проветривания."],
                2: ["• Проветривайте помещения.", "• Сократите интенсивные нагрузки на улице при дискомфорте."],
                3: ["• Держите окна закрытыми с наветренной стороны.", "• Включите очиститель воздуха при наличии."],
                4: ["• Закройте окна; используйте рециркуляцию воздуха.", "• На улице используйте респиратор FFP2/N95."],
                5: ["• Не выходите на улицу без крайней необходимости.", "• Проведите влажную уборку дома."]
            },
            'sensitive': {
                1: "• Ограничений нет.",
                2: "• Держите при себе необходимые медикаменты/ингаляторы.",
                3: "• Сократите прогулки вдоль автодорог.",
                4: "• Отмените outdoor-активность. Оставайтесь в помещении.",
                5: "• Строгий режим пребывания в помещении. При ухудшении состояния — к врачу."
            }
        },
        'kk': {
            'title': "💡 **Қауіпсіздік ұсыныстары:**",
            'status_1': "🟢 **Ауа таза.** Серуендеуге және үйді желдетуге өте қолайлы.",
            'status_2': "🟡 **Орташа сапа.** Ауа деңгейі қалыпты.",
            'status_3': "🟠 **Сезімтал топтар үшін зиянды.**",
            'status_4': "🔴 **Зиянды деңгей.** Денсаулыққа жағымсыз жағдай.",
            'status_5': "🟣 **Өте қауіпті деңгей!** Жоғары қауіп бар.",
            'wind_threat': f"⚠️ **Назар аударыңыз:** Жел ({wind_speed} м/с) өнеркәсіп аймағынан соғып тұр.",
            'wind_clear': "🍃 Жел өнеркәсіптік нысандардан қарама-қарсы бағытта соғып тұр.",
            'actions_title': "📋 **Не істеу ұсынылады:**",
            'sensitive_title': "⚠️ **Сезімтал топтар үшін:**",
            'actions': {
                1: ["• Ашық ауада серуендеңіз.", "• Бөлмелерді желдетіңіз."],
                2: ["• Бөлмені желдетуге болады.", "• Жайсыздық сезілсе, жаттығуларды азайтыңыз."],
                3: ["• Терезелерді жабық ұстаңыз.", "• Ауа тазартқышты қосыңыз."],
                4: ["• Терезелерді тығыз жабыңыз.", "• Далаға шығарда FFP2 маскасын тағыңыз."],
                5: ["• Далаға шығуды барынша шектеңіз.", "• Көбірек таза су ішіңіз."]
            },
            'sensitive': {
                1: "• Шектеулер жоқ.",
                2: "• Ингаляторларды өзіңізбен ұстаңыз.",
                3: "• Серуендеуді азайтыңыз.",
                4: "• Сырттағы белсенділіктен бас тартыңыз.",
                5: "• Үйде болыңыз. Денсаулық нашарласа, дәрігерге көрініңіз."
            }
        },
        'en': {
            'title': "💡 **Safety Recommendations:**",
            'status_1': "🟢 **Good Air Quality.** Great conditions for outdoor activity.",
            'status_2': "🟡 **Moderate Quality.** Acceptable air condition.",
            'status_3': "🟠 **Unhealthy for Sensitive Groups.**",
            'status_4': "🔴 **Unhealthy Level.** Adverse conditions.",
            'status_5': "🟣 **Hazardous Level!** Serious health risk.",
            'wind_threat': f"⚠️ **Warning:** Wind ({wind_speed} m/s) is blowing from industrial zones toward you.",
            'wind_clear': "🍃 Wind is blowing away from industrial facilities.",
            'actions_title': "📋 **Recommended Actions:**",
            'sensitive_title': "⚠️ **For Sensitive Groups:**",
            'actions': {
                1: ["• Enjoy outdoor activities.", "• Open windows for ventilation."],
                2: ["• Ventilate indoors.", "• Limit prolonged outdoor exertion if uncomfortable."],
                3: ["• Keep windows closed.", "• Use HEPA air purifiers if available."],
                4: ["• Keep windows sealed.", "• Wear FFP2/N95 respirator outdoors."],
                5: ["• Stay indoors.", "• Perform wet cleaning indoors."]
            },
            'sensitive': {
                1: "• No restrictions.",
                2: "• Carry required medical inhalers.",
                3: "• Reduce walks near heavy traffic.",
                4: "• Avoid outdoor activities.",
                5: "• Stay strictly indoors. Consult a doctor if feeling unwell."
            }
        }
    }

    t = templates.get(lang, templates['ru'])
    res = [t['title'], t[f'status_{risk_level}']]

    if has_active_sources:
        res.append(t['wind_threat'])
    elif wind_analysis.get('nearby_sources_count', 0) > 0:
        res.append(t['wind_clear'])

    res.append(f"\n{t['actions_title']}")
    for act in t['actions'].get(risk_level, []):
        res.append(act)

    res.append(f"\n{t['sensitive_title']}")
    res.append(t['sensitive'].get(risk_level, ""))

    return "\n".join(res)

def get_ai_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    # 1. Попытка Gemini
    rec = get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    if rec: return rec

    # 2. Попытка DeepSeek
    rec = get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    if rec: return rec

    # 3. Локальный Fallback
    return get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)

# ==========================================
# 7. ФОРМАТИРОВАНИЕ И ОТПРАВКА ОТВЕТА
# ==========================================

def format_full_response(air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, lang='ru'):
    aqi_str = air_data.get('aqi', 'N/A') if air_data else 'Н/Д'
    pm25_str = air_data.get('pm25', 'N/A') if air_data else 'Н/Д'
    temp_str = f"{weather['temp']}°C" if weather else 'N/A'
    wind_str = f"{weather['wind_speed']} м/с" if weather else 'N/A'

    active_cnt = len(wind_analysis.get('active_sources', []))
    total_cnt = wind_analysis.get('nearby_sources_count', 0)

    msg = f"🌍 **Экологический отчет** (Источник данных: {source_name})\n"
    msg += f"───────────────────────\n"
    msg += f"📊 **Индекс AQI:** {aqi_str} | **PM2.5:** {pm25_str}\n"
    msg += f"🌡 **Температура:** {temp_str} | 💨 **Ветер:** {wind_str}\n"
    msg += f"🏭 **Наветренные промзоны:** {active_cnt} из {total_cnt} рядом\n"
    msg += f"Статус: **{pollution_analysis['level_str']}**\n"
    msg += f"───────────────────────\n\n"
    msg += f"{recommendations}"

    return msg

def safe_send_message(chat_id, text):
    """Безопасная отправка сообщения с защитой от ошибок Telegram Markdown"""
    try:
        bot.send_message(chat_id, text, parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException:
        # Если ИИ сломал разметку — отправляем как чистый текст
        bot.send_message(chat_id, text, parse_mode=None)

# ==========================================
# 8. ОБРАБОТЧИКИ ТЕЛЕГРАМ-БОТА
# ==========================================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_ids.add(message.chat.id)
    lang_markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
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

    loc_markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_text = {
        'ru': "📍 Отправить локацию",
        'kk': "📍 Орынды жіберу",
        'en': "📍 Send Location"
    }.get(lang, "📍 Отправить локацию")

    btn = telebot.types.KeyboardButton(btn_text, request_location=True)
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

    # Сбор данных
    air_data, source_name = get_best_air_data(lat, lon)
    weather = get_weather(lat, lon)
    sources = get_nearby_sources(lat, lon)

    # Аналитика
    wind_analysis = analyze_wind_and_sources(weather, sources, lat, lon)
    pollution_analysis = analyze_pollution(air_data, wind_analysis)

    # Получение рекомендаций (AI -> Fallback)
    recommendations = get_ai_recommendations(
        air_data, weather, wind_analysis, pollution_analysis, lang
    )

    # Форматирование и безопасная отправка
    response = format_full_response(
        air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, lang
    )
    safe_send_message(message.chat.id, response)

# ==========================================
# 9. ФОНОВЫЕ ЗАДАЧИ (РАССЫЛКА НАПОМИНАНИЙ)
# ==========================================

def background_notifier():
    """Фоновый поток для напоминаний раз в 6 часов"""
    while True:
        time.sleep(21600)  # 6 часов
        for uid in list(user_ids):
            try:
                lang = user_languages.get(str(uid), 'ru')
                remind_text = {
                    'ru': "🔔 Не забудьте обновить геолокацию, чтобы проверить текущее качество воздуха!",
                    'kk': "🔔 Ағымдағы ауа сапасын тексеру үшін геолокацияны жаңартуды ұмытпаңыз!",
                    'en': "🔔 Don't forget to send your location to update air quality status!"
                }.get(lang, "🔔 Проверьте качество воздуха!")
                bot.send_message(uid, remind_text)
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления {uid}: {e}")

# ==========================================
# 10. ТОЧКА ВХОДА И ЗАПУСК
# ==========================================

if __name__ == '__main__':
    acquire_pid_lock()
    load_user_languages()

    # Запуск фонового потока
    notifier_thread = threading.Thread(target=background_notifier, daemon=True)
    notifier_thread.start()

    print("🚀 Эко-бот успешно запущен!", flush=True)

    try:
        bot.infinity_polling(timeout=20, long_polling_timeout=10)
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Остановка бота...")
    finally:
        release_pid_lock()
