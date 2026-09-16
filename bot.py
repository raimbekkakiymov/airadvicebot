import os
import sys
import math
import time
import json
import re
import logging
import threading
import requests
import telebot
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from threading import Lock
from math import radians, sin, cos, asin, sqrt, atan2, degrees

# Парсинг
try:
    import cloudscraper
    from bs4 import BeautifulSoup
    PARSING_AVAILABLE = True
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
    )
except ImportError:
    PARSING_AVAILABLE = False
    scraper = None
    print("⚠️ cloudscraper/bs4 не установлены", flush=True)

# ==========================================
# AIRKZ API
# ==========================================

AIRKZ_API_URL = "http://93.185.75.19:4001/"
AIRKZ_USERNAME = "mobileAdmin"
AIRKZ_PASSWORD = "1661429855DDDCAC2AE4D26FAF255"
AIRKZ_CLIENT_ID = "android"
AIRKZ_CLIENT_SECRET = "nvx5qggoqejo71num53l"

_airkz_token = {"access_token": None, "expires_at": 0}
_airkz_token_lock = Lock()

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

DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

USER_LANG_FILE = "user_languages.json"
H2S_CACHE_FILE = "h2s_cache.json"
H2S_UPDATE_DAYS = 7

if not BOT_TOKEN:
    print("❌ BOT_TOKEN не установлен!", flush=True)
    BOT_TOKEN = "DUMMY_TOKEN"

bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

user_languages = {}
user_ids = set()
h2s_cache = {}
h2s_cache_lock = Lock()

def load_user_languages():
    global user_languages
    if os.path.exists(USER_LANG_FILE):
        try:
            with open(USER_LANG_FILE, 'r', encoding='utf-8') as f:
                user_languages = json.load(f)
            print(f"✅ Загружено {len(user_languages)} языков", flush=True)
        except Exception as e:
            logging.error(f"Ошибка загрузки: {e}")

