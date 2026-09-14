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
    print("⚠️ cloudscraper/bs4 не установлены — H₂S парсинг отключён", flush=True)

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

PID_FILE = "bot.pid"
USER_LANG_FILE = "user_languages.json"
H2S_CACHE_FILE = "h2s_cache.json"
H2S_UPDATE_DAYS = 7

if not BOT_TOKEN:
    print("❌ ВНИМАНИЕ: BOT_TOKEN не установлен!", flush=True)
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
# 3. H₂S КЭШ
# ==========================================

def load_h2s_cache():
    global h2s_cache
    if os.path.exists(H2S_CACHE_FILE):
        try:
            with open(H2S_CACHE_FILE, 'r', encoding='utf-8') as f:
                h2s_cache = json.load(f)
            print(f"✅ Загружено {len(h2s_cache)} записей H₂S из кэша", flush=True)
        except Exception as e:
            logging.error(f"Ошибка загрузки H₂S кэша: {e}")
            h2s_cache = {}

def save_h2s_cache():
    try:
        with h2s_cache_lock:
            with open(H2S_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(h2s_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения H₂S кэша: {e}")

def get_cache_key(lat, lon):
    return f"{round(lat, 2)},{round(lon, 2)}"

def is_cache_fresh(key):
    """Проверка свежести кэша (поддерживает короткие интервалы)"""
    if key not in h2s_cache:
        return False
    
    entry = h2s_cache[key]
    
    if 'next_update' in entry:
        try:
            next_update = datetime.fromisoformat(entry['next_update'])
            return datetime.now() < next_update
        except:
            pass
    
    if 'updated' in entry:
        try:
            updated = datetime.fromisoformat(entry['updated'])
            return datetime.now() - updated < timedelta(days=H2S_UPDATE_DAYS)
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
    if 35 < lat < 72 and -25 < lon < 45:
        return 'eu'
    if 12 < lat < 42 and 30 < lon < 65:
        return 'me'
    return 'world'

# ==========================================
# 5. ПАРСЕРЫ H₂S
# ==========================================

def parse_iqair(lat, lon):
    """IQAir — глобальный источник"""
    if not PARSING_AVAILABLE:
        return None
    try:
        search_url = f"https://www.iqair.com/search?q={lat},{lon}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        r = scraper.get(search_url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, 'lxml')
        
        city_link = None
        for a in soup.find_all('a', href=True):
            if '/world-air-quality' in a['href'] or '/air-quality' in a['href']:
                city_link = a['href']
                break
        
        if not city_link:
            return None
        
        if not city_link.startswith('http'):
            city_link = 'https://www.iqair.com' + city_link
        
        r = scraper.get(city_link, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, 'lxml')
        
        h2s_value = None
        h2s_unit = None
        
        for row in soup.find_all('tr'):
            text = row.get_text().lower()
            if 'h2s' in text or 'hydrogen sulfide' in text or 'сероводород' in text:
                cells = row.find_all(['td', 'th'])
                for cell in cells:
                    match = re.search(r'([\d.,]+)\s*(µg/m³|mg/m³|ppb|ppm)', cell.get_text())
                    if match:
                        h2s_value = match.group(1).replace(',', '.')
                        h2s_unit = match.group(2)
                        break
                if h2s_value:
                    break
        
        if h2s_value:
            city_name = soup.find('h1')
            city_name = city_name.get_text(strip=True) if city_name else 'Unknown'
            print(f"✅ IQAir: H₂S={h2s_value} {h2s_unit}", flush=True)
            return {
                'h2s': h2s_value,
                'unit': h2s_unit or 'µg/m³',
                'source': 'IQAir',
                'station': city_name
            }
        return None
    except Exception as e:
        print(f"❌ IQAir parse error: {e}", flush=True)
        return None

def parse_aqicn(lat, lon):
    """AQICN — глобальный"""
    if not PARSING_AVAILABLE:
        return None
    try:
        url = f"https://aqicn.org/city/geo/{lat},{lon}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36',
        }
        
        r = scraper.get(url, headers=headers, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, 'lxml')
        
        h2s_value = None
        h2s_unit = None
        station_name = None
        
        for elem in soup.find_all(['div', 'td', 'tr', 'span']):
            text = elem.get_text().lower()
            if 'h2s' in text or 'сероводород' in text or 'hydrogen sulfide' in text:
                parent = elem.find_parent()
                if parent:
                    match = re.search(r'([\d.,]+)\s*(µg/m³|mg/m³|ppb)', parent.get_text())
                    if match:
                        h2s_value = match.group(1).replace(',', '.')
                        h2s_unit = match.group(2)
                        break
        
        h1 = soup.find('h1')
        if h1:
            station_name = h1.get_text(strip=True)
        
        if h2s_value:
            print(f"✅ AQICN: H₂S={h2s_value} {h2s_unit}", flush=True)
            return {
                'h2s': h2s_value,
                'unit': h2s_unit,
                'source': 'AQICN',
                'station': station_name or 'Unknown'
            }
        return None
    except Exception as e:
        print(f"❌ AQICN parse error: {e}", flush=True)
        return None

def parse_airkz(lat, lon):
    """AirKZ — Казахстан (быстрая версия с короткими таймаутами)"""
    if not PARSING_AVAILABLE:
        return None
    try:
        urls_to_try = [
            "https://airkz.kz/api/stations",
            "https://airkz.kz/",
        ]
        
        for url in urls_to_try:
            try:
                r = scraper.get(url, timeout=5)
                if r.status_code == 200:
                    print(f"✅ AirKZ: {url}", flush=True)
                    
                    if r.headers.get('content-type', '').startswith('application/json'):
                        try:
                            data = r.json()
                            result = find_h2s_in_json(data)
                            if result:
                                result['source'] = 'AirKZ'
                                result['station'] = result.get('station', 'AirKZ')
                                return result
                        except:
                            pass
                    
                    soup = BeautifulSoup(r.text, 'lxml')
                    result = find_h2s_in_html(soup)
                    if result:
                        result['source'] = 'AirKZ'
                        return result
            except Exception as e:
                print(f"⚠️ AirKZ {url}: {e}", flush=True)
                continue
        return None
    except Exception as e:
        print(f"❌ AirKZ parse error: {e}", flush=True)
        return None

def parse_kazhydromet(lat, lon):
    """Казгидромет — официальный источник Казахстана"""
    if not PARSING_AVAILABLE:
        return None
    try:
        url = "https://www.kazhydromet.kz/ru/ecology/monitoring"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'ru-RU,ru;q=0.9',
        }
        
        r = scraper.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, 'lxml')
        
        for table in soup.find_all('table'):
            table_text = table.get_text().lower()
            
            if 'сероводород' in table_text or 'h2s' in table_text:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        city = cells[0].get_text(strip=True)
                        for cell in cells[1:]:
                            cell_text = cell.get_text(strip=True)
                            if re.search(r'[\d.,]+', cell_text):
                                print(f"✅ Казгидромет: {city} = {cell_text}", flush=True)
                                return {
                                    'h2s': cell_text,
                                    'unit': 'mg/m³',
                                    'source': 'Казгидромет',
                                    'station': city
                                }
        return None
    except Exception as e:
        print(f"❌ Казгидромет parse error: {e}", flush=True)
        return None

