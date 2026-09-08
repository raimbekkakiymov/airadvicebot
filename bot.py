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
from datetime import datetime, timedelta
from collections import defaultdict
from functools import lru_cache
import asyncio
import aiohttp
from flask import Flask, request, jsonify

# Попытка импорта psycopg2 (опционально)
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    print("⚠️ psycopg2 не установлен, использую JSON файл")

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WAQI_API_KEY = os.getenv("WAQI_API_KEY", "demo")
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL (опционально)
PORT = int(os.getenv("PORT", 8080))

# Константы
VERSION = "3.0.0"
CACHE_TIMEOUT = 600  # 10 минут
RATE_LIMIT = 10  # запросов в минуту
USER_DATA_FILE = "user_data.json"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = telebot.TeleBot(BOT_TOKEN if BOT_TOKEN else "DUMMY_TOKEN", threaded=False)
app = Flask(__name__)

# ==========================================
# 2. ХРАНИЛИЩЕ ДАННЫХ (JSON вместо PostgreSQL)
# ==========================================

class DataStore:
    def __init__(self):
        self.users = {}
        self.air_history = {}
        self.stats = {}
        self.lock = threading.Lock()
        self.load_data()
    
    def load_data(self):
        """Загрузка данных из файла"""
        try:
            if os.path.exists(USER_DATA_FILE):
                with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.users = data.get('users', {})
                    self.air_history = data.get('air_history', {})
                    self.stats = data.get('stats', {})
                logger.info(f"✅ Загружено {len(self.users)} пользователей")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки данных: {e}")
    
    def save_data(self):
        """Сохранение данных в файл"""
        try:
            with self.lock:
                data = {
                    'users': self.users,
                    'air_history': self.air_history,
                    'stats': self.stats
                }
                with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения данных: {e}")
    
    def save_user(self, user_id, username, first_name, language='ru'):
        """Сохранение пользователя"""
        self.users[str(user_id)] = {
            'user_id': user_id,
            'username': username,
            'first_name': first_name,
            'language': language,
            'latitude': None,
            'longitude': None,
            'created_at': datetime.now().isoformat(),
            'last_active': datetime.now().isoformat()
        }
        self.save_data()
    
    def update_user_location(self, user_id, lat, lon):
        """Обновление локации пользователя"""
        user_id_str = str(user_id)
        if user_id_str in self.users:
            self.users[user_id_str]['latitude'] = lat
            self.users[user_id_str]['longitude'] = lon
            self.users[user_id_str]['last_active'] = datetime.now().isoformat()
            self.save_data()
    
    def update_user_language(self, user_id, language):
        """Обновление языка пользователя"""
        user_id_str = str(user_id)
        if user_id_str in self.users:
            self.users[user_id_str]['language'] = language
            self.users[user_id_str]['last_active'] = datetime.now().isoformat()
            self.save_data()
    
    def get_user(self, user_id):
        """Получение пользователя"""
        return self.users.get(str(user_id))
    
    def get_all_users(self):
        """Получение всех активных пользователей"""
        active_users = []
        now = datetime.now()
        for user_id, user_data in self.users.items():
            try:
                last_active = datetime.fromisoformat(user_data.get('last_active', ''))
                if (now - last_active).days < 30:
                    active_users.append(user_data)
            except:
                active_users.append(user_data)
        return active_users
    
    def save_air_quality(self, user_id, lat, lon, air_data, source):
        """Сохранение данных о качестве воздуха"""
        user_id_str = str(user_id)
        if user_id_str not in self.air_history:
            self.air_history[user_id_str] = []
        
        self.air_history[user_id_str].append({
            'latitude': lat,
            'longitude': lon,
            'air_data': air_data,
            'source': source,
            'created_at': datetime.now().isoformat()
        })
        
        # Ограничиваем историю последними 100 записями
        if len(self.air_history[user_id_str]) > 100:
            self.air_history[user_id_str] = self.air_history[user_id_str][-100:]
        
        self.save_data()
    
    def get_air_history(self, user_id, hours=24):
        """Получение истории качества воздуха"""
        user_id_str = str(user_id)
        history = self.air_history.get(user_id_str, [])
        
        if not history:
            return []
        
        # Фильтруем по времени
        cutoff = datetime.now() - timedelta(hours=hours)
        filtered = []
        for record in history:
            try:
                created_at = datetime.fromisoformat(record['created_at'])
                if created_at >= cutoff:
                    filtered.append(record)
            except:
                filtered.append(record)
        
        return filtered
    
    def update_stats(self, user_id):
        """Обновление статистики пользователя"""
        user_id_str = str(user_id)
        if user_id_str not in self.stats:
            self.stats[user_id_str] = {
                'total_requests': 0,
                'daily_requests': 0,
                'request_date': datetime.now().date().isoformat(),
                'last_request_time': None
            }
        
        self.stats[user_id_str]['total_requests'] += 1
        
        today = datetime.now().date().isoformat()
        if self.stats[user_id_str]['request_date'] != today:
            self.stats[user_id_str]['daily_requests'] = 1
            self.stats[user_id_str]['request_date'] = today
        else:
            self.stats[user_id_str]['daily_requests'] += 1
        
        self.stats[user_id_str]['last_request_time'] = datetime.now().isoformat()
        self.save_data()