def save_user_languages():
    try:
        with open(USER_LANG_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_languages, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения: {e}")

# ==========================================
# 3. H₂S КЭШ
# ==========================================

def load_h2s_cache():
    global h2s_cache
    if os.path.exists(H2S_CACHE_FILE):
        try:
            with open(H2S_CACHE_FILE, 'r', encoding='utf-8') as f:
                h2s_cache = json.load(f)
            print(f"✅ Загружено {len(h2s_cache)} записей H₂S", flush=True)
        except Exception as e:
            h2s_cache = {}

def save_h2s_cache():
    try:
        with h2s_cache_lock:
            with open(H2S_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(h2s_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения H₂S: {e}")

def get_cache_key(lat, lon):
    return f"{round(lat, 2)},{round(lon, 2)}"

def is_cache_fresh(key):
    if key not in h2s_cache:
        return False
    entry = h2s_cache[key]
    if 'next_update' in entry:
        try:
            next_update = datetime.fromisoformat(entry['next_update'])
            return datetime.now() < next_update
        except:
            pass
    return False

# ==========================================
# 4. ОПРЕДЕЛЕНИЕ РЕГИОНА
# ==========================================

def detect_region(lat, lon):
    if 40 < lat < 56 and 46 < lon < 88:
        return 'kz'
    if 24 < lat < 72 and -170 < lon < -50:
        return 'us'
    return 'world'

# ==========================================
# 5. AIRKZ API
# ==========================================

def airkz_get_token():
    global _airkz_token
    with _airkz_token_lock:
        now = time.time()
        if _airkz_token["access_token"] and _airkz_token["expires_at"] > now + 60:
            return _airkz_token["access_token"]
        try:
            url = f"{AIRKZ_API_URL}oauth/token"
            body = {
                "grant_type": "password",
                "client_id": AIRKZ_CLIENT_ID,
                "client_secret": AIRKZ_CLIENT_SECRET,
                "username": AIRKZ_USERNAME,
                "password": AIRKZ_PASSWORD
            }
            print(f"🔑 AirKZ: запрос токена...", flush=True)
            r = requests.post(url, data=body, timeout=15)
            if r.status_code == 200:
                data = r.json()
                token = data.get("access_token")
                if token:
                    _airkz_token["access_token"] = token
                    _airkz_token["expires_at"] = now + data.get("expires_in", 3600)
                    print(f"✅ AirKZ: токен получен", flush=True)
                    return token
        except Exception as e:
            print(f"❌ AirKZ auth: {e}", flush=True)
        return None

def airkz_extract_h2s_from_json(data):
    found = []
    def walk(obj):
        if isinstance(obj, dict):
            for key, val in obj.items():
                k_low = str(key).lower()
                if k_low in ("h2s", "h_2_s", "h2svalue"):
                    if isinstance(val, (int, float)):
                        found.append({"value": val})
                    elif isinstance(val, dict):
                        v = val.get("value") or val.get("v")
                        if v is not None:
                            found.append({"value": v})
                    elif isinstance(val, str):
                        try:
                            found.append({"value": float(val.replace(",", "."))})
                        except:
                            pass
                walk(val)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(data)
    return found

def airkz_get_h2s(lat, lon):
    token = airkz_get_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    endpoints = [
        f"api/station/nearest?lat={lat}&lon={lon}",
        f"api/station/list",
        f"api/stations",
        f"api/averages",
        f"api/v1/stations",
    ]
    for endpoint in endpoints:
        try:
            r = requests.get(f"{AIRKZ_API_URL}{endpoint}", headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                h2s_list = airkz_extract_h2s_from_json(data)
                if h2s_list:
                    val = h2s_list[0]["value"]
                    print(f"✅ AirKZ: H2S={val}", flush=True)
                    return {
                        "h2s": val,
                        "unit": "mg/m3",
                        "source": "AirKZ API",
                        "station": "AirKZ"
                    }
        except:
            continue
    return None

# ==========================================
# 6. OVERPASS API (ОБЪЕКТЫ НА ВЕТРУ) ⭐ НОВОЕ
# ==========================================

def calculate_distance_bearing(lat1, lon1, lat2, lon2):
    """Расстояние (км) и азимут (°) от точки 1 к точке 2"""
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    distance = 2 * R * asin(sqrt(a))
    
    y = sin(dlon) * cos(radians(lat2))
    x = cos(radians(lat1)) * sin(radians(lat2)) - sin(radians(lat1)) * cos(radians(lat2)) * cos(dlon)
    bearing = (degrees(atan2(y, x)) + 360) % 360
    
    return distance, bearing


def _determine_type(tags):
    """Определяет тип объекта по OSM-тегам"""
    if tags.get("landuse") == "landfill":
        return "Полигон ТБО (свалка)"
    if tags.get("industrial") == "waste_incinerator":
        return "Мусоросжигательный завод"
    if tags.get("power") == "plant":
        fuel = tags.get("plant:source", "")
        if "coal" in fuel:
            return "Угольная ТЭЦ"
        if "gas" in fuel:
            return "Газовая ТЭЦ"
        return "Электростанция/ТЭЦ"
    if tags.get("man_made") == "works":
        product = tags.get("product", "")
        if "oil" in product:
            return "НПЗ"
        if "chemical" in product:
            return "Химзавод"
        if "steel" in product or "metal" in product:
            return "Металлургический завод"
        return "Промышленный завод"
    if tags.get("landuse") == "industrial":
        return "Промзона"
    if tags.get("aeroway") == "aerodrome":
        return "Аэропорт"
    if tags.get("landuse") == "quarry":
        return "Карьер"
    return "Промышленный объект"


def find_industrial_objects(lat, lon, radius_km=10):
    """Поиск промышленных объектов через OpenStreetMap"""
    try:
        radius_m = radius_km * 1000
        
        query = f"""
        [out:json][timeout:30];
        (
          node["man_made"="works"](around:{radius_m},{lat},{lon});
          way["man_made"="works"](around:{radius_m},{lat},{lon});
          node["landuse"="landfill"](around:{radius_m},{lat},{lon});
          way["landuse"="landfill"](around:{radius_m},{lat},{lon});
          node["power"="plant"](around:{radius_m},{lat},{lon});
          way["power"="plant"](around:{radius_m},{lat},{lon});
          node["landuse"="industrial"](around:{radius_m},{lat},{lon});
          way["landuse"="industrial"](around:{radius_m},{lat},{lon});
          node["aeroway"="aerodrome"](around:{radius_m},{lat},{lon});
          way["aeroway"="aerodrome"](around:{radius_m},{lat},{lon});
          node["industrial"="waste_incinerator"](around:{radius_m},{lat},{lon});
          node["landuse"="quarry"](around:{radius_m},{lat},{lon});
          way["landuse"="quarry"](around:{radius_m},{lat},{lon});
        );
        out center tags;
        """
        
        print(f"🔍 Overpass: ищу объекты в {radius_km} км...", flush=True)
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=40,
            headers={"User-Agent": "AirQualityBot/1.0"}
        )
        
        if r.status_code != 200:
            print(f"⚠️ Overpass {r.status_code}", flush=True)
            return []
        
        data = r.json()
        objects = []
        
        for elem in data.get("elements", []):
            obj_lat = elem.get("lat") or elem.get("center", {}).get("lat")
            obj_lon = elem.get("lon") or elem.get("center", {}).get("lon")
            if not obj_lat or not obj_lon:
                continue
            
            tags = elem.get("tags", {})
            obj_type = _determine_type(tags)
            name = tags.get("name") or tags.get("name:ru") or tags.get("name:en") or obj_type
            
            distance, bearing = calculate_distance_bearing(lat, lon, obj_lat, obj_lon)
            
            objects.append({
                "name": name,
                "type": obj_type,
                "distance_km": round(distance, 1),
                "bearing": round(bearing),
                "lat": obj_lat,
                "lon": obj_lon
            })
        
        print(f"✅ Overpass: найдено {len(objects)} объектов", flush=True)
        return objects
    except Exception as e:
        print(f"❌ Overpass: {e}", flush=True)
        return []


def filter_on_wind(objects, wind_deg, tolerance=45):
    """Фильтр: объекты на стороне ветра (±tolerance°)"""
    result = []
    for obj in objects:
        diff = abs(obj['bearing'] - wind_deg)
        if diff > 180:
            diff = 360 - diff
        if diff <= tolerance:
            result.append(obj)
    return sorted(result, key=lambda x: x['distance_km'])

# ==========================================
# 7. ПАРСЕРЫ H₂S (fallback)
# ==========================================

def parse_kazhydromet(lat, lon):
    if not PARSING_AVAILABLE:
        return None
    try:
        url = "https://www.kazhydromet.kz/ru/ecology/monitoring"
        headers = {'User-Agent': 'Mozilla/5.0 Chrome/120.0.0.0'}
        r = scraper.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, 'lxml')
        for table in soup.find_all('table'):
            table_text = table.get_text().lower()
            if 'сероводород' in table_text or 'h2s' in table_text:
                for row in table.find_all('tr'):
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        city = cells[0].get_text(strip=True)
                        for cell in cells[1:]:
                            ct = cell.get_text(strip=True)
                            if re.search(r'[\d.,]+', ct):
                                return {
                                    'h2s': ct, 'unit': 'mg/m³',
                                    'source': 'Казгидромет', 'station': city
                                }
        return None
    except:
        return None


def parse_iqair(lat, lon):
    if not PARSING_AVAILABLE:
        return None
    try:
        url = f"https://www.iqair.com/search?q={lat},{lon}"
        headers = {'User-Agent': 'Mozilla/5.0 Chrome/120.0.0.0'}
        r = scraper.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, 'lxml')
        for a in soup.find_all('a', href=True):
            if '/world-air-quality' in a['href']:
                link = a['href']
                if not link.startswith('http'):
                    link = 'https://www.iqair.com' + link
                r = scraper.get(link, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, 'lxml')
                for row in soup.find_all('tr'):
                    text = row.get_text().lower()
                    if 'h2s' in text or 'hydrogen sulfide' in text:
                        match = re.search(r'([\d.,]+)\s*(µg/m³|mg/m³|ppb|ppm)', row.get_text())
                        if match:
                            return {
                                'h2s': match.group(1).replace(',', '.'),
                                'unit': match.group(2),
                                'source': 'IQAir', 'station': 'IQAir'
                            }
        return None
    except:
        return None


def estimate_h2s_indirect(air_data):
    if not air_data:
        return None
    so2 = air_data.get('so2')
    if not isinstance(so2, (int, float)):
        return None
    if so2 > 50:
        return {'h2s': 'Повышен (косвенно)', 'unit': f'SO2={so2} µg/m³',
                'source': 'Оценка по SO2', 'station': 'косвенно'}
    elif so2 > 20:
        return {'h2s': 'Умеренный (косвенно)', 'unit': f'SO2={so2} µg/m³',
                'source': 'Оценка по SO2', 'station': 'косвенно'}
    return None


def _parse_with_timeout(parser, lat, lon, timeout=15):
    result = [None]
    def target():
        try:
            result[0] = parser(lat, lon)
        except:
            pass
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return result[0] if not t.is_alive() else None


def _parse_h2s_all_sources(lat, lon, air_data=None):
    region = detect_region(lat, lon)
    
    if region == 'kz':
        try:
            result = _parse_with_timeout(airkz_get_h2s, lat, lon, timeout=20)
            if result:
                return result
        except:
            pass
    
    if PARSING_AVAILABLE:
        parsers = [parse_kazhydromet, parse_iqair] if region == 'kz' else [parse_iqair]
        for p in parsers:
            result = _parse_with_timeout(p, lat, lon, timeout=10)
            if result:
                return result
    
    return estimate_h2s_indirect(air_data)


def _parse_and_cache(key, lat, lon, air_data, lang):
    try:
        result = _parse_h2s_all_sources(lat, lon, air_data)
        now = datetime.now()
        
        if result:
            h2s_cache[key] = {
                'h2s': result.get('h2s', 'N/A'),
                'unit': result.get('unit', 'µg/m³'),
                'source': result['source'],
                'station': result.get('station', 'Unknown'),
                'updated': now.isoformat(),
                'next_update': (now + timedelta(days=H2S_UPDATE_DAYS)).isoformat(),
            }
        else:
            h2s_cache[key] = {
                'h2s': None, 'unit': None,
                'source': 'Нет данных', 'station': None,
                'updated': now.isoformat(),
                'next_update': (now + timedelta(days=H2S_UPDATE_DAYS)).isoformat(),
            }
        save_h2s_cache()
        return h2s_cache.get(key)
    except Exception as e:
        logging.error(f"H₂S: {e}")
        return None


def get_h2s_sync(lat, lon, air_data=None, lang='ru', timeout=30):
    key = get_cache_key(lat, lon)
    if is_cache_fresh(key):
        print(f"📦 H₂S из кэша", flush=True)
        return h2s_cache[key]
    
    print(f"🔄 H₂S парсинг...", flush=True)
    result_holder = [None]
    done = threading.Event()
    
    def worker():
        try:
            result_holder[0] = _parse_and_cache(key, lat, lon, air_data, lang)
        finally:
            done.set()
    
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    done.wait(timeout=timeout)
    
    if result_holder[0]:
        return result_holder[0]
    return estimate_h2s_indirect(air_data)

# ==========================================
# 8. DEEPSEEK API
# ==========================================

def call_deepseek(prompt, system_prompt=None, max_tokens=1000, temperature=0.7):
    if not DEEPSEEK_API_KEY:
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
        
        print(f"🤖 DeepSeek запрос...", flush=True)
        r = requests.post(DEEPSEEK_URL, headers=headers, json=body, timeout=25)
        
        if r.status_code == 200:
            data = r.json()
            if 'choices' in data and data['choices']:
                print(f"✅ DeepSeek ответил", flush=True)
                return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"❌ DeepSeek: {e}", flush=True)
    return None