def parse_airnow(lat, lon):
    """AirNow — США"""
    if not PARSING_AVAILABLE:
        return None
    try:
        url = f"https://www.airnow.gov/?latitude={lat}&longitude={lon}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36',
        }
        
        r = scraper.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, 'lxml')
        
        for elem in soup.find_all(text=re.compile(r'H2S|Hydrogen Sulfide', re.I)):
            parent = elem.find_parent()
            if parent:
                match = re.search(r'([\d.]+)', parent.get_text())
                if match:
                    return {
                        'h2s': match.group(1),
                        'unit': 'AQI',
                        'source': 'AirNow',
                        'station': 'US EPA'
                    }
        return None
    except Exception as e:
        print(f"❌ AirNow parse error: {e}", flush=True)
        return None

def find_h2s_in_json(data):
    if isinstance(data, dict):
        for key, value in data.items():
            if 'h2s' in str(key).lower() or 'сероводород' in str(key).lower():
                return {'h2s': value, 'unit': 'µg/m³'}
            result = find_h2s_in_json(value)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_h2s_in_json(item)
            if result:
                return result
    return None

def find_h2s_in_html(soup):
    for elem in soup.find_all(text=re.compile(r'h2s|сероводород|hydrogen sulfide', re.I)):
        parent = elem.find_parent()
        if parent:
            match = re.search(r'([\d.,]+)\s*(µg/m³|mg/m³|ppb|ppm)', parent.get_text())
            if match:
                return {
                    'h2s': match.group(1).replace(',', '.'),
                    'unit': match.group(2)
                }
    return None