# Инициализация хранилища
store = DataStore()

# ==========================================
# 3. КЭШИРОВАНИЕ
# ==========================================

class Cache:
    def __init__(self):
        self.cache = {}
        self.lock = threading.Lock()
    
    def get(self, key):
        with self.lock:
            if key in self.cache:
                data, timestamp = self.cache[key]
                if time.time() - timestamp < CACHE_TIMEOUT:
                    return data
                else:
                    del self.cache[key]
            return None
    
    def set(self, key, value):
        with self.lock:
            self.cache[key] = (value, time.time())
    
    def clear(self):
        with self.lock:
            self.cache.clear()

cache = Cache()

# ==========================================
# 4. RATE LIMITING
# ==========================================

class RateLimiter:
    def __init__(self):
        self.requests = defaultdict(list)
        self.lock = threading.Lock()
    
    def is_allowed(self, user_id):
        with self.lock:
            now = time.time()
            # Удаляем старые запросы
            self.requests[user_id] = [t for t in self.requests[user_id] if now - t < 60]
            
            if len(self.requests[user_id]) >= RATE_LIMIT:
                return False
            
            self.requests[user_id].append(now)
            return True

rate_limiter = RateLimiter()

# ==========================================
# 5. МАТЕМАТИЧЕСКИЕ ФУНКЦИИ
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
# 6. API ФУНКЦИИ
# ==========================================

def get_weather(lat, lon):
    if not WEATHER_API_KEY:
        return None
    
    cache_key = f"weather_{round(lat, 2)}_{round(lon, 2)}"
    cached = cache.get(cache_key)
    if cached:
        logger.info("📦 Погода из кэша")
        return cached
    
    logger.info("💨 Запрос погоды...")
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json()
            result = {
                'temp': data['main']['temp'],
                'humidity': data['main']['humidity'],
                'wind_speed': data['wind']['speed'],
                'wind_deg': data['wind'].get('deg', 0),
                'description': data['weather'][0]['description'],
                'pressure': data['main'].get('pressure'),
                'visibility': data.get('visibility')
            }
            cache.set(cache_key, result)
            return result
    except Exception as e:
        logger.error(f"OpenWeatherMap error: {e}")
    return None

def get_best_air_data(lat, lon):
    cache_key = f"air_{round(lat, 2)}_{round(lon, 2)}"
    cached = cache.get(cache_key)
    if cached:
        logger.info("📦 Данные о воздухе из кэша")
        return cached
    
    logger.info("📊 Запрос качества воздуха...")
    
    # Пробуем WAQI
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
                logger.info(f"✅ WAQI: AQI={result.get('aqi')}")
                cache.set(cache_key, (result, "WAQI"))
                return result, "WAQI"
    except Exception as e:
        logger.error(f"WAQI error: {e}")
    
    # Пробуем OpenAQ
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
                logger.info(f"✅ OpenAQ: {components}")
                cache.set(cache_key, (components, "OpenAQ"))
                return components, "OpenAQ"
    except Exception as e:
        logger.error(f"OpenAQ error: {e}")
    
    logger.info("❌ Нет данных о воздухе")
    return None, "None"