# ==========================================
# 9. МАТЕМАТИКА / ПОГОДА
# ==========================================

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

def get_weather(lat, lon):
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
            }
    except Exception as e:
        logging.error(f"Weather: {e}")
    return None

def get_best_air_data(lat, lon):
    try:
        url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={WAQI_API_KEY}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        if data.get('status') == 'ok':
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
        logging.error(f"WAQI: {e}")
    return None, "None"

def calculate_aqi_from_pm25(pm25):
    if pm25 <= 12: return round((50 / 12) * pm25)
    elif pm25 <= 35.4: return round(((100 - 51) / (35.4 - 12.1)) * (pm25 - 12.1) + 51)
    elif pm25 <= 55.4: return round(((150 - 101) / (55.4 - 35.5)) * (pm25 - 35.5) + 101)
    elif pm25 <= 150.4: return round(((200 - 151) / (150.4 - 55.5)) * (pm25 - 55.5) + 151)
    return 200

# ==========================================
# 10. АНАЛИЗ
# ==========================================

def analyze_pollution(air_data, lang='ru'):
    aqi = air_data.get('aqi') if air_data else None
    pm25 = air_data.get('pm25') if air_data else None
    
    if pm25 and pm25 > 25 and (not aqi or aqi < 50):
        aqi = calculate_aqi_from_pm25(pm25)
    
    levels = {
        'ru': {1: "Чистый воздух", 2: "Умеренное качество", 3: "Вредно для чувствительных",
               4: "Вредный уровень", 5: "Опасный уровень"},
        'kk': {1: "Таза ауа", 2: "Орташа сапа", 3: "Сезімтал топтар үшін зиянды",
               4: "Зиянды деңгей", 5: "Қауіпті деңгей"},
        'en': {1: "Clean air", 2: "Moderate quality", 3: "Unhealthy for sensitive",
               4: "Unhealthy level", 5: "Hazardous"}
    }
    
    t = levels.get(lang, levels['ru'])
    
    if aqi:
        if aqi <= 50: return {'level_str': t[1], 'level_code': 1}
        elif aqi <= 100: return {'level_str': t[2], 'level_code': 2}
        elif aqi <= 150: return {'level_str': t[3], 'level_code': 3}
        elif aqi <= 200: return {'level_str': t[4], 'level_code': 4}
        else: return {'level_str': t[5], 'level_code': 5}
    return {'level_str': t[1], 'level_code': 1}