def estimate_h2s_indirect(air_data):
    """Косвенная оценка H₂S по SO2"""
    if not air_data:
        return None
    
    so2 = air_data.get('so2')
    if not isinstance(so2, (int, float)):
        return None
    
    if so2 > 50:
        return {
            'h2s': 'Повышен (косвенно)',
            'unit': f'SO2={so2} µg/m³',
            'source': 'Оценка по SO2',
            'station': 'косвенная оценка'
        }
    elif so2 > 20:
        return {
            'h2s': 'Умеренный (косвенно)',
            'unit': f'SO2={so2} µg/m³',
            'source': 'Оценка по SO2',
            'station': 'косвенная оценка'
        }
    return None

def _parse_with_timeout(parser, lat, lon, timeout=15):
    """Запуск парсера с жёстким таймаутом"""
    result = [None]
    exception = [None]
    
    def target():
        try:
            result[0] = parser(lat, lon)
        except Exception as e:
            exception[0] = e
    
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    
    if t.is_alive():
        print(f"⏱ {parser.__name__}: таймаут {timeout}с", flush=True)
        return None
    
    if exception[0]:
        print(f"⚠️ {parser.__name__}: {exception[0]}", flush=True)
        return None
    
    return result[0]


def _parse_h2s_all_sources(lat, lon, air_data=None):
    """Парсинг по всем источникам с таймаутами"""
    if not PARSING_AVAILABLE:
        return estimate_h2s_indirect(air_data)
    
    region = detect_region(lat, lon)
    
    # Региональные источники (10-12 сек каждый)
    if region == 'kz':
        for parser in [parse_airkz, parse_kazhydromet]:
            result = _parse_with_timeout(parser, lat, lon, timeout=10)
            if result:
                return result
    
    if region == 'us':
        result = _parse_with_timeout(parse_airnow, lat, lon, timeout=10)
        if result:
            return result
    
    # Глобальные источники (12 сек каждый)
    for parser in [parse_iqair, parse_aqicn]:
        result = _parse_with_timeout(parser, lat, lon, timeout=12)
        if result:
            return result
    
    return estimate_h2s_indirect(air_data)


def _parse_and_cache(key, lat, lon, air_data, lang):
    """Парсинг + сохранение в кэш"""
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
                'failures': 0
            }
            print(f"✅ H₂S сохранён: {result['source']}", flush=True)
        else:
            failures = h2s_cache.get(key, {}).get('failures', 0) + 1
            h2s_cache[key] = {
                'h2s': None,
                'unit': None,
                'source': 'Нет данных',
                'station': None,
                'updated': now.isoformat(),
                'next_update': (now + timedelta(days=H2S_UPDATE_DAYS)).isoformat(),
                'failures': failures
            }
            print(f"⚠️ H₂S не найден (попытка {failures})", flush=True)
        
        save_h2s_cache()
        return h2s_cache.get(key)
    except Exception as e:
        logging.error(f"Ошибка парсинга H₂S: {e}")
        return None


