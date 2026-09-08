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
    
    overpass_urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
        "https://overpass.private.coffee/api/interpreter"
    ]
    
    query = f"""
    [out:json][timeout:5];
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
            r = requests.post(url, data={'data': query}, timeout=5)
            if r.status_code == 200:
                elements = r.json().get('elements', [])
                sources = []
                for el in elements:
                    s_lat = el.get('lat') or el.get('center', {}).get('lat')
                    s_lon = el.get('lon') or el.get('center', {}).get('lon')
                    tags = el.get('tags', {})
                    name = tags.get('name') or tags.get('landuse') or tags.get('man_made') or "Промзона"
                    if s_lat and s_lon:
                        sources.append({'name': name, 'lat': s_lat, 'lon': s_lon})
                if sources:
                    print(f"✅ Найдено объектов: {len(sources)}", flush=True)
                    return sources
        except Exception as e:
            logging.error(f"Overpass error ({url}): {e}")
    
    print("❌ Объекты не найдены", flush=True)
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
# 7. ИИ РЕКОМЕНДАЦИИ
# ==========================================

def get_pollutants_for_sources(active_sources, air_data):
    pollutants = []
    
    if air_data:
        pm25 = air_data.get('pm25', 0) or 0
        pm10 = air_data.get('pm10', 0) or 0
        no2 = air_data.get('no2', 0) or 0
        so2 = air_data.get('so2', 0) or 0
        co = air_data.get('co', 0) or 0
        
        if pm25 > 35:
            pollutants.extend(['Сажа', 'Пыль', 'Тяжёлые металлы'])
        if pm10 > 60:
            pollutants.extend(['Дорожная пыль', 'Строительная пыль'])
        if no2 > 80:
            pollutants.extend(['Бенз(а)пирен', 'Угарный газ'])
        if so2 > 50:
            pollutants.extend(['Сульфаты', 'Кислотные аэрозоли'])
        if co > 5:
            pollutants.extend(['Летучие органические соединения'])
    
    for src in active_sources:
        name = src['name'].lower()
        if 'свалка' in name or 'landfill' in name or 'waste' in name:
            pollutants.extend(['Метан', 'Сероводород', 'Аммиак', 'Меркаптаны'])
        elif 'тэц' in name or 'power' in name or 'электро' in name:
            pollutants.extend(['Зола', 'Диоксид серы', 'Оксиды азота', 'Ртуть'])
        elif 'нефт' in name or 'oil' in name or 'нпз' in name:
            pollutants.extend(['Бензол', 'Толуол', 'Сероводород', 'Фенол'])
        elif 'хим' in name or 'chemical' in name:
            pollutants.extend(['Фталаты', 'Винилхлорид', 'Микропластик', 'Полимерная пыль'])
        elif 'цемент' in name or 'cement' in name:
            pollutants.extend(['Цементная пыль', 'Оксиды кальция', 'Кремниевая пыль'])
        elif 'метал' in name or 'metal' in name:
            pollutants.extend(['Тяжёлые металлы', 'Металлическая пыль'])
        else:
            pollutants.extend(['Промышленная пыль', 'Летучие соединения'])
    
    seen = set()
    result = []
    for p in pollutants:
        if p not in seen:
            seen.add(p)
            result.append(p)
    
    return result

def build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    wind_dir = get_wind_direction_text(weather['wind_deg'], lang) if weather else 'Н/Д'
    active_names = [s['name'] for s in wind_analysis.get('active_sources', [])]
    pollutants = get_pollutants_for_sources(wind_analysis.get('active_sources', []), air_data)
    
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
- Мелкие частицы (PM2.5): {air_data.get('pm25') if air_data else 'Нет данных'} µg/m3
- Крупная пыль (PM10): {air_data.get('pm10') if air_data else 'Нет данных'} µg/m3
- Диоксид азота (NO2): {air_data.get('no2') if air_data else 'Нет данных'} µg/m3
- Диоксид серы (SO2): {air_data.get('so2') if air_data else 'Нет данных'} µg/m3
- Температура: {weather.get('temp') if weather else 'Н/Д'}°C
- Влажность: {weather.get('humidity') if weather else 'Н/Д'}%
- Ветер: {wind_dir}, {weather.get('wind_speed') if weather else 'Н/Д'} м/с
- Наветренные объекты: {', '.join(active_names) if active_names else 'Не обнаружены'}
- Сопутствующие элементы: {', '.join(pollutants) if pollutants else 'Не определены'}

Дай РАЗВЕРНУТЫЕ рекомендации:

1. ФИЗИЧЕСКАЯ АКТИВНОСТЬ: можно ли гулять, бегать? Чем заменить?

2. ПИТАНИЕ: 5-7 конкретных продуктов, почему они помогают против данных загрязнителей

3. ПИТЬЕВОЙ РЕЖИМ: сколько и как часто пить

4. ВИТАМИНЫ: конкретные витамины и зачем

ВАЖНО: Отвечай ТОЛЬКО на языке: {lang_name}
Не используй другие языки. Весь ответ должен быть на {lang_name}.
"""
    return prompt