# ==========================================
# 11. ⭐ НОВЫЙ ЕДИНЫЙ ПРОМПТ (анализ + рекомендации)
# ==========================================

def get_ai_analysis(lat, lon, wind_deg, wind_dir_text, wind_speed,
                    air_data, weather, h2s_data, objects_on_wind, lang='ru'):
    """
    Логика: Объект на ветру → тип объекта → вещества → рекомендации
    """
    if not DEEPSEEK_API_KEY:
        return None
    
    lang_name = {'ru': 'Русский', 'kk': 'Казахский', 'en': 'English'}.get(lang, 'Русский')
    
    # WAQI данные
    aqi = air_data.get('aqi', '—') if air_data else '—'
    pm25 = air_data.get('pm25', '—') if air_data else '—'
    pm10 = air_data.get('pm10', '—') if air_data else '—'
    no2 = air_data.get('no2', '—') if air_data else '—'
    so2 = air_data.get('so2', '—') if air_data else '—'
    temp = weather.get('temp', '—') if weather else '—'
    
    h2s_value = h2s_data.get('h2s', '—') if h2s_data and h2s_data.get('h2s') else '—'
    h2s_unit = h2s_data.get('unit', '') if h2s_data else ''
    
    # Объекты на ветру — ГЛАВНОЕ
    if objects_on_wind:
        obj_lines = []
        for obj in objects_on_wind[:5]:
            obj_lines.append(
                f"• {obj['name']} ({obj['type']}) — {obj['distance_km']} км, азимут {obj['bearing']}°"
            )
        objects_text = "\n".join(obj_lines)
    else:
        objects_text = "нет промышленных объектов на ветру в радиусе 10 км"
    
    prompt = f"""Ты эколог. Язык: {lang_name}.

ПОЛЬЗОВАТЕЛЬ: {lat}, {lon}
ВЕТЕР С: {wind_dir_text} ({wind_deg}°), {wind_speed} м/с

ОБЪЕКТЫ НА СТОРОНЕ ВЕТРА (источник загрязнений):
{objects_text}

ДАННЫЕ WAQI:
AQI: {aqi} | PM2.5: {pm25} | PM10: {pm10} | NO2: {no2} | SO2: {so2}
H2S: {h2s_value} {h2s_unit}
Температура: {temp}°C

ЗАДАЧА:
1. ВАЖНО: Датчики WAQI могут не стоять рядом с объектом,
   но выбросы от объектов на ветру ВСЕГДА летят к пользователю.

2. Для каждого объекта на ветру (из списка выше):
   - Назови тип (НПЗ, ТЭЦ, полигон ТБО, химзавод и т.д.)
   - Укажи какие ВЕЩЕСТВА типичны для такого типа:
     • НПЗ → H₂S, аммиак, бензол, SO₂, NO₂, углеводороды
     • Полигон ТБО → метан, H₂S, тяжёлые металлы, PM2.5
     • ТЭЦ → SO₂, NO₂, зола, PM2.5, PM10
     • Химзавод → NO₂, аммиак, органика
     • Мусоросжигатель → диоксины, металлы, PM2.5
   - Пиши "возможны", "вероятны" — не утверждай факты о выбросах

3. Сопоставь с WAQI:
   - Если SO2 высокий и рядом ТЭЦ/НПЗ → подтверждение
   - Если данные в норме, но объект есть → "датчики могут не улавливать"

4. Дай 4 короткие рекомендации (1 строка каждая):
   • Выход: да/нет/ограниченно
   • Окна: открыть/закрыть
   • Питание: 3 продукта
   • Витамины: 2 добавки

ФОРМАТ ОТВЕТА (строго):
🏭 На ветру: [объект + тип + расстояние]
⚠️ Возможны: [вещества с "возможны"]
📊 С учётом WAQI: [соответствие или расхождение]
📋 Рекомендации: [4 короткие строки]
💡 Вывод: [1 строка]

ВАЖНО:
- Не пиши общие фразы ("транспорт", "предприятия")
- Используй ТОЛЬКО объекты из списка выше
- Если объектов нет — скажи "промышленных источников на ветру не обнаружено"
- Будь краток: 10-12 строк максимум
"""
    
    system_prompt = f"Эколог. Язык: {lang_name}. Кратко, по делу."
    return call_deepseek(prompt, system_prompt, max_tokens=700, temperature=0.3)