def get_h2s_sync(lat, lon, air_data=None, lang='ru', timeout=30):
    """
    Синхронное получение H₂S с ограничением по времени.
    Ждём парсинга, но не более timeout секунд.
    """
    if not PARSING_AVAILABLE:
        return estimate_h2s_indirect(air_data)
    
    key = get_cache_key(lat, lon)
    
    # Свежий кэш
    if is_cache_fresh(key):
        entry = h2s_cache[key]
        try:
            age_days = (datetime.now() - datetime.fromisoformat(entry['updated'])).days
            print(f"📦 H₂S из кэша (возраст: {age_days} дн.)", flush=True)
        except:
            pass
        return entry
    
    # Нужен парсинг — ждём с таймаутом
    print(f"🔄 H₂S: парсинг с таймаутом {timeout}с...", flush=True)
    
    result_holder = [None]
    done_event = threading.Event()
    
    def parse_worker():
        try:
            result_holder[0] = _parse_and_cache(key, lat, lon, air_data, lang)
        except Exception as e:
            logging.error(f"H₂S parse error: {e}")
            result_holder[0] = None
        finally:
            done_event.set()
    
    worker = threading.Thread(target=parse_worker, daemon=True)
    worker.start()
    
    finished = done_event.wait(timeout=timeout)
    
    if finished and result_holder[0]:
        print(f"✅ H₂S готов: {result_holder[0].get('source', 'N/A')}", flush=True)
        return result_holder[0]
    
    # Таймаут — отдаём косвенную оценку
    print(f"⏱ H₂S таймаут, отдаю косвенную оценку", flush=True)
    
    estimate = estimate_h2s_indirect(air_data)
    if estimate:
        estimate['updated'] = datetime.now().isoformat()
        estimate['source'] = estimate.get('source', 'Оценка') + ' (таймаут парсинга)'
    
    # Сохраняем в кэш, чтобы следующий запрос не парсил снова (через 5 минут попробуем заново)
    if key not in h2s_cache:
        now = datetime.now()
        h2s_cache[key] = {
            'h2s': estimate.get('h2s') if estimate else None,
            'unit': estimate.get('unit') if estimate else None,
            'source': 'Таймаут — нужен повторный парсинг',
            'station': None,
            'updated': now.isoformat(),
            'next_update': (now + timedelta(minutes=5)).isoformat(),
            'failures': 0
        }
        save_h2s_cache()
    
    return estimate

# ==========================================
# 6. DEEPSEEK API
# ==========================================

def call_deepseek(prompt, system_prompt=None, max_tokens=1000, temperature=0.7):
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
# 7. МАТЕМАТИКА
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
# 8. ВНЕШНИЕ API
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
# 9. АНАЛИЗ
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
# 10. DEEPSEEK АНАЛИЗ ИСТОЧНИКОВ
# ==========================================

def get_ai_source_analysis(lat, lon, wind_deg, wind_dir_text, air_data, h2s_data, lang='ru'):
    if not DEEPSEEK_API_KEY:
        return None
    
    lang_name = {'ru': 'Русский', 'kk': 'Казахский', 'en': 'English'}.get(lang, 'Русский')
    
    aqi = air_data.get('aqi', 'N/A') if air_data else 'N/A'
    pm25 = air_data.get('pm25', 'N/A') if air_data else 'N/A'
    so2 = air_data.get('so2', 'N/A') if air_data else 'N/A'
    no2 = air_data.get('no2', 'N/A') if air_data else 'N/A'
    
    h2s_info = ""
    if h2s_data and h2s_data.get('h2s'):
        h2s_info = f"• H₂S: {h2s_data['h2s']} {h2s_data.get('unit', '')} (источник: {h2s_data.get('source', 'N/A')})\n"
    
    prompt = (
        f"Ты эксперт по экологии. ОТВЕЧАЙ КОРОТКО (2-3 предложения).\n\n"
        f"Данные: AQI={aqi}, PM2.5={pm25}, SO2={so2}, NO2={no2}\n"
        f"{h2s_info}"
        f"Ветер: {wind_dir_text}\n"
        f"Координаты: {lat}, {lon}\n\n"
        f"ФОРМАТ (строго):\n"
        f"🏭 Источники: [1-2 вероятных источника]\n"
        f"⚠️ Элементы: [2-3 элемента]\n\n"
        f"Язык: {lang_name}"
    )
    
    system_prompt = f"Отвечай кратко, без лишних слов. Язык: {lang_name}"
    
    print("🏭 Короткий анализ источников...", flush=True)
    return call_deepseek(prompt, system_prompt, max_tokens=250, temperature=0.3)

