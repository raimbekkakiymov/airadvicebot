import telebot
from telebot import types
import requests
import os
import math
from datetime import datetime

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)

# ============ ШАГ 1: ГЕОПОЗИЦИЯ ============

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Приветствие и запрос геолокации"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton("📍 Отправить местоположение", request_location=True))
    
    bot.send_message(
        message.chat.id,
        "🌍 **AirAdvice**\n\n"
        "Я анализирую качество воздуха рядом с вами и даю персональные рекомендации.\n\n"
        "Отправьте ваше местоположение:",
        reply_markup=markup,
        parse_mode='Markdown'
    )


@bot.message_handler(content_types=['location'])
def handle_location(message):
    """Обработка геолокации и запуск анализа"""
    lat = message.location.latitude
    lon = message.location.longitude
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    # ШАГ 2: Берем анализ качества воздуха
    air_data, source_name = get_best_air_data(lat, lon)
    
    # ШАГ 3: Берем погоду
    weather = get_weather(lat, lon)
    
    # ШАГ 4: Ищем объекты вокруг
    sources = get_nearby_sources(lat, lon)
    
    # ШАГ 5: Анализ ветра
    wind_analysis = analyze_wind_and_sources(weather, sources)
    
    # ШАГ 6: Анализ загрязнения
    pollution_analysis = analyze_pollution(air_data, wind_analysis)
    
    # ШАГ 7: Рекомендации DeepSeek
    recommendations = get_deepseek_recommendations(
        air_data, weather, wind_analysis, pollution_analysis
    )
    
    # ШАГ 8: Формируем ответ
    response = format_full_response(
        air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name
    )
    
    bot.send_message(message.chat.id, response, parse_mode='Markdown')


# ============ ШАГ 2: СБОР ДАННЫХ О ВОЗДУХЕ ============

def get_air_quality_waqi(lat, lon):
    """WAQI API (бесплатный demo token)"""
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
        print(f"WAQI error: {e}")
    return None


def get_air_quality_openaq(lat, lon):
    """OpenAQ API (бесплатный)"""
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
        print(f"OpenAQ error: {e}")
    return None


def get_best_air_data(lat, lon):
    """Пробуем все источники"""
    air_data = get_air_quality_waqi(lat, lon)
    if air_data and any(air_data.values()):
        return air_data, "WAQI"
    
    air_data = get_air_quality_openaq(lat, lon)
    if air_data and any(air_data.values()):
        return air_data, "OpenAQ"
    
    return None, None


# ============ ШАГ 3: ПОГОДА ============

def get_weather(lat, lon):
    """OpenWeatherMap API"""
    if not WEATHER_API_KEY:
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
        print(f"Weather error: {e}")
    return None


def get_wind_direction_text(deg):
    """Переводим градусы в текст"""
    directions = [
        (0, "Северный"), (45, "Северо-восточный"), (90, "Восточный"),
        (135, "Юго-восточный"), (180, "Южный"), (225, "Юго-западный"),
        (270, "Западный"), (315, "Северо-западный")
    ]
    closest = min(directions, key=lambda x: abs(x[0] - deg))
    return closest[1]


# ============ ШАГ 4: ПОИСК ОБЪЕКТОВ ============

def calculate_bearing(lat1, lon1, lat2, lon2):
    """Вычисляем направление от объекта к пользователю"""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.atan2(x, y)
    return (math.degrees(bearing) + 360) % 360


def get_nearby_sources(lat, lon):
    """OpenStreetMap: ищем заводы, свалки, ТЭЦ"""
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
            
            # Определяем тип
            if tags.get('landuse') == 'landfill':
                src_type = 'landfill'
                name = tags.get('name', 'Свалка')
            elif tags.get('power') == 'plant':
                src_type = 'power_plant'
                name = tags.get('name', 'ТЭЦ')
            elif tags.get('landuse') == 'industrial':
                src_type = 'industrial'
                name = tags.get('name', 'Промзона')
            elif tags.get('man_made') == 'works':
                src_type = 'chemical_plant'
                name = tags.get('name', 'Завод')
            else:
                continue
            
            bearing = calculate_bearing(obj_lat, obj_lon, lat, lon)
            
            sources.append({
                'type': src_type,
                'name': name,
                'bearing': bearing
            })
        
        return sources[:5]
    except Exception as e:
        print(f"OSM error: {e}")
        return []


# ============ ШАГ 5: АНАЛИЗ ВЕТРА ============

def analyze_wind_and_sources(weather, sources):
    """Определяем, какие объекты находятся с наветренной стороны"""
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
        wind_from = check_wind_from_source(wind_deg, src['bearing'])
        if wind_from:
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
    """Проверяем, дует ли ветер со стороны источника"""
    diff = abs(wind_deg - source_bearing)
    if diff > 180:
        diff = 360 - diff
    return diff < 45


# ============ ШАГ 6: АНАЛИЗ ЗАГРЯЗНЕНИЯ ============