# ==========================================
# 12. Rule-based fallback
# ==========================================

def get_rule_based_recommendations(pollution_analysis, lang='ru'):
    risk = pollution_analysis.get('level_code', 1)
    activity_map = {
        'ru': {1: "✅ Можно бегать", 2: "🏃‍♂️ Можно гулять", 3: "⚠️ Лучше в зал", 4: "⛔ Только дома", 5: "🚫 Оставайтесь дома"},
        'kk': {1: "✅ Жүгіруге болады", 2: "🏃‍♂️ Серуендеуге болады", 3: "⚠️ Залға", 4: "⛔ Үйде", 5: "🚫 Үйде"},
        'en': {1: "✅ Can run", 2: "🏃‍♂️ Can walk", 3: "⚠️ Gym", 4: "⛔ Indoor", 5: "🚫 Stay home"}
    }
    activity = activity_map.get(lang, activity_map['ru']).get(risk, "✅ OK")
    
    return (
        f"🏭 На ветру: данных нет\n"
        f"📋 Рекомендации:\n"
        f"• Выход: {activity}\n"
        f"• Окна: закрыть\n"
        f"• Питание: овощи, зелёный чай, яблоки\n"
        f"• Витамины: C, Омега-3"
    )

# ==========================================
# 13. ФОРМАТИРОВАНИЕ H₂S
# ==========================================

