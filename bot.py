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
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify
import asyncio
import aiohttp

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WAQI_API_KEY = os.getenv("WAQI_API_KEY", "demo")
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-app.onrender.com

# Константы
VERSION = "3.0.0"
CACHE_TIMEOUT = 600  # 10 минут
RATE_LIMIT = 10  # запросов в минуту
NOTIFICATION_INTERVAL = 21600  # 6 часов

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
# 2. БАЗА ДАННЫХ (PostgreSQL)
# ==========================================

class Database:
    def __init__(self):
        self.conn = None
        self.connect()
        self.init_tables()
    
    def connect(self):
        try:
            if DATABASE_URL:
                self.conn = psycopg2.connect(DATABASE_URL, sslmode='require')
                logger.info("✅ Подключено к PostgreSQL")
            else:
                logger.warning("⚠️ DATABASE_URL не найден, использую JSON файл")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к БД: {e}")
            self.conn = None
    
    def init_tables(self):
        if not self.conn:
            return
        
        try:
            cur = self.conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    language TEXT DEFAULT 'ru',
                    latitude FLOAT,
                    longitude FLOAT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    last_active TIMESTAMP DEFAULT NOW()
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS air_quality_history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id),
                    latitude FLOAT,
                    longitude FLOAT,
                    aqi INTEGER,
                    pm25 FLOAT,
                    pm10 FLOAT,
                    no2 FLOAT,
                    so2 FLOAT,
                    co FLOAT,
                    o3 FLOAT,
                    source TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
                    total_requests INTEGER DEFAULT 0,
                    last_request_time TIMESTAMP,
                    daily_requests INTEGER DEFAULT 0,
                    request_date DATE DEFAULT CURRENT_DATE
                )
            """)
            
            self.conn.commit()
            logger.info("✅ Таблицы созданы/проверены")
        except Exception as e:
            logger.error(f"❌ Ошибка создания таблиц: {e}")
    
    def save_user(self, user_id, username, first_name, language='ru'):
        if not self.conn:
            return
        
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO users (user_id, username, first_name, language)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    language = EXCLUDED.language,
                    last_active = NOW()
            """, (user_id, username, first_name, language))
            self.conn.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения пользователя: {e}")
    
    def update_user_location(self, user_id, lat, lon):
        if not self.conn:
            return
        
        try:
            cur = self.conn.cursor()
            cur.execute("""
                UPDATE users 
                SET latitude = %s, longitude = %s, last_active = NOW()
                WHERE user_id = %s
            """, (lat, lon, user_id))
            self.conn.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка обновления локации: {e}")
    
    def update_user_language(self, user_id, language):
        if not self.conn:
            return
        
        try:
            cur = self.conn.cursor()
            cur.execute("""
                UPDATE users 
                SET language = %s, last_active = NOW()
                WHERE user_id = %s
            """, (language, user_id))
            self.conn.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка обновления языка: {e}")
    
    def get_user(self, user_id):
        if not self.conn:
            return None
        
        try:
            cur = self.conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            return cur.fetchone()
        except Exception as e:
            logger.error(f"❌ Ошибка получения пользователя: {e}")
            return None
    
    def get_all_users(self):
        if not self.conn:
            return []
        
        try:
            cur = self.conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT * FROM users WHERE last_active > NOW() - INTERVAL '30 days'")
            return cur.fetchall()
        except Exception as e:
            logger.error(f"❌ Ошибка получения пользователей: {e}")
            return []
    
    def save_air_quality(self, user_id, lat, lon, air_data, source):
        if not self.conn or not air_data:
            return
        
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO air_quality_history 
                (user_id, latitude, longitude, aqi, pm25, pm10, no2, so2, co, o3, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, lat, lon,
                air_data.get('aqi'),
                air_data.get('pm25'),
                air_data.get('pm10'),
                air_data.get('no2'),
                air_data.get('so2'),
                air_data.get('co'),
                air_data.get('o3'),
                source
            ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения данных о воздухе: {e}")
    
    def get_air_history(self, user_id, hours=24):
        if not self.conn:
            return []
        
        try:
            cur = self.conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT * FROM air_quality_history 
                WHERE user_id = %s 
                AND created_at > NOW() - INTERVAL '%s hours'
                ORDER BY created_at DESC
                LIMIT 100
            """, (user_id, hours))
            return cur.fetchall()
        except Exception as e:
            logger.error(f"❌ Ошибка получения истории: {e}")
            return []
    
    def update_stats(self, user_id):
        if not self.conn:
            return
        
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO user_stats (user_id, total_requests, last_request_time, daily_requests, request_date)
                VALUES (%s, 1, NOW(), 1, CURRENT_DATE)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    total_requests = user_stats.total_requests + 1,
                    last_request_time = NOW(),
                    daily_requests = CASE 
                        WHEN user_stats.request_date = CURRENT_DATE THEN user_stats.daily_requests + 1
                        ELSE 1
                    END,
                    request_date = CURRENT_DATE
            """, (user_id,))
            self.conn.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка обновления статистики: {e}")

# Инициализация БД
db = Database()

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
# 6. API ФУНКЦИИ (с кэшированием)
# ==========================================

@lru_cache(maxsize=128)
def get_weather_cached(lat, lon, timestamp):
    """Кэшированная версия get_weather"""
    if not WEATHER_API_KEY:
        return None
    
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
                'description': data['weather'][0]['description'],
                'pressure': data['main'].get('pressure'),
                'visibility': data.get('visibility')
            }
    except Exception as e:
        logger.error(f"OpenWeatherMap error: {e}")
    return None

def get_weather(lat, lon):
    # Кэшируем на 10 минут
    cache_key = f"weather_{round(lat,2)}_{round(lon,2)}"
    cached = cache.get(cache_key)
    if cached:
        logger.info("📦 Погода из кэша")
        return cached
    
    result = get_weather_cached(lat, lon, int(time.time() // CACHE_TIMEOUT))
    if result:
        cache.set(cache_key, result)
    
    return result

async def get_air_quality_async(lat, lon):
    """Асинхронное получение данных о воздухе"""
    async with aiohttp.ClientSession() as session:
        # Пробуем WAQI
        try:
            token = WAQI_API_KEY or "demo"
            url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={token}"
            async with session.get(url) as r:
                data = await r.json()
                
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
                        return result, "WAQI"
        except Exception as e:
            logger.error(f"WAQI error: {e}")
        
        # Пробуем OpenAQ
        try:
            url = f"https://api.openaq.org/v2/latest?coordinates={lat},{lon}&radius=25000&limit=10"
            headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
            async with session.get(url, headers=headers) as r:
                data = await r.json()
                
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
                        return components, "OpenAQ"
        except Exception as e:
            logger.error(f"OpenAQ error: {e}")
        
        return None, "None"

def get_best_air_data(lat, lon):
    cache_key = f"air_{round(lat,2)}_{round(lon,2)}"
    cached = cache.get(cache_key)
    if cached:
        logger.info("📦 Данные о воздухе из кэша")
        return cached
    
    # Используем asyncio для асинхронных запросов
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result, source = loop.run_until_complete(get_air_quality_async(lat, lon))
    loop.close()
    
    if result:
        cache.set(cache_key, (result, source))
    
    return result, source

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
        prompt = f"""
Ты — эксперт по экологии и промышленной безопасности.

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

Ответь на языке: {lang_name}
"""
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}
        body = {
            "systemInstruction": {
                "parts": [{"text": f"Ты отвечаешь ТОЛЬКО на языке: {lang_name}. Все названия продуктов, витаминов, активности — только на {lang_name}. Не используй другие языки."}]
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
        msg += f"