def analyze_pollution(air_data, wind_analysis):
    """Делаем выводы о возможных загрязнителях"""
    if not air_data:
        return {
            'level': 'unknown',
            'emoji': '⚪',
            'elevated': [],
            'possible_pollutants': [],
            'pm25': 0, 'pm10': 0, 'no2': 0, 'so2': 0, 'co': 0, 'o3': 0
        }
    
    pm25 = air_data.get('pm25', 0)
    pm10 = air_data.get('pm10', 0)
    no2 = air_data.get('no2', 0)
    so2 = air_data.get('so2', 0)
    co = air_data.get('co', 0)
    o3 = air_data.get('o3', 0)
    
    # Определяем уровень
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
    
    # Определяем повышенные загрязнители
    elevated = []
    if pm25 > 35:
        elevated.append('PM2.5')
    if pm10 > 60:
        elevated.append('PM10')
    if no2 > 80:
        elevated.append('NO₂')
    if so2 > 50:
        elevated.append('SO₂')
    if co > 5:
        elevated.append('CO')
    if o3 > 100:
        elevated.append('O₃')
    
    # Сопутствующие элементы на основе повышенных загрязнителей
    possible_pollutants = []
    
    # От повышенных загрязнителей
    if 'PM2.5' in elevated:
        possible_pollutants.extend(['Сажа', 'Пыль', 'Тяжёлые металлы'])
    if 'NO₂' in elevated:
        possible_pollutants.extend(['Бенз(а)пирен', 'Угарный газ'])
    if 'SO₂' in elevated:
        possible_pollutants.extend(['Сульфаты', 'Кислотные аэрозоли'])
    if 'CO' in elevated:
        possible_pollutants.extend(['Летучие органические соединения'])
    
    # От объектов с наветренной стороны
    for src in wind_analysis.get('upwind_sources', []):
        if src['type'] == 'landfill':
            possible_pollutants.extend(['Метан', 'Сероводород', 'Аммиак'])
        elif src['type'] == 'power_plant':
            possible_pollutants.extend(['Зола', 'Диоксид серы', 'Оксиды азота'])
        elif src['type'] == 'chemical_plant':
            possible_pollutants.extend(['Фталаты', 'Винилхлорид', 'Микропластик'])
        elif src['type'] == 'industrial':
            possible_pollutants.extend(['Промышленная пыль', 'Летучие соединения'])
    
    possible_pollutants = list(set(possible_pollutants))
    
    return {
        'level': level,
        'emoji': emoji,
        'elevated': elevated,
        'possible_pollutants': possible_pollutants,
        'pm25': pm25,
        'pm10': pm10,
        'no2': no2,
        'so2': so2,
        'co': co,
        'o3': o3
    }


# ============ ШАГ 7: DEEPSEEK РЕКОМЕНДАЦИИ ============

def get_deepseek_recommendations(air_data, weather, wind_analysis, pollution_analysis):
    """Запрашиваем рекомендации у DeepSeek"""
    if not DEEPSEEK_API_KEY:
        print("DeepSeek API key not found")
        return None
    
    try:
        # Формируем контекст
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
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        body = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system",
                    "content": "Ты — эксперт по экологии и нутрициологии."
                },
                {
                    "role": "user",
                    "content": context
                }
            ],
            "temperature": 0.3,
            "max_tokens": 2000
        }
        
        response = requests.post(url, headers=headers, json=body, timeout=30)
        data = response.json()
        
        if 'choices' in data:
            return data['choices'][0]['message']['content']
        else:
            print(f"DeepSeek response: {data}")
    
    except Exception as e:
        print(f"DeepSeek error: {e}")
    
    return None


# ============ ШАГ 8: ФОРМИРОВАНИЕ ОТВЕТА ============

def format_full_response(air_data, weather, wind_analysis, pollution_analysis, recommendations, source_name):
    """Формируем полный ответ пользователю"""
    
    text = f"{pollution_analysis['emoji']} **Качество воздуха: {pollution_analysis['level']}**\n\n"
    
    # Показатели
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
    
    # Погода
    if weather:
        text += "💨 **Погода:**\n"
        text += f"• Температура: {weather['temp']:.0f}°C\n"
        text += f"• Влажность: {weather['humidity']}%\n"
        text += f"• Ветер: {wind_analysis['wind_direction_text']}, {wind_analysis['wind_speed']} м/с\n\n"
    else:
        text += "💨 **Погода:** Нет данных\n\n"
    
    # Объекты с наветренной стороны
    if wind_analysis.get('upwind_sources'):
        text += "🏭 **Объекты с наветренной стороны:**\n"
        for src in wind_analysis['upwind_sources']:
            text += f"• {src['name']}\n"
        text += "\n"
    
    # Сопутствующие элементы
    if pollution_analysis.get('possible_pollutants'):
        text += "⚠️ **Возможные сопутствующие элементы:**\n"
        text += ", ".join(pollution_analysis['possible_pollutants'])
        text += "\n\n"
    
    # Рекомендации ИИ
    if recommendations:
        text += f"{recommendations}\n\n"
    else:
        text += "⚠️ _Не удалось получить рекомендации ИИ. Проверьте API ключ._\n\n"
    
    text += f"📡 Данные: {source_name or 'Unknown'}\n"
    text += f"---\n_Обновлено автоматически_"
    
    return text


# ============ ЗАПУСК ============
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не найден")
        exit(1)
    
    print("✅ Бот запущен...")
    bot.polling(none_stop=True)