def get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    if not GEMINI_API_KEY:
        return None
    print("🤖 Запрос к Gemini...", flush=True)
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
    print("🤖 Запрос к DeepSeek...", flush=True)
    try:
        prompt = build_ai_prompt(air_data, weather, wind_analysis, pollution_analysis, lang)
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        body = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5
        }
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
    pollutants = get_pollutants_for_sources(wind_analysis.get('active_sources', []), air_data)
    
    food_map = {
        'Метан': "🥦 Брокколи, шпинат (хлорофилл связывает токсины)",
        'Сероводород': "🍵 Зелёный чай, куркума (антиоксиданты)",
        'Аммиак': "💧 Обильное питьё, лимонная вода",
        'Зола': "🍎 Яблоки, свёкла (пектин выводит тяжёлые металлы)",
        'Диоксид серы': "🥬 Капуста, редис (крестоцветные защищают бронхи)",
        'Оксиды азота': "🥕 Морковь, тыква (витамин A для слизистых)",
        'Тяжёлые металлы': "🌿 Кинза, морская капуста (альгинаты связывают металлы)",
        'Бенз(а)пирен': "🍇 Черника, виноград (ресвератрол)",
        'Сажа': "🍵 Зелёный чай, имбирь (противовоспалительное)",
        'Пыль': "💧 Обильное питьё, тёплые напитки",
        'Фталаты': "🥦 Брокколи, цветная капуста (сульфорафан)",
        'Винилхлорид': "🌿 Расторопша, кинза (поддержка печени)",
        'Микропластик': "🦪 Морская капуста, клетчатка",
        'Летучие соединения': "🍊 Цитрусовые, зелёный чай",
        'Ртуть': "🦪 Морская капуста, кинза (выводят ртуть)",
        'Бензол': "🥦 Брокколи, капуста (глюкозинолаты)",
        'Толуол': "🍵 Зелёный чай, куркума"
    }
    
    recommended_foods = []
    for p in pollutants[:5]:
        if p in food_map:
            recommended_foods.append(food_map[p])
    
    if not recommended_foods:
        recommended_foods = ["🥗 Сбалансированное питание: овощи, белки, цельные крупы"]
    
    vitamins = ["💊 Витамин C (антиоксидант)", "💊 Омега-3 (противовоспалительное)"]
    if 'Диоксид серы' in pollutants:
        vitamins.append("💊 Витамин B12")
    if 'Тяжёлые металлы' in pollutants:
        vitamins.append("💊 Цинк, селен")
    if 'Оксиды азота' in pollutants:
        vitamins.append("💊 Витамин E")
    
    activity_map = {
        'ru': {
            1: "✅ Можно бегать, гулять, тренироваться на улице",
            2: "🏃‍♂️ Можно гулять, но интенсивный бег лучше перенести в зал",
            3: "⚠️ Лучше тренироваться в помещении. На улице — маска",
            4: "⛔ Только в помещении. Окна закрыты",
            5: "🚫 Оставайтесь дома. Никаких уличных тренировок"
        },
        'kk': {
            1: "✅ Жүгіруге, серуендеуге, далада жаттығуға болады",
            2: "🏃‍♂️ Серуендеуге болады, бірақ қарқынды жүгіруді залға ауыстырған жөн",
            3: "⚠️ Үй ішінде жаттығу ұсынылады. Далада — маска",
            4: "⛔ Тек үй ішінде. Терезелер жабық",
            5: "🚫 Үйде болыңыз. Далада жаттығуға болмайды"
        },
        'en': {
            1: "✅ You can run, walk, train outdoors",
            2: "🏃‍♂️ You can walk, but intense running better move to gym",
            3: "⚠️ Better to exercise indoors. Wear mask outside",
            4: "⛔ Indoor only. Windows closed",
            5: "🚫 Stay home. No outdoor training"
        }
    }
    
    water_map = {
        'ru': {
            1: "💧 1.5-2 литра в день",
            2: "💧 2 литра в день",
            3: "💧 2-2.5 литра, каждые 30 минут по глотку",
            4: "💧 2.5-3 литра, тёплая вода",
            5: "💧 3+ литра, обязательно тёплая"
        },
        'kk': {
            1: "💧 Күніне 1.5-2 литр",
            2: "💧 Күніне 2 литр",
            3: "💧 2-2.5 литр, әр 30 минут сайын бір жұтым",
            4: "💧 2.5-3 литр, жылы су",
            5: "💧 3+ литр, міндетті түрде жылы"
        },
        'en': {
            1: "💧 1.5-2 liters per day",
            2: "💧 2 liters per day",
            3: "💧 2-2.5 liters, sip every 30 minutes",
            4: "💧 2.5-3 liters, warm water",
            5: "💧 3+ liters, must be warm"
        }
    }
    
    activity = activity_map.get(lang, activity_map['ru']).get(risk_level, "✅ OK")
    water = water_map.get(lang, water_map['ru']).get(risk_level, "💧 2L/day")
    
    titles_rule = {
        'ru': {'activity': "Физическая активность", 'food': "Питание", 'water': "Питьевой режим", 'vitamins': "Витамины"},
        'kk': {'activity': "Дене белсенділігі", 'food': "Тамақтану", 'water': "Су ішу режимі", 'vitamins': "Дәрумендер"},
        'en': {'activity': "Physical Activity", 'food': "Nutrition", 'water': "Water Intake", 'vitamins': "Vitamins"}
    }
    
    tr = titles_rule.get(lang, titles_rule['ru'])
    
    msg = f"🏃‍♂️ **{tr['activity']}:**\n{activity}\n\n"
    msg += f"🥗 **{tr['food']}:**\n"
    for food in recommended_foods:
        msg += f"{food}\n"
    msg += f"\n💧 **{tr['water']}:**\n{water}\n\n"
    msg += f"💊 **{tr['vitamins']}:**\n"
    for vit in vitamins:
        msg += f"{vit}\n"
    
    return msg