def calculate_aqi_from_pm25(pm25):
    """Расчет AQI на основе PM2.5"""
    if pm25 <= 12:
        return round((50 / 12) * pm25)
    elif pm25 <= 35.4:
        return round(((100 - 51) / (35.4 - 12.1)) * (pm25 - 12.1) + 51)
    elif pm25 <= 55.4:
        return round(((150 - 101) / (55.4 - 35.5)) * (pm25 - 35.5) + 101)
    elif pm25 <= 150.4:
        return round(((200 - 151) / (150.4 - 55.5)) * (pm25 - 55.5) + 151)
    elif pm25 <= 250.4:
        return round(((300 - 201) / (250.4 - 150.5)) * (pm25 - 150.5) + 201)
    else:
        return 300

def get_nearby_sources(lat, lon):
    """Получение ближайших промышленных источников"""
    # Здесь можно интегрировать OpenStreetMap или другую базу
    # Для примера возвращаем пустой список
    return []

# ==========================================
# 7. АНАЛИЗ ДАННЫХ
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
                'bearing': round(bearing, 1),
                'distance': src.get('distance', 'Н/Д')
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
            1: "Чистый воздух", 
            2: "Умеренное качество", 
            3: "Вредно для чувствительных групп",
            4: "Вредный уровень", 
            5: "Очень вредный", 
            6: "Опасный уровень",
            'risk': "Повышенный риск (ветер с промзоны)", 
            'normal': "Норма (косвенная оценка)"
        },
        'kk': {
            1: "Таза ауа", 
            2: "Орташа сапа", 
            3: "Сезімтал топтар үшін зиянды",
            4: "Зиянды деңгей", 
            5: "Өте зиянды", 
            6: "Қауіпті деңгей",
            'risk': "Жоғары қауіп (өнеркәсіп аймағынан жел)", 
            'normal': "Қалыпты (жанама бағалау)"
        },
        'en': {
            1: "Clean air", 
            2: "Moderate quality", 
            3: "Unhealthy for sensitive groups",
            4: "Unhealthy level", 
            5: "Very unhealthy", 
            6: "Hazardous level",
            'risk': "Increased risk (wind from industrial zone)", 
            'normal': "Normal (indirect assessment)"
        }
    }
    
    t = levels.get(lang, levels['ru'])
    
    if aqi:
        if aqi <= 50: level, level_code = t[1], 1
        elif aqi <= 100: level, level_code = t[2], 2
        elif aqi <= 150: level, level_code = t[3], 3
        elif aqi <= 200: level, level_code = t[4], 4
        elif aqi <= 300: level, level_code = t[5], 5
        else: level, level_code = t[6], 6
    else:
        if has_active_sources:
            level, level_code = t['risk'], 3
        else:
            level, level_code = t['normal'], 1

    return {'level_str': level, 'level_code': level_code}

# ==========================================
# 8. ИИ АНАЛИЗ (Gemini)
# ==========================================

def get_ai_source_analysis(lat, lon, wind_deg, wind_dir_text, air_data, lang='ru'):
    if not GEMINI_API_KEY:
        return None
    
    lang_names = {
        'ru': 'Русский',
        'kk': 'Казахский (Қазақша)',
        'en': 'English'
    }
    lang_name = lang_names.get(lang, 'Русский')
    
    try:
        prompt = f"""Ты — эксперт по экологии и промышленной безопасности.

ПОЛЬЗОВАТЕЛЬ НАХОДИТСЯ:
- Координаты: {lat}, {lon}
- Ветер дует с: {wind_dir_text} (градус: {wind_deg}°)

ТЕКУЩИЕ ПОКАЗАТЕЛИ ВОЗДУХА:
- AQI: {air_data.get('aqi', 'Нет данных') if air_data else 'Нет данных'}
- Диоксид серы (SO2): {air_data.get('so2', 'Нет данных') if air_data else 'Нет данных'} µg/m3
- Диоксид азота (NO2): {air_data.get('no2', 'Нет данных') if air_data else 'Нет данных'} µg/m3

ВАЖНО: Используй свои знания о географии. Даже если не знаешь точное название объекта, предположи, что может находиться в этом направлении (НПЗ, ТЭЦ, свалка, химзавод и т.д.) и какие элементы они выделяют.

ФОРМАТ ОТВЕТА (обязательно):
🏭 Вероятные источники:
• [Название] — [что выделяет]

⚠️ Сопутствующие элементы:
• [Элемент] — [опасность]

Ответь на языке: {lang_name}"""
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        body = {
            "systemInstruction": {
                "parts": [{"text": f"Ты отвечаешь ТОЛЬКО на языке: {lang_name}."}]
            },
            "contents": [{"parts": [{"text": prompt}]}]
        }
        
        r = requests.post(url, headers=headers, json=body, timeout=10)
        data = r.json()
        
        if 'candidates' in data and data['candidates']:
            result = data['candidates'][0]['content']['parts'][0]['text']
            logger.info("✅ ИИ определил источники")
            return result
    
    except Exception as e:
        logger.error(f"ИИ анализ источников: {e}")
    
    return None