# ==========================================
# 11. DEEPSEEK РЕКОМЕНДАЦИИ
# ==========================================

def get_ai_recommendations(air_data, weather, pollution_analysis, lang='ru', source_analysis=None, h2s_data=None):
    if not DEEPSEEK_API_KEY:
        return get_rule_based_recommendations(pollution_analysis, lang)
    
    wind_dir = get_wind_direction_text(weather['wind_deg'], lang) if weather else 'N/A'
    lang_name = {'ru': 'Русский', 'kk': 'Казахский', 'en': 'English'}.get(lang, 'Русский')
    
    aqi = air_data.get('aqi', 'N/A') if air_data else 'N/A'
    pm25 = air_data.get('pm25', 'N/A') if air_data else 'N/A'
    so2 = air_data.get('so2', 'N/A') if air_data else 'N/A'
    no2 = air_data.get('no2', 'N/A') if air_data else 'N/A'
    temp = weather.get('temp', 'N/A') if weather else 'N/A'
    
    h2s_info = ""
    if h2s_data and h2s_data.get('h2s'):
        h2s_info = f"• H₂S: {h2s_data['h2s']} {h2s_data.get('unit', '')}\n"
    
    prompt = f"""Ты эксперт по нутрициологии и здоровью.

ВОЗДУХ: AQI={aqi}, PM2.5={pm25}, SO2={so2}, NO2={no2}
{h2s_info}ПОГОДА: {temp}°C, ветер {wind_dir}

АНАЛИЗ ИСТОЧНИКОВ: {source_analysis if source_analysis else 'Нет данных'}

ДАЙ РЕКОМЕНДАЦИИ:

1. 🏃‍♂️ АКТИВНОСТЬ
[1 предложение]

2. 🥗 ПИТАНИЕ — объясни почему каждый продукт:
• Яблоко — [пектин связывает токсины]
• Морковь — [бета-каротин для легких]
• Зеленый чай — [катехины-антиоксиданты]
• Оливковое масло — [витамин E, омега-9]
• Брокколи — [сульфорафан детоксикация]
• [1-2 продукта под ситуацию]

3. 💧 ВОДА
[сколько + что добавить]

4. 💊 ВИТАМИНЫ — объясни почему:
• Витамин C — [антиоксидант]
• Омега-3 — [противовоспалительное]
• Магний — [расслабляет бронхи]
• Витамин D — [иммунитет]
• Цинк — [защита клеток]

ФОРМАТ: Каждый пункт с "—" и кратким объяснением.
Язык: {lang_name}"""
    
    system_prompt = f"Объясняй пользу каждого продукта. Язык: {lang_name}"
    
    print("🤖 Детальные рекомендации...", flush=True)
    result = call_deepseek(prompt, system_prompt, max_tokens=900, temperature=0.4)
    
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
# 12. ФОРМАТИРОВАНИЕ H₂S
# ==========================================