def format_h2s_block(h2s_data, lang='ru'):
    titles = {
        'ru': {'title': "🛢 СЕРОВОДОРОД (H₂S)", 'value': "Значение",
               'station': "Станция", 'source': "Источник",
               'no_data': "Прямых датчиков рядом нет",
               'estimate': "Оценка по SO₂",
               'norm': "Норма ВОЗ: 0.15 мг/м³"},
        'kk': {'title': "🛢 КҮКІРТСУТЕК (H₂S)", 'value': "Мәні",
               'station': "Станция", 'source': "Дереккөз",
               'no_data': "Датчиктер жоқ", 'estimate': "SO₂ бағасы",
               'norm': "ДДҰ: 0.15 мг/м³"},
        'en': {'title': "🛢 H₂S", 'value': "Value", 'station': "Station",
               'source': "Source", 'no_data': "No direct sensors",
               'estimate': "SO₂ estimate", 'norm': "WHO limit: 0.15 mg/m³"}
    }
    t = titles.get(lang, titles['ru'])
    msg = f"**{t['title']}:**\n"
    
    if h2s_data and h2s_data.get('h2s'):
        msg += f"• {t['value']}: {h2s_data['h2s']} {h2s_data.get('unit', '')}\n"
        if h2s_data.get('station'):
            msg += f"• {t['station']}: {h2s_data['station']}\n"
        msg += f"• {t['source']}: {h2s_data['source']}\n"
        msg += f"• {t['norm']}\n\n"
    elif h2s_data and 'Оценка' in h2s_data.get('source', ''):
        msg += f"• {t['estimate']}: {h2s_data['h2s']}\n"
        msg += f"• {t['norm']}\n\n"
    else:
        msg += f"• {t['no_data']}\n"
        msg += f"• {t['norm']}\n\n"
    return msg

# ==========================================
# 14. ФОРМАТИРОВАНИЕ ОТЧЁТА
# ==========================================

def format_full_response(air_data, weather, pollution_analysis, ai_analysis, source_name, lang='ru', h2s_data=None):
    if not lang:
        lang = 'ru'
    
    titles = {
        'ru': {'report': "Экологический отчет", 'air_quality': "Качество воздуха",
               'status': "Статус", 'weather': "Погода", 'temp': "Температура",
               'humidity': "Влажность", 'wind': "Ветер", 'no_data': "Нет данных",
               'source': "Источник"},
        'kk': {'report': "Экологиялық есеп", 'air_quality': "Ауа сапасы",
               'status': "Статус", 'weather': "Ауа райы", 'temp': "Температура",
               'humidity': "Ылғалдылық", 'wind': "Жел", 'no_data': "Деректер жоқ",
               'source': "Дереккөз"},
        'en': {'report': "Environmental Report", 'air_quality': "Air Quality",
               'status': "Status", 'weather': "Weather", 'temp': "Temperature",
               'humidity': "Humidity", 'wind': "Wind", 'no_data': "No data",
               'source': "Source"}
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
            if lang == 'ru': msg += f"⚠️ PM2.5 превышает норму ВОЗ\n"
            elif lang == 'kk': msg += f"⚠️ PM2.5 ДДҰ асып түсті\n"
            else: msg += f"⚠️ PM2.5 exceeds WHO limit\n"
        
        msg += f"{t['status']}: **{pollution_analysis['level_str']}**\n\n"
    else:
        msg += f"📊 **{t['air_quality']}:** {t['no_data']}\n\n"
    
    if weather:
        msg += f"💨 **{t['weather']}:**\n"
        msg += f"• {t['temp']}: {weather['temp']} C\n"
        msg += f"• {t['humidity']}: {weather['humidity']}%\n"
        msg += f"• {t['wind']}: {get_wind_direction_text(weather['wind_deg'], lang)}, {weather['wind_speed']} м/с\n\n"
    
    if h2s_data is not None:
        msg += format_h2s_block(h2s_data, lang)
    
    # ⭐ ЕДИНЫЙ БЛОК ИИ-АНАЛИЗА
    if ai_analysis:
        msg += f"{ai_analysis}\n\n"
    
    msg += "───────────────────────\n"
    msg += f"📡 _{t['source']}: {source_name}_"
    msg += f"\n🤖 _AI: DeepSeek + OpenStreetMap_"
    
    return msg

def safe_send_message(chat_id, text):
    max_length = 4000
    lang = user_languages.get(str(chat_id), 'ru')
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    location_text = {'ru': "📍 Новая локация", 'kk': "📍 Жаңа орын", 'en': "📍 New location"}.get(lang, "📍 New location")
    refresh_text = {'ru': "🔄 Обновить", 'kk': "🔄 Жаңарту", 'en': "🔄 Refresh"}.get(lang, "🔄 Refresh")
    
    markup.add(types.KeyboardButton(location_text, request_location=True))
    markup.add(types.KeyboardButton(refresh_text))
    
    try:
        if len(text) <= max_length:
            try:
                bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=markup)
            except:
                bot.send_message(chat_id, text, reply_markup=markup)
            return
        
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
        
        for i, part in enumerate(parts):
            if len(parts) > 1:
                part += f"\n\n📄 Часть {i+1}/{len(parts)}"
            try:
                if i == len(parts) - 1:
                    bot.send_message(chat_id, part, parse_mode='Markdown', reply_markup=markup)
                else:
                    bot.send_message(chat_id, part, parse_mode='Markdown')
            except:
                bot.send_message(chat_id, part)
            time.sleep(0.5)
    except Exception as e:
        logging.error(f"Send error: {e}")