def get_ai_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    rec = get_gemini_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    if rec: return rec

    rec = get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    if rec: return rec

    print("📋 Использую rule-based рекомендации", flush=True)
    return get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)

# ==========================================
# 8. ФОРМАТИРОВАНИЕ ОТВЕТА
# ==========================================

def format_full_response(air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, lang='ru'):
    pollutant_names = {
        'ru': {
            'pm25': "Мелкие частицы (PM2.5)",
            'pm10': "Крупная пыль (PM10)",
            'no2': "Диоксид азота",
            'so2': "Диоксид серы"
        },
        'kk': {
            'pm25': "Ұсақ бөлшектер (PM2.5)",
            'pm10': "Ірі шаң (PM10)",
            'no2': "Азот диоксиді",
            'so2': "Күкірт диоксиді"
        },
        'en': {
            'pm25': "Fine particles (PM2.5)",
            'pm10': "Coarse dust (PM10)",
            'no2': "Nitrogen dioxide",
            'so2': "Sulfur dioxide"
        }
    }
    
    titles = {
        'ru': {
            'report': "Экологический отчет",
            'air_quality': "Качество воздуха",
            'status': "Статус",
            'weather': "Погода",
            'temp': "Температура",
            'humidity': "Влажность",
            'wind': "Ветер",
            'upwind': "Объекты с наветренной стороны",
            'nearby': "Промышленных объектов рядом",
            'wind_away': "Ветер дует в сторону от объектов.",
            'pollutants': "Возможные сопутствующие элементы",
            'no_data': "Нет данных",
            'source': "Источник"
        },
        'kk': {
            'report': "Экологиялық есеп",
            'air_quality': "Ауа сапасы",
            'status': "Статус",
            'weather': "Ауа райы",
            'temp': "Температура",
            'humidity': "Ылғалдылық",
            'wind': "Жел",
            'upwind': "Жел жақтағы нысандар",
            'nearby': "Жақын жердегі өнеркәсіп нысандары",
            'wind_away': "Жел нысандардан қарама-қарсы соғып тұр.",
            'pollutants': "Ықтимал қосымша элементтер",
            'no_data': "Деректер жоқ",
            'source': "Дереккөз"
        },
        'en': {
            'report': "Environmental Report",
            'air_quality': "Air Quality",
            'status': "Status",
            'weather': "Weather",
            'temp': "Temperature",
            'humidity': "Humidity",
            'wind': "Wind",
            'upwind': "Upwind Sources",
            'nearby': "Nearby industrial objects",
            'wind_away': "Wind blows away from objects.",
            'pollutants': "Possible Additional Pollutants",
            'no_data': "No data",
            'source': "Source"
        }
    }
    
    t = titles.get(lang, titles['ru'])
    pn = pollutant_names.get(lang, pollutant_names['ru'])
    
    msg = f"🌍 **{t['report']}**\n"
    msg += f"───────────────────────\n\n"
    
    if air_data:
        aqi = air_data.get('aqi', t['no_data'])
        pm25 = air_data.get('pm25', t['no_data'])
        pm10 = air_data.get('pm10', t['no_data'])
        no2 = air_data.get('no2', t['no_data'])
        so2 = air_data.get('so2', t['no_data'])
        
        msg += f"📊 **{t['air_quality']}:**\n"
        msg += f"• AQI: {aqi}\n"
        msg += f"• {pn['pm25']}: {pm25} µg/m3\n"
        msg += f"• {pn['pm10']}: {pm10} µg/m3\n"
        msg += f"• {pn['no2']}: {no2} µg/m3\n"
        msg += f"• {pn['so2']}: {so2} µg/m3\n"
        msg += f"{t['status']}: **{pollution_analysis['level_str']}**\n\n"
    else:
        msg += f"📊 **{t['air_quality']}:** {t['no_data']}\n\n"
    
    if weather:
        temp = weather.get('temp', t['no_data'])
        humidity = weather.get('humidity', t['no_data'])
        wind_speed = weather.get('wind_speed', t['no_data'])
        wind_dir = get_wind_direction_text(weather.get('wind_deg', 0), lang)
        
        msg += f"💨 **{t['weather']}:**\n"
        msg += f"• {t['temp']}: {temp}°C\n"
        msg += f"• {t['humidity']}: {humidity}%\n"
        msg += f"• {t['wind']}: {wind_dir}, {wind_speed} м/с\n\n"
    else:
        msg += f"💨 **{t['weather']}:** {t['no_data']}\n\n"
    
    active_sources = wind_analysis.get('active_sources', [])
    total_sources = wind_analysis.get('nearby_sources_count', 0)
    
    if active_sources:
        msg += f"🏭 **{t['upwind']}:**\n"
        for src in active_sources:
            msg += f"• {src['name']}\n"
        msg += "\n"
        
        pollutants = get_pollutants_for_sources(active_sources, air_data)
        if pollutants:
            msg += f"⚠️ **{t['pollutants']}:**\n"
            for p in pollutants:
                msg += f"• {p}\n"
            msg += "\n"
    elif total_sources > 0:
        msg += f"🏭 **{t['nearby']}:** {total_sources}\n"
        msg += f"{t['wind_away']}\n\n"
    
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
# 9. ОБРАБОТЧИКИ
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
    
    print("🏭 Ищу объекты...", flush=True)
    sources = get_nearby_sources(lat, lon)
    
    print("🔍 Анализирую...", flush=True)
    wind_analysis = analyze_wind_and_sources(weather, sources, lat, lon)
    pollution_analysis = analyze_pollution(air_data, wind_analysis)
    
    print("🤖 Запрашиваю ИИ...", flush=True)
    recommendations = get_ai_recommendations(
        air_data, weather, wind_analysis, pollution_analysis, lang
    )
    
    print("📤 Формирую ответ...", flush=True)
    response = format_full_response(
        air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, lang
    )
    
    print("✅ Отправляю ответ...", flush=True)
    safe_send_message(message.chat.id, response)
    print("📨 Ответ отправлен", flush=True)

# ==========================================
# 10. ФОНОВЫЕ ЗАДАЧИ
# ==========================================

def background_notifier():
    while True:
        time.sleep(21600)
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
                logging.error(f"Ошибка уведомления: {e}")

# ==========================================
# 11. ЗАПУСК
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