def format_h2s_block(h2s_data, lang='ru'):
    titles = {
        'ru': {
            'title': "🛢 СЕРОВОДОРОД (H₂S)",
            'value': "Значение", 'station': "Станция", 'source': "Источник",
            'updated': "Обновлено", 'no_data': "Прямых датчиков рядом нет",
            'estimate': "Косвенная оценка по SO₂",
            'norm': "Норма ВОЗ: 0.15 мг/м³ (разово)",
            'days_ago': "дн. назад", 'today': "сегодня", 'yesterday': "вчера"
        },
        'kk': {
            'title': "🛢 КҮКІРТСУТЕК (H₂S)",
            'value': "Мәні", 'station': "Станция", 'source': "Дереккөз",
            'updated': "Жаңартылды", 'no_data': "Жақын жерде датчиктер жоқ",
            'estimate': "SO₂ арқылы бағалау",
            'norm': "ДДҰ нормасы: 0.15 мг/м³",
            'days_ago': "күн бұрын", 'today': "бүгін", 'yesterday': "кеше"
        },
        'en': {
            'title': "🛢 HYDROGEN SULFIDE (H₂S)",
            'value': "Value", 'station': "Station", 'source': "Source",
            'updated': "Updated", 'no_data': "No direct sensors nearby",
            'estimate': "Indirect estimate via SO₂",
            'norm': "WHO limit: 0.15 mg/m³",
            'days_ago': "days ago", 'today': "today", 'yesterday': "yesterday"
        }
    }
    
    t = titles.get(lang, titles['ru'])
    msg = f"**{t['title']}:**\n"
    
    if h2s_data and h2s_data.get('h2s'):
        msg += f"• {t['value']}: {h2s_data['h2s']} {h2s_data.get('unit', '')}\n"
        if h2s_data.get('station'):
            msg += f"• {t['station']}: {h2s_data['station']}\n"
        msg += f"• {t['source']}: {h2s_data['source']}\n"
        
        if h2s_data.get('updated'):
            try:
                updated = datetime.fromisoformat(h2s_data['updated'])
                days = (datetime.now() - updated).days
                if days == 0:
                    age = t['today']
                elif days == 1:
                    age = t['yesterday']
                else:
                    age = f"{days} {t['days_ago']}"
                msg += f"• {t['updated']}: {age}\n"
            except:
                pass
        
        msg += f"• {t['norm']}\n\n"
    
    elif h2s_data and h2s_data.get('source', '').startswith('Оценка'):
        msg += f"• {t['estimate']}: {h2s_data['h2s']}\n"
        msg += f"• {t['norm']}\n\n"
    
    else:
        msg += f"• {t['no_data']}\n"
        msg += f"• {t['norm']}\n\n"
    
    return msg

# ==========================================
# 13. ФОРМАТИРОВАНИЕ ОТЧЁТА
# ==========================================

def format_full_response(air_data, weather, pollution_analysis, recommendations, source_name, lang='ru', ai_source_analysis=None, h2s_data=None):
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
    
    # Блок H₂S
    if h2s_data is not None:
        msg += format_h2s_block(h2s_data, lang)
    
    if ai_source_analysis:
        msg += f"{ai_source_analysis}\n\n"
    
    msg += "───────────────────────\n"
    msg += f"{recommendations}"
    msg += f"\n\n📡 _{t['source']}: {source_name}_"
    msg += f"\n🤖 _AI: DeepSeek_"
    
    return msg

def safe_send_message(chat_id, text):
    max_length = 4000
    
    lang = user_languages.get(str(chat_id), 'ru')
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    location_text = {
        'ru': "📍 Новая локация", 'kk': "📍 Жаңа орын", 'en': "📍 New location"
    }.get(lang, "📍 New location")
    
    refresh_text = {
        'ru': "🔄 Обновить", 'kk': "🔄 Жаңарту", 'en': "🔄 Refresh"
    }.get(lang, "🔄 Refresh")
    
    markup.add(types.KeyboardButton(location_text, request_location=True))
    markup.add(types.KeyboardButton(refresh_text))
    
    try:
        if len(text) <= max_length:
            try:
                bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=markup)
            except:
                bot.send_message(chat_id, text, reply_markup=markup)
            return
        
        print(f"📤 Разбиваю длинное сообщение ({len(text)} символов)...", flush=True)
        
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
        
        print(f"✅ Отправлено {len(parts)} частей", flush=True)
    except Exception as e:
        logging.error(f"Send error: {e}")