# ==========================================
# 15. ОБРАБОТЧИКИ
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
    bot.send_message(message.chat.id, "Выберите язык:", reply_markup=markup)

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
    location_text = {'ru': "📍 Отправить локацию", 'kk': "📍 Орынды жіберу", 'en': "📍 Send Location"}.get(lang, "📍 Send Location")
    markup.add(types.KeyboardButton(location_text, request_location=True))
    start_text = {'ru': "🚀 Старт", 'kk': "🚀 Бастау", 'en': "🚀 Start"}.get(lang, "🚀 Start")
    markup.add(types.KeyboardButton(start_text))
    
    confirm = {'ru': "Язык сохранен!", 'kk': "Тіл сақталды!", 'en': "Language saved!"}.get(lang, "OK")
    bot.send_message(message.chat.id, confirm, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['🚀 Старт', '🚀 Бастау', '🚀 Start'])
def start_button(message):
    user_id = str(message.chat.id)
    lang = user_languages.get(user_id, 'ru')
    
    welcome_text = {
        'ru': "👋 Добро пожаловать!\n\n📍 Отправьте геолокацию, чтобы начать!",
        'kk': "👋 Қош келдіңіз!\n\n📍 Геолокацияңызды жіберіңіз!",
        'en': "👋 Welcome!\n\n📍 Send your location to start!"
    }.get(lang, "Welcome!")
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    location_text = {'ru': "📍 Отправить локацию", 'kk': "📍 Орынды жіберу", 'en': "📍 Send Location"}.get(lang, "📍 Send Location")
    markup.add(types.KeyboardButton(location_text, request_location=True))
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['🔄 Обновить', '🔄 Жаңарту', '🔄 Refresh'])
def refresh_data(message):
    user_id = str(message.chat.id)
    lang = user_languages.get(user_id, 'ru')
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    location_text = {'ru': "📍 Отправить локацию", 'kk': "📍 Орынды жіберу", 'en': "📍 Send Location"}.get(lang, "📍 Send Location")
    markup.add(types.KeyboardButton(location_text, request_location=True))
    
    msg = {'ru': "Отправьте новую локацию.", 'kk': "Жаңа геолокация жіберіңіз.", 'en': "Send new location."}.get(lang, "OK")
    bot.send_message(message.chat.id, msg, reply_markup=markup)

# ==========================================
# ГЛАВНЫЙ ОБРАБОТЧИК ЛОКАЦИИ ⭐ ОБНОВЛЁН
# ==========================================