def get_ai_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    if not GEMINI_API_KEY:
        return get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)
    
    try:
        wind_dir = get_wind_direction_text(weather['wind_deg'], lang) if weather else 'Н/Д'
        
        lang_names = {
            'ru': 'Русский',
            'kk': 'Казахский (Қазақша)',
            'en': 'English'
        }
        lang_name = lang_names.get(lang, 'Русский')
        
        prompt = f"""Ты — эксперт по экологии, токсикологии и нутрициологии.

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

КРИТИЧЕСКИ ВАЖНО: Отвечай ТОЛЬКО на {lang_name}. Названия продуктов пиши на {lang_name}."""
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        body = {
            "systemInstruction": {
                "parts": [{"text": f"Отвечай только на {lang_name}"}]
            },
            "contents": [{"parts": [{"text": prompt}]}]
        }

        r = requests.post(url, headers=headers, json=body, timeout=8)
        data = r.json()
        
        if 'candidates' in data and data['candidates']:
            logger.info("✅ Gemini ответил")
            return data['candidates'][0]['content']['parts'][0]['text']
    
    except Exception as e:
        logger.error(f"Gemini error: {e}")
    
    return get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang)

def get_rule_based_recommendations(air_data, weather, wind_analysis, pollution_analysis, lang='ru'):
    risk_level = pollution_analysis.get('level_code', 1)
    
    activity_map = {
        'ru': {
            1: "✅ Можно бегать", 
            2: "🏃‍♂️ Можно гулять", 
            3: "⚠️ Лучше в зал", 
            4: "⛔ Только дома", 
            5: "🚫 Оставайтесь дома",
            6: "🚫 Оставайтесь дома"
        },
        'kk': {
            1: "✅ Жүгіруге болады", 
            2: "🏃‍♂️ Серуендеуге болады", 
            3: "⚠️ Залға барыңыз", 
            4: "⛔ Тек үйде", 
            5: "🚫 Үйде болыңыз",
            6: "🚫 Үйде болыңыз"
        },
        'en': {
            1: "✅ You can run", 
            2: "🏃‍♂️ You can walk", 
            3: "⚠️ Better go to gym", 
            4: "⛔ Indoor only", 
            5: "🚫 Stay home",
            6: "🚫 Stay home"
        }
    }
    
    titles_rule = {
        'ru': {'activity': "Физическая активность", 'food': "Питание", 'water': "Питьевой режим", 'vitamins': "Витамины"},
        'kk': {'activity': "Дене белсенділігі", 'food': "Тамақтану", 'water': "Су ішу режимі", 'vitamins': "Дәрумендер"},
        'en': {'activity': "Physical Activity", 'food': "Nutrition", 'water': "Water Intake", 'vitamins': "Vitamins"}
    }
    
    tr = titles_rule.get(lang, titles_rule['ru'])
    activity = activity_map.get(lang, activity_map['ru']).get(risk_level, "✅ OK")
    
    foods = {
        'ru': ["Овощи и фрукты", "Зелёный чай", "Брокколи", "Гранаты", "Куркума"],
        'kk': ["Көкөністер мен жемістер", "Көк шай", "Брокколи", "Анар", "Куркума"],
        'en': ["Vegetables and fruits", "Green tea", "Broccoli", "Pomegranates", "Turmeric"]
    }
    
    msg = f"🏃‍♂️ **{tr['activity']}:**\n{activity}\n\n"
    msg += f"🥗 **{tr['food']}:**\n"
    for food in foods.get(lang, foods['ru']):
        msg += f"• {food}\n"
    msg += f"\n💧 **{tr['water']}:**\n• 2-2.5 литра в день\n\n"
    msg += f"💊 **{tr['vitamins']}:**\n• Витамин C\n• Витамин E\n• Омега-3\n"
    
    return msg