# ==========================================
# 14. ОБРАБОТЧИКИ
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
    
    location_text = {
        'ru': "📍 Отправить локацию", 'kk': "📍 Орынды жіберу", 'en': "📍 Send Location"
    }.get(lang, "📍 Send Location")
    markup.add(types.KeyboardButton(location_text, request_location=True))
    
    start_text = {
        'ru': "🚀 Старт", 'kk': "🚀 Бастау", 'en': "🚀 Start"
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
            "• Показывать уровень H₂S\n"
            "• Давать рекомендации по здоровью\n\n"
            "📍 Отправьте вашу геолокацию, чтобы начать!"
        ),
        'kk': (
            "👋 Қош келдіңіз!\n\n"
            "Мен сіздің аймағыңыздағы ауа сапасын білуге көмектесемін.\n\n"
            "📊 Не істей аламын:\n"
            "• AQI және ластаушыларды көрсету\n"
            "• Ластану көздерін анықтау\n"
            "• H₂S деңгейін көрсету\n"
            "• Денсаулық бойынша ұсыныстар беру\n\n"
            "📍 Бастау үшін геолокацияңызды жіберіңіз!"
        ),
        'en': (
            "👋 Welcome!\n\n"
            "I'll help you check air quality in your area.\n\n"
            "📊 What I can do:\n"
            "• Show AQI and pollutants\n"
            "• Identify pollution sources\n"
            "• Show H₂S levels\n"
            "• Give health recommendations\n\n"
            "📍 Send your location to start!"
        )
    }.get(lang, "Welcome!")
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    location_text = {
        'ru': "📍 Отправить локацию", 'kk': "📍 Орынды жіберу", 'en': "📍 Send Location"
    }.get(lang, "📍 Send Location")
    markup.add(types.KeyboardButton(location_text, request_location=True))
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ['🔄 Обновить', '🔄 Жаңарту', '🔄 Refresh'])
def refresh_data(message):
    user_id = str(message.chat.id)
    lang = user_languages.get(user_id, 'ru')
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    location_text = {
        'ru': "📍 Отправить локацию", 'kk': "📍 Орынды жіберу", 'en': "📍 Send Location"
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
    
    # Сообщения ожидания
    wait_msgs = {
        'ru': "⏳ Обрабатываю данные, ожидайте...\n\n_Обычно занимает 5-15 секунд_",
        'kk': "⏳ Деректерді өңдеп жатырмын, күте тұрыңыз...\n\n_Әдетте 5-15 секунд алады_",
        'en': "⏳ Processing data, please wait...\n\n_Usually takes 5-15 seconds_"
    }
    
    # Шаг 1: Статусное сообщение
    try:
        status_msg = bot.send_message(
            message.chat.id,
            wait_msgs.get(lang, wait_msgs['ru']),
            parse_mode='Markdown'
        )
        status_msg_id = status_msg.message_id
    except Exception as e:
        logging.error(f"Status msg error: {e}")
        status_msg_id = None
    
    def update_status(text):
        if status_msg_id:
            try:
                bot.edit_message_text(
                    text,
                    chat_id=message.chat.id,
                    message_id=status_msg_id,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logging.error(f"Edit status error: {e}")
    
    try:
        # Шаг 2: Качество воздуха
        update_status({
            'ru': "📊 Получаю качество воздуха...",
            'kk': "📊 Ауа сапасын аламын...",
            'en': "📊 Getting air quality..."
        }.get(lang))
        
        air_data, source_name = get_best_air_data(lat, lon)
        weather = get_weather(lat, lon)
        
        wind_deg = weather.get('wind_deg', 0) if weather else 0
        wind_dir_text = get_wind_direction_text(wind_deg, lang)
        
        # Шаг 3: H₂S — синхронно с таймаутом 30 сек
        update_status({
            'ru': "🛢 Ищу данные о H₂S...\n\n_Парсинг источников_",
            'kk': "🛢 H₂S деректерін іздеймін...\n\n_Дереккөздерді талдау_",
            'en': "🛢 Searching H₂S data...\n\n_Parsing sources_"
        }.get(lang))
        
        h2s_data = get_h2s_sync(lat, lon, air_data, lang, timeout=30)
        
        # Шаг 4: Анализ источников через DeepSeek
        update_status({
            'ru': "🤖 Анализирую источники через ИИ...",
            'kk': "🤖 ИИ арқылы көздерді талдаймын...",
            'en': "🤖 Analyzing sources with AI..."
        }.get(lang))
        
        ai_source_analysis = get_ai_source_analysis(
            lat, lon, wind_deg, wind_dir_text, air_data, h2s_data, lang
        )
        
        pollution_analysis = analyze_pollution(air_data, lang)
        
        # Шаг 5: Рекомендации
        update_status({
            'ru': "🥗 Готовлю рекомендации...",
            'kk': "🥗 Ұсыныстар дайындаймын...",
            'en': "🥗 Preparing recommendations..."
        }.get(lang))
        
        recommendations = get_ai_recommendations(
            air_data, weather, pollution_analysis, lang,
            ai_source_analysis, h2s_data
        )
        
        # Шаг 6: Удаляем статус и отправляем отчёт
        if status_msg_id:
            try:
                bot.delete_message(message.chat.id, status_msg_id)
            except:
                pass
        
        response = format_full_response(
            air_data, weather, pollution_analysis,
            recommendations, source_name, lang,
            ai_source_analysis, h2s_data
        )
        
        safe_send_message(message.chat.id, response)
        
    except Exception as e:
        logging.error(f"Handle location error: {e}")
        if status_msg_id:
            try:
                bot.delete_message(message.chat.id, status_msg_id)
            except:
                pass
        
        error_msg = {
            'ru': "❌ Ошибка обработки. Попробуйте ещё раз.",
            'kk': "❌ Өңдеу қатесі. Қайталап көріңіз.",
            'en': "❌ Processing error. Try again."
        }.get(lang, "❌ Error")
        
        bot.send_message(message.chat.id, error_msg)

# ==========================================
# 15. АДМИН-КОМАНДЫ
# ==========================================

@bot.message_handler(commands=['refresh_h2s'])
def refresh_h2s_command(message):
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    if ADMIN_ID and message.chat.id != ADMIN_ID:
        return
    
    count = len(h2s_cache)
    h2s_cache.clear()
    save_h2s_cache()
    
    bot.reply_to(message, f"✅ Кэш H₂S очищен ({count} записей). Следующий запрос обновит данные.")

@bot.message_handler(commands=['stats_h2s'])
def stats_h2s(message):
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    if ADMIN_ID and message.chat.id != ADMIN_ID:
        return
    
    total = len(h2s_cache)
    with_data = sum(1 for v in h2s_cache.values() if v.get('h2s'))
    sources = {}
    for v in h2s_cache.values():
        src = v.get('source', 'Unknown')
        sources[src] = sources.get(src, 0) + 1
    
    text = f"📊 H₂S кэш:\n"
    text += f"• Всего записей: {total}\n"
    text += f"• С данными: {with_data}\n"
    text += f"• Источники:\n"
    for src, count in sources.items():
        text += f"  — {src}: {count}\n"
    
    bot.reply_to(message, text)

# ==========================================
# 16. ФОНОВЫЕ ЗАДАЧИ
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
# 17. ЗАПУСК
# ==========================================

if __name__ == '__main__':
    print("=" * 50, flush=True)
    print("🚀 ЗАПУСК БОТА...", flush=True)
    print(f"🔑 BOT_TOKEN: {'✅' if BOT_TOKEN and BOT_TOKEN != 'DUMMY_TOKEN' else '❌'}", flush=True)
    print(f"🤖 DEEPSEEK: {'✅' if DEEPSEEK_API_KEY else '❌'}", flush=True)
    print(f"🌤 WEATHER: {'✅' if WEATHER_API_KEY else '❌'}", flush=True)
    print(f"🕷 PARSING: {'✅' if PARSING_AVAILABLE else '❌'}", flush=True)
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
