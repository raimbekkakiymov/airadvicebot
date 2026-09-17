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

# Парсинг HTML
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

# WebSocket для ecodata.kz (экспериментально)
try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("⚠️ websocket-client не установлен — ecodata.kz недоступен", flush=True)

# ==========================================
# AIRKZ API (OAuth)
# ==========================================

AIRKZ_API_URL = "http://93.185.75.19:4001/"
AIRKZ_USERNAME = "mobileAdmin"
AIRKZ_PASSWORD = "1661429855DDDCAC2AE4D26FAF255"
AIRKZ_CLIENT_ID = "android"
AIRKZ_CLIENT_SECRET = "nvx5qggoqejo71num53l"

_airkz_token = {"access_token": None, "expires_at": 0}
_airkz_token_lock = Lock()

# ==========================================
# ECODATA.KZ (Kazhydromet R Shiny) ⭐ НОВОЕ
# ==========================================

ECODATA_BASE = "http://ecodata.kz:3838/app_dem_visual"
ECODATA_TIMEOUT = 15

# ==========================================
# 1. HEALTH CHECK SERVER
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
        print(f"✅ Health check server на порту {port}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"❌ Health check: {e}", flush=True)


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
# 4. РЕГИОН
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
# 5B. ⭐ ECODATA.KZ (Kazhydromet R Shiny)
# ==========================================

def ecodata_get_session_id():
    """
    Получает SESSION_ID от R Shiny приложения.
    Приложение отдаёт session при первом GET запросе.
    """
    try:
        r = requests.get(
            f"{ECODATA_BASE}/",
            timeout=ECODATA_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            print(f"⚠️ ecodata: главная {r.status_code}", flush=True)
            return None
        
        # R Shiny передаёт session_id в HTML
        # Ищем паттерн: session/XXXXXXXXXXXXXXXX/...
        match = re.search(r'session/([a-zA-Z0-9]+)/', r.text)
        if match:
            session_id = match.group(1)
            print(f"✅ ecodata: session_id = {session_id[:20]}...", flush=True)
            return session_id
        return None
    except Exception as e:
        print(f"❌ ecodata session: {e}", flush=True)
        return None


def ecodata_get_h2s(lat, lon):
    """
    Попытка получить H2S через ecodata.kz.
    
    Пробует несколько подходов:
    1. SockJS WebSocket (экспериментально)
    2. Прямые HTTP endpoints
    3. Fallback — возвращает None
    """
    print(f"🌐 ecodata.kz: попытка получить данные для {lat},{lon}", flush=True)
    
    # ─── Подход 1: Проверяем доступность ───
    try:
        r = requests.get(
            f"{ECODATA_BASE}/__sockjs__/info",
            timeout=ECODATA_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            info = r.json()
            print(f"✅ ecodata: SockJS доступен, websocket={info.get('websocket')}", flush=True)
    except Exception as e:
        print(f"⚠️ ecodata SockJS info: {e}", flush=True)
        return None
    
    # ─── Подход 2: Пробуем прямые HTTP endpoints ───
    # R Shiny может отдавать данные через /dataobj/ или /session/
    direct_endpoints = [
        f"{ECODATA_BASE}/data/measurements.json",
        f"{ECODATA_BASE}/data/stations.json",
        f"{ECODATA_BASE}/data/h2s.json",
        f"{ECODATA_BASE}/stations.json",
        f"{ECODATA_BASE}/h2s.json",
    ]
    
    for endpoint in direct_endpoints:
        try:
            r = requests.get(
                endpoint,
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                content_type = r.headers.get('content-type', '')
                if 'json' in content_type:
                    try:
                        data = r.json()
                        h2s_list = airkz_extract_h2s_from_json(data)
                        if h2s_list:
                            print(f"✅ ecodata: H2S={h2s_list[0]['value']} через {endpoint}", flush=True)
                            return {
                                "h2s": h2s_list[0]['value'],
                                "unit": "mg/m³",
                                "source": "ecodata.kz",
                                "station": "Казгидромет"
                            }
                    except:
                        pass
        except:
            continue
    
    # ─── Подход 3: WebSocket (экспериментально) ───
    if WS_AVAILABLE:
        session_id = ecodata_get_session_id()
        if session_id:
            try:
                # Формат SockJS WebSocket URL
                ws_url = f"ws://ecodata.kz:3838/app_dem_visual/__sockjs__/n={session_id}/websocket"
                print(f"🔌 ecodata: пробую WS: {ws_url}", flush=True)
                
                ws = websocket.create_connection(ws_url, timeout=10)
                
                # R Shiny обменивается сообщениями
                # Отправляем запрос данных
                ws.send('["0"]')  # init
                
                # Ждём ответ
                received_data = None
                start_time = time.time()
                
                while time.time() - start_time < 8:
                    try:
                        msg = ws.recv()
                        if msg and len(msg) > 10:
                            # Ищем H2S в бинарных/текстовых данных
                            if isinstance(msg, str):
                                if 'h2s' in msg.lower() or 'сероводород' in msg.lower():
                                    print(f"🎯 ecodata: H2S найден в WS!", flush=True)
                                    received_data = msg
                                    break
                            elif isinstance(msg, bytes):
                                # Бинарные данные — ищем паттерны
                                text = msg.decode('utf-8', errors='ignore')
                                if 'h2s' in text.lower():
                                    received_data = text
                                    break
                    except:
                        break
                
                ws.close()
                
                if received_data:
                    print(f"✅ ecodata: получены данные", flush=True)
                    # Тут нужно парсить R serialization — сложно
                    # Возвращаем None пока, чтобы не ломать логику
                
            except Exception as e:
                print(f"⚠️ ecodata WS: {e}", flush=True)
    
    print(f"⚠️ ecodata: H2S не получен", flush=True)
    return None

# ==========================================
# 6. OVERPASS API
# ==========================================

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]


def calculate_distance_bearing(lat1, lon1, lat2, lon2):
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
    if tags.get("landuse") == "landfill":
        return "Полигон ТБО"
    if tags.get("industrial") == "waste_incinerator":
        return "Мусоросжигательный завод"
    if tags.get("power") == "plant":
        fuel = tags.get("plant:source", "")
        if "coal" in fuel: return "Угольная ТЭЦ"
        if "gas" in fuel: return "Газовая ТЭЦ"
        if "oil" in fuel: return "Мазутная ТЭЦ"
        return "Электростанция/ТЭЦ"
    industrial = tags.get("industrial", "")
    if industrial == "oil": return "Нефтедобыча"
    if industrial == "refinery": return "НПЗ"
    if industrial == "chemical": return "Химзавод"
    if industrial == "steel": return "Металлургический завод"
    if industrial == "mine": return "Шахта/добыча"
    if industrial == "port": return "Порт"
    if tags.get("man_made") == "works":
        product = tags.get("product", "")
        if "oil" in product: return "НПЗ"
        if "chemical" in product: return "Химзавод"
        if "steel" in product or "metal" in product: return "Металлургический завод"
        if "cement" in product: return "Цементный завод"
        return "Промышленный завод"
    if tags.get("man_made") == "chimney": return "Промышленная труба"
    if tags.get("man_made") == "storage_tank": return "Резервуар"
    if tags.get("man_made") == "silo": return "Элеватор/силос"
    if tags.get("building") == "industrial": return "Промздание"
    if tags.get("building") == "factory": return "Фабрика"
    if tags.get("landuse") == "industrial": return "Промзона"
    if tags.get("aeroway") == "aerodrome": return "Аэропорт"
    if tags.get("landuse") == "quarry": return "Карьер"
    return "Промышленный объект"


def _query_overpass(query):
    for server in OVERPASS_SERVERS:
        try:
            r = requests.post(
                server,
                data={"data": query},
                timeout=60,
                headers={"User-Agent": "AirQualityBot/1.0"}
            )
            if r.status_code == 200:
                data = r.json()
                if "elements" in data:
                    return data
        except Exception as e:
            print(f"⚠️ Overpass {server}: {e}", flush=True)
            continue
    return None


def find_industrial_objects(lat, lon, radius_km=10):
    try:
        radius_m = int(radius_km * 1000)
        query = f"""
        [out:json][timeout:60];
        (
          nwr["man_made"="works"](around:{radius_m},{lat},{lon});
          nwr["landuse"="industrial"](around:{radius_m},{lat},{lon});
          nwr["industrial"](around:{radius_m},{lat},{lon});
          nwr["power"="plant"](around:{radius_m},{lat},{lon});
          nwr["landuse"="landfill"](around:{radius_m},{lat},{lon});
          nwr["man_made"="chimney"](around:{radius_m},{lat},{lon});
          nwr["man_made"="storage_tank"](around:{radius_m},{lat},{lon});
          nwr["man_made"="silo"](around:{radius_m},{lat},{lon});
          nwr["building"="industrial"](around:{radius_m},{lat},{lon});
          nwr["building"="factory"](around:{radius_m},{lat},{lon});
          nwr["aeroway"="aerodrome"](around:{radius_m},{lat},{lon});
          nwr["landuse"="quarry"](around:{radius_m},{lat},{lon});
        );
        out center tags;
        """
        print(f"🔍 Overpass: ищу объекты в {radius_km} км...", flush=True)
        data = _query_overpass(query)
        if not data:
            return []
        elements = data.get("elements", [])
        objects = []
        seen = set()
        for elem in elements:
            obj_lat = elem.get("lat")
            obj_lon = elem.get("lon")
            if not obj_lat or not obj_lon:
                center = elem.get("center", {})
                obj_lat = center.get("lat")
                obj_lon = center.get("lon")
            if not obj_lat or not obj_lon:
                continue
            key = f"{round(obj_lat,4)}_{round(obj_lon,4)}"
            if key in seen:
                continue
            seen.add(key)
            tags = elem.get("tags", {})
            name = (tags.get("name") or tags.get("name:ru") or tags.get("name:en")
                    or tags.get("operator") or tags.get("brand") or _determine_type(tags))
            obj_type = _determine_type(tags)
            distance, bearing = calculate_distance_bearing(lat, lon, obj_lat, obj_lon)
            if distance < 0.1:
                continue
            objects.append({
                "name": name, "type": obj_type,
                "distance_km": round(distance, 1),
                "bearing": round(bearing),
                "lat": obj_lat, "lon": obj_lon, "tags": tags
            })
        objects.sort(key=lambda x: x["distance_km"])
        print(f"✅ Overpass: {len(objects)} объектов", flush=True)
        for obj in objects[:3]:
            print(f"   • {obj['name']} ({obj['type']}) — {obj['distance_km']} км, {obj['bearing']}°", flush=True)
        return objects
    except Exception as e:
        print(f"❌ Overpass: {e}", flush=True)
        return []


def filter_on_wind(objects, wind_deg, tolerance=60):
    result = []
    for obj in objects:
        diff = abs(obj['bearing'] - wind_deg)
        if diff > 180:
            diff = 360 - diff
        if diff <= tolerance:
            obj_copy = dict(obj)
            obj_copy['wind_diff'] = round(diff)
            result.append(obj_copy)
    return sorted(result, key=lambda x: (x['distance_km'], x['wind_diff']))

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
                                return {'h2s': ct, 'unit': 'mg/m³',
                                        'source': 'Казгидромет', 'station': city}
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
                            return {'h2s': match.group(1).replace(',', '.'),
                                    'unit': match.group(2),
                                    'source': 'IQAir', 'station': 'IQAir'}
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
    """
    Порядок приоритета:
    1. ECODATA.KZ (Kazhydromet официальный, R Shiny) — только для KZ
    2. AIRKZ API (OAuth)
    3. Парсинг HTML (Казгидромет, IQAir)
    4. Оценка по SO2
    """
    region = detect_region(lat, lon)
    
    if region == 'kz':
        # ⭐ 1. ecodata.kz (новое)
        try:
            result = _parse_with_timeout(ecodata_get_h2s, lat, lon, timeout=20)
            if result:
                return result
        except Exception as e:
            print(f"⚠️ ecodata: {e}", flush=True)
        
        # 2. AirKZ API
        try:
            result = _parse_with_timeout(airkz_get_h2s, lat, lon, timeout=20)
            if result:
                return result
        except:
            pass
    
    # 3. Парсеры HTML
    if PARSING_AVAILABLE:
        parsers = [parse_kazhydromet, parse_iqair] if region == 'kz' else [parse_iqair]
        for p in parsers:
            result = _parse_with_timeout(p, lat, lon, timeout=10)
            if result:
                return result
    
    # 4. Оценка по SO2
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
        r = requests.post(DEEPSEEK_URL, headers=headers, json=body, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if 'choices' in data and data['choices']:
                print(f"✅ DeepSeek ответил", flush=True)
                return data['choices'][0]['message']['content']
        else:
            print(f"❌ DeepSeek {r.status_code}", flush=True)
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
# 11. DEEPSEEK ПРОМПТ
# ==========================================

def get_ai_analysis(lat, lon, wind_deg, wind_dir_text, wind_speed,
                    air_data, weather, h2s_data, objects_on_wind, lang='ru'):
    if not DEEPSEEK_API_KEY:
        return None
    lang_name = {'ru': 'Русский', 'kk': 'Казахский', 'en': 'English'}.get(lang, 'Русский')
    aqi = air_data.get('aqi', '—') if air_data else '—'
    pm25 = air_data.get('pm25', '—') if air_data else '—'
    no2 = air_data.get('no2', '—') if air_data else '—'
    so2 = air_data.get('so2', '—') if air_data else '—'
    temp = weather.get('temp', '—') if weather else '—'
    h2s_value = h2s_data.get('h2s', '—') if h2s_data and h2s_data.get('h2s') else '—'
    h2s_unit = h2s_data.get('unit', '') if h2s_data else ''
    if objects_on_wind:
        obj_lines = []
        for obj in objects_on_wind[:5]:
            obj_lines.append(f"• {obj['name']} — тип: {obj['type']} — {obj['distance_km']} км, азимут {obj['bearing']}°")
        objects_text = "\n".join(obj_lines)
    else:
        objects_text = "ОБЪЕКТОВ НЕ НАЙДЕНО в радиусе 10 км"
    prompt = f"""Ты эколог-аналитик. Язык ответа: {lang_name}.

КРИТИЧЕСКИ ВАЖНО: Объекты ниже — РЕАЛЬНЫЕ данные из OSM. Не придумывай свои.

ПОЛЬЗОВАТЕЛЬ: {lat}, {lon}
ВЕТЕР ДУЕТ С: {wind_dir_text} ({wind_deg}°), {wind_speed} м/с

=== ОБЪЕКТЫ НА СТОРОНЕ ВЕТРА ===
{objects_text}

=== ДАННЫЕ WAQI ===
AQI: {aqi} | PM2.5: {pm25} | NO2: {no2} | SO2: {so2}
H2S: {h2s_value} {h2s_unit}
Температура: {temp}°C

ЗАДАЧА:
1. Возьми 2-3 объекта из списка выше. Если нет — напиши "промышленных источников не обнаружено".
2. Для каждого: название, тип, расстояние, возможные вещества:
   НПЗ → H₂S, аммиак, бензол, SO₂, NO₂
   Полигон ТБО → метан, H₂S, металлы, PM2.5
   ТЭЦ → SO₂, NO₂, зола, PM2.5
3. Сопоставь с WAQI.
4. Дай рекомендации (6 блоков): Активность, Защита, Дома, Группы риска, Питание, Витамины.

ФОРМАТ:
🏭 Источники на ветру:
• [Объект] — [расстояние] — возможны: [вещества]

📊 С учётом WAQI:
• [вещество] = [значение] — [комментарий]

━━━━━━━━━━━━━━━━━
🚶 Активность: ...
😷 Защита: ...
🏠 Дома: ...
👥 Группы риска: ...
🥗 Питание: ...
💊 Витамины: ...
💡 Вывод: ...

ВАЖНО: Только объекты из списка. Пиши "возможны".
Язык: {lang_name}
"""
    system_prompt = f"Эколог. Работай только с объектами из списка. Язык: {lang_name}."
    return call_deepseek(prompt, system_prompt, max_tokens=1200, temperature=0.2)

# ==========================================
# 12. Rule-based fallback
# ==========================================

def get_rule_based_recommendations(pollution_analysis, lang='ru'):
    risk = pollution_analysis.get('level_code', 1)
    activity_map = {
        'ru': {1: "✅ Можно бегать", 2: "🏃‍♂️ Можно гулять", 3: "⚠️ Лучше в зал", 4: "⛔ Только дома", 5: "🚫 Оставайтесь дома"},
        'kk': {1: "✅ Жүгіруге болады", 2: "🏃‍♂️ Серуендеуге", 3: "⚠️ Залға", 4: "⛔ Үйде", 5: "🚫 Үйде"},
        'en': {1: "✅ Can run", 2: "🏃‍♂️ Can walk", 3: "⚠️ Gym", 4: "⛔ Indoor", 5: "🚫 Stay home"}
    }
    activity = activity_map.get(lang, activity_map['ru']).get(risk, "✅ OK")
    return (
        f"🏭 На ветру: промышленных объектов не обнаружено\n\n"
        f"📊 С учётом WAQI: данные недоступны\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🚶 Активность: {activity}\n"
        f"😷 Защита: N95/KN95 если выйти\n"
        f"🏠 Дома: очиститель HEPA\n"
        f"👥 Группы риска: астматики, дети, пожилые\n"
        f"🥗 Питание: яблоки, брокколи, зелёный чай\n"
        f"💊 Витамины: C (1000 мг), D3 (2000 IU), Омега-3 (2000 мг)\n\n"
        f"💡 Вывод: следите за самочувствием"
    )

# ==========================================
# 13. H₂S ФОРМАТИРОВАНИЕ
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
    msg = f"🌍 **{t['report']}**\n───────────────────────\n\n"
    if air_data:
        msg += f"📊 **{t['air_quality']}:**\n"
        msg += f"• AQI: {air_data.get('aqi', t['no_data'])} | PM2.5: {air_data.get('pm25', t['no_data'])} | PM10: {air_data.get('pm10', t['no_data'])}\n"
        msg += f"• NO2: {air_data.get('no2', t['no_data'])} | SO2: {air_data.get('so2', t['no_data'])}\n"
        pm25 = air_data.get('pm25')
        if pm25 and pm25 > 25:
            if lang == 'ru': msg += f"⚠️ PM2.5 превышает норму ВОЗ\n"
            elif lang == 'kk': msg += f"⚠️ PM2.5 ДДҰ асып түсті\n"
            else: msg += f"⚠️ PM2.5 exceeds WHO limit\n"
        msg += f"{t['status']}: **{pollution_analysis['level_str']}**\n\n"
    else:
        msg += f"📊 **{t['air_quality']}:** {t['no_data']}\n\n"
    if weather:
        msg += f"💨 **{t['weather']}:** {weather['temp']}°C, {weather['humidity']}%, "
        msg += f"{t['wind']} {get_wind_direction_text(weather['wind_deg'], lang)} {weather['wind_speed']} м/с\n\n"
    if h2s_data is not None:
        msg += format_h2s_block(h2s_data, lang)
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


@bot.message_handler(commands=['test_osm'])
def test_osm_cmd(message):
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    if ADMIN_ID and message.chat.id != ADMIN_ID:
        return
    args = message.text.replace('/test_osm', '').strip()
    if not args or ',' not in args:
        bot.reply_to(message, "Формат: /test_osm 47.09,51.92")
        return
    try:
        lat, lon = map(float, args.split(','))
    except:
        bot.reply_to(message, "❌ Ошибка формата")
        return
    bot.reply_to(message, f"🔍 Ищу объекты вокруг {lat},{lon}...")
    objects = find_industrial_objects(lat, lon, radius_km=10)
    if not objects:
        bot.send_message(message.chat.id, "❌ Объектов не найдено")
        return
    text = f"📍 Найдено {len(objects)} объектов:\n\n"
    for obj in objects[:15]:
        text += f"• {obj['name'][:40]}\n  {obj['type']} — {obj['distance_km']} км, азимут {obj['bearing']}°\n\n"
    if len(text) > 4000:
        text = text[:4000]
    bot.send_message(message.chat.id, text)


@bot.message_handler(commands=['test_ecodata'])
def test_ecodata_cmd(message):
    """Тест ecodata.kz — /test_ecodata 47.09,51.92"""
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    if ADMIN_ID and message.chat.id != ADMIN_ID:
        return
    args = message.text.replace('/test_ecodata', '').strip()
    if not args or ',' not in args:
        bot.reply_to(message, "Формат: /test_ecodata 47.09,51.92")
        return
    try:
        lat, lon = map(float, args.split(','))
    except:
        bot.reply_to(message, "❌ Ошибка формата")
        return
    bot.reply_to(message, f"🌐 Проверяю ecodata.kz для {lat},{lon}...")
    result = ecodata_get_h2s(lat, lon)
    if result:
        bot.send_message(message.chat.id, f"✅ Найдено: {json.dumps(result, ensure_ascii=False, indent=2)}")
    else:
        bot.send_message(message.chat.id, "❌ ecodata: данных не получено\n\nПроверьте логи Render")


@bot.message_handler(commands=['lang'])
def change_language(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English')
    bot.send_message(message.chat.id, "Выберите язык:", reply_markup=markup)


@bot.message_handler(func=lambda m: m.text in ['🇷🇺 Русский', '🇰🇿 Қазақша', '🇬🇧 English'])
def set_language(message):
    if 'Русский' in message.text: lang = 'ru'
    elif 'Қазақша' in message.text: lang = 'kk'
    else: lang = 'en'
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
# ГЛАВНЫЙ ОБРАБОТЧИК ЛОКАЦИИ
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
        update_status({'ru': "📊 Получаю качество воздуха...", 'kk': "📊 Ауа сапасын...", 'en': "📊 Getting air quality..."}.get(lang))
        air_data, source_name = get_best_air_data(lat, lon)
        weather = get_weather(lat, lon)
        wind_deg = weather.get('wind_deg', 0) if weather else 0
        wind_dir_text = get_wind_direction_text(wind_deg, lang)
        wind_speed = weather.get('wind_speed', 0) if weather else 0
        update_status({'ru': "🛢 Ищу H₂S...", 'kk': "🛢 H₂S іздеймін...", 'en': "🛢 Searching H₂S..."}.get(lang))
        h2s_data = get_h2s_sync(lat, lon, air_data, lang, timeout=25)
        update_status({'ru': "🏭 Ищу объекты на ветру...", 'kk': "🏭 Жел жағындағы...", 'en': "🏭 Finding objects..."}.get(lang))
        all_objects = find_industrial_objects(lat, lon, radius_km=10)
        objects_on_wind = filter_on_wind(all_objects, wind_deg, tolerance=60)
        print(f"📍 Найдено {len(all_objects)}, на ветру {len(objects_on_wind)}", flush=True)
        if not objects_on_wind and all_objects:
            print(f"⚠️ На ветру пусто — беру 3 ближайших", flush=True)
            objects_on_wind = all_objects[:3]
        pollution_analysis = analyze_pollution(air_data, lang)
        update_status({'ru': "🤖 Анализирую через ИИ...", 'kk': "🤖 ИИ талдаймын...", 'en': "🤖 Analyzing..."}.get(lang))
        ai_analysis = get_ai_analysis(
            lat, lon, wind_deg, wind_dir_text, wind_speed,
            air_data, weather, h2s_data, objects_on_wind, lang
        )
        if not ai_analysis:
            ai_analysis = get_rule_based_recommendations(pollution_analysis, lang)
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
                remind = {'ru': "🔔 Проверьте качество воздуха!", 'kk': "🔔 Ауа сапасын тексеріңіз!", 'en': "🔔 Check air quality!"}.get(lang, "🔔 Check!")
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
    print(f"🔌 WEBSOCKET: {'✅' if WS_AVAILABLE else '❌'}", flush=True)
    print(f"🛢 AIRKZ API: {AIRKZ_API_URL}", flush=True)
    print(f"🌐 ECODATA.KZ: {ECODATA_BASE}", flush=True)
    print(f"🏭 OVERPASS: ✅", flush=True)
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