@bot.message_handler(content_types=['location'])
def handle_location(message):
    print("📍 Геолокация получена", flush=True)
    user_id = str(message.chat.id)
    user_ids.add(message.chat.id)
    
    lang = user_languages.get(user_id, 'ru')
    
    if user_id not in user_languages:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row('🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English')
        bot.send_message(message.chat.id, "Сначала выберите язык:", reply_markup=markup)
        return
    
    lat = float(message.location.latitude)
    lon = float(message.location.longitude)
    
    wait_msgs = {
        'ru': "⏳ Обрабатываю данные...",
        'kk': "⏳ Деректерді өңдеп жатырмын...",
        'en': "⏳ Processing data..."
    }
    
    try:
        status_msg = bot.send_message(message.chat.id, wait_msgs.get(lang, wait_msgs['ru']))
        status_msg_id = status_msg.message_id
    except:
        status_msg_id = None
    
    def update_status(text):
        if status_msg_id:
            try:
                bot.edit_message_text(text, chat_id=message.chat.id, message_id=status_msg_id)
            except:
                pass
    
    try:
        # 1. Качество воздуха
        update_status({
            'ru': "📊 Получаю качество воздуха...",
            'kk': "📊 Ауа сапасын аламын...",
            'en': "📊 Getting air quality..."
        }.get(lang))
        
        air_data, source_name = get_best_air_data(lat, lon)
        weather = get_weather(lat, lon)
        
        wind_deg = weather.get('wind_deg', 0) if weather else 0
        wind_dir_text = get_wind_direction_text(wind_deg, lang)
        wind_speed = weather.get('wind_speed', 0) if weather else 0
        
        # 2. H₂S
        update_status({
            'ru': "🛢 Ищу H₂S...",
            'kk': "🛢 H₂S іздеймін...",
            'en': "🛢 Searching H₂S..."
        }.get(lang))
        
        h2s_data = get_h2s_sync(lat, lon, air_data, lang, timeout=25)
        
        # 3. ОБЪЕКТЫ НА ВЕТРУ (Overpass API) ⭐ НОВОЕ
        update_status({
            'ru': "🏭 Ищу объекты на ветру...",
            'kk': "🏭 Жел жағындағы нысандарды іздеймін...",
            'en': "🏭 Finding objects on wind side..."
        }.get(lang))
        
        all_objects = find_industrial_objects(lat, lon, radius_km=10)
        objects_on_wind = filter_on_wind(all_objects, wind_deg, tolerance=45)
        
        print(f"📍 Найдено {len(all_objects)} объектов, из них {len(objects_on_wind)} на ветру", flush=True)
        
        pollution_analysis = analyze_pollution(air_data, lang)
        
        # 4. ЕДИНЫЙ запрос к DeepSeek (анализ + рекомендации) ⭐ НОВОЕ
        update_status({
            'ru': "🤖 Анализирую через ИИ...",
            'kk': "🤖 ИИ арқылы талдаймын...",
            'en': "🤖 Analyzing with AI..."
        }.get(lang))
        
        ai_analysis = get_ai_analysis(
            lat, lon, wind_deg, wind_dir_text, wind_speed,
            air_data, weather, h2s_data, objects_on_wind, lang
        )
        
        if not ai_analysis:
            ai_analysis = get_rule_based_recommendations(pollution_analysis, lang)
        
        # 5. Удаляем статус и отправляем
        if status_msg_id:
            try:
                bot.delete_message(message.chat.id, status_msg_id)
            except:
                pass
        
        response = format_full_response(
            air_data, weather, pollution_analysis,
            ai_analysis, source_name, lang, h2s_data
        )
        
        safe_send_message(message.chat.id, response)
        
    except Exception as e:
        logging.error(f"Handle location error: {e}")
        if status_msg_id:
            try:
                bot.delete_message(message.chat.id, status_msg_id)
            except:
                pass
        
        error_msg = {'ru': "❌ Ошибка. Попробуйте ещё раз.", 'kk': "❌ Қате.", 'en': "❌ Error."}.get(lang, "❌ Error")
        bot.send_message(message.chat.id, error_msg)

# ==========================================
# 16. ФОНОВЫЕ ЗАДАЧИ
# ==========================================

def background_notifier():
    while True:
        time.sleep(21600)
        for uid in list(user_ids):
            try:
                lang = user_languages.get(str(uid), 'ru')
                remind = {'ru': "🔔 Проверьте качество воздуха!", 'kk': "🔔 Ауа сапасын тексеріңіз!", 'en': "🔔 Check air quality!"}.get(lang, "🔔 Check air quality!")
                bot.send_message(uid, remind)
            except Exception as e:
                logging.error(f"Notify error: {e}")

# ==========================================
# 17. ЗАПУСК
# ==========================================

if __name__ == '__main__':
    print("=" * 50, flush=True)
    print("🚀 ЗАПУСК БОТА...", flush=True)
    print(f"🔑 BOT_TOKEN: {'✅' if BOT_TOKEN and BOT_TOKEN != 'DUMMY_TOKEN' else '❌'}", flush=True)
    print(f"🤖 DEEPSEEK: {'✅' if DEEPSEEK_API_KEY else '❌'}", flush=True)
    print(f"🌤 WEATHER: {'✅' if WEATHER_API_KEY else '❌'}", flush=True)
    print(f"🕷 PARSING: {'✅' if PARSING_AVAILABLE else '❌'}", flush=True)
    print(f"🛢 AIRKZ API: {AIRKZ_API_URL}", flush=True)
    print(f"🏭 OVERPASS: ✅ (OpenStreetMap)", flush=True)
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
    load_h2s_cache()
    
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