# ==========================================
# 9. ФОРМАТИРОВАНИЕ ОТВЕТА
# ==========================================

def format_full_response(air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name, lang='ru', ai_source_analysis=None):
    titles = {
        'ru': {
            'report': "Экологический отчет", 
            'air_quality': "Качество воздуха", 
            'status': "Статус",
            'weather': "Погода", 
            'temp': "Температура", 
            'humidity': "Влажность", 
            'wind': "Ветер",
            'pressure': "Давление",
            'visibility': "Видимость",
            'no_data': "Нет данных", 
            'source': "Источник",
            'updated': "Обновлено"
        },
        'kk': {
            'report': "Экологиялық есеп", 
            'air_quality': "Ауа сапасы", 
            'status': "Статус",
            'weather': "Ауа райы", 
            'temp': "Температура", 
            'humidity': "Ылғалдылық", 
            'wind': "Жел",
            'pressure': "Қысым",
            'visibility': "Көріну",
            'no_data': "Деректер жоқ", 
            'source': "Дереккөз",
            'updated': "Жаңартылды"
        },
        'en': {
            'report': "Environmental Report", 
            'air_quality': "Air Quality", 
            'status': "Status",
            'weather': "Weather", 
            'temp': "Temperature", 
            'humidity': "Humidity", 
            'wind': "Wind",
            'pressure': "Pressure",
            'visibility': "Visibility",
            'no_data': "No data", 
            'source': "Source",
            'updated': "Updated"
        }
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
        msg += f"{t['status']}: **{pollution_analysis['level_str']}**\n\n"
    else:
        msg += f"📊 **{t['air_quality']}:** {t['no_data']}\n\n"
    
    if weather:
        msg += f"💨 **{t['weather']}:**\n"
        msg += f"• {t['temp']}: {weather['temp']}°C\n"
        msg += f"• {t['humidity']}: {weather['humidity']}%\n"
        msg += f"• {t['wind']}: {get_wind_direction_text(weather['wind_deg'], lang)}, {weather['wind_speed']} м/с\n"
        if 'pressure' in weather:
            msg += f"• {t['pressure']}: {weather['pressure']} hPa\n"
        msg += "\n"
    
    if ai_source_analysis:
        msg += f"{ai_source_analysis}\n\n"
    
    msg += "───────────────────────\n"
    msg += f"{recommendations}"
    msg += f"\n\n📡 _{t['source']}: {source_name}_"
    msg += f"\n🕐 {datetime.now().strftime('%H:%M')}"
    
    return msg

def safe_send_message(chat_id, text):
    try:
        bot.send_message(chat_id, text, parse_mode='Markdown')
    except:
        try:
            bot.send_message(chat_id, text, parse_mode=None)
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")

# ==========================================
# 10. ОБРАБОТЧИКИ КОМАНД
# ==========================================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user = message.from_user
    store.save_user(user.id, user.username, user.first_name)
    
    lang_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    lang_markup.row('🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English')
    bot.send_message(
        message.chat.id,
        "Выберите язык / Тілді таңдаңыз / Choose language:",
        reply_markup=lang_markup
    )

@bot.message_handler(func=lambda m: m.text in ['🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English'])
def set_language(message):
    user = message.from_user
    
    if 'Русский' in message.text: lang = 'ru'
    elif 'Қазақша' in message.text: lang = 'kk'
    else: lang = 'en'

    store.update_user_language(user.id, lang)

    loc_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_text = {
        'ru': "📍 Отправить локацию",
        'kk': "📍 Орынды жіберу",
        'en': "📍 Send Location"
    }.get(lang, "📍 Отправить локацию")

    btn = types.KeyboardButton(btn_text, request_location=True)
    loc_markup.add(btn)

    confirm_msg = {
        'ru': "Язык сохранен! Нажмите кнопку ниже, чтобы отправить вашу геолокацию.",
        'kk': "Тіл сақталды! Геолокацияңызды жіберу үшін төмендегі батырманы басыңыз.",
        'en': "Language saved! Press the button below to send
