from qgis.core import QgsField, QgsFeature, QgsVectorLayer
from qgis.PyQt.QtCore import QVariant
import urllib.request, json, time, re

layer = iface.activeLayer()

#теги для транспортного моделирования
TAGS_TO_FETCH = {
    'name': 'string',        # Название дороги
    'maxspeed': 'string',    # Максимальная скорость (может быть '50', 'walk', 'none')
    'lanes': 'string',       # Количество полос (может быть '2', '1', '2;3')
    'oneway': 'string',      # Одностороннее движение ('yes', 'no', '-1')
    'highway': 'string',     # Тип дороги (уже есть, но на всякий случай)
    'bridge': 'string',      # Мост ('yes', 'no')
    'tunnel': 'string',      # Тоннель ('yes', 'no')
    'surface': 'string',     # Покрытие ('asphalt', 'concrete', 'gravel')
    'lit': 'string',         # Освещение ('yes', 'no')
    'access': 'string',      # Ограничения доступа ('private', 'no')
    'width': 'string',       # Ширина дороги в метрах
    'junction': 'string',    # Тип пересечения ('roundabout', 'circular')
    'smoothness': 'string'   # Качество покрытия ('excellent', 'good', 'bad')
}

#все osm_id из слоя
osm_ids = []
for feature in layer.getFeatures():
    osm_id = feature['osm_id']
    if osm_id:
        osm_ids.append(str(osm_id))

print(f"Найдено {len(osm_ids)} объектов с OSM ID")

#разделение на группы
chunk_size = 300
id_chunks = [osm_ids[i:i + chunk_size] for i in range(0, len(osm_ids), chunk_size)]

#словарь для хранения всех тегов
osm_to_tags = {}

#каждую группу обрабатываем
for chunk_num, id_chunk in enumerate(id_chunks):
    print(f"запрос {chunk_num+1}/{len(id_chunks)} ({len(id_chunk)} объектов)")
    
    #один большой запрос
    id_list = ','.join(id_chunk)
    
    #все теги для каждого way
    query = f"""
    [out:json][timeout:90];
    way(id:{id_list});
    out tags;
    """
    
    try:
        req = urllib.request.Request(
            "http://overpass-api.de/api/interpreter", #обращение к overpass
            data=query.encode('utf-8'),
            headers={'User-Agent': 'QGIS-Script/1.0'}
        )
        
        response = urllib.request.urlopen(req, timeout=120) #лимит по времени, чтобы н висло
        data = json.load(response)
        
        #обработка ответа
        for element in data.get('elements', []):
            element_id = element['id']
            osm_to_tags[element_id] = {}
            
            if 'tags' in element:
                #только нужные теги
                for tag_name in TAGS_TO_FETCH.keys():
                    if tag_name in element['tags']:
                        osm_to_tags[element_id][tag_name] = element['tags'][tag_name]
        
        print(f"{len(data.get('elements', []))} элементов")
        
        #опять лимит против багов
        time.sleep(7)
        
    except urllib.error.HTTPError as e:
        if e.code == 429:  # Too Many Requests
            print(f"достигнут лимит")
            time.sleep(30)
        else:
            print(f"ошибка HTTP {e.code}: {e.reason}")
            time.sleep(10)
    except Exception as e:
        print(f"ошибка: {str(e)[:100]}")
        time.sleep(5)
        continue

print(f"\nтеги для {len(osm_to_tags)} объектов")

#отсутствующие поля если их нет
fields_to_add = []
provider = layer.dataProvider()

#+ необходимые поля
for field_name, field_type in TAGS_TO_FETCH.items():
    if layer.fields().indexFromName(field_name) == -1:
        if field_type == 'string':
            #лимит длины
            length = 50
            if field_name == 'name':
                length = 100
            elif field_name in ['maxspeed', 'access']:
                length = 20
            
            new_field = QgsField(field_name, QVariant.String, len=length)
            fields_to_add.append(new_field)
            print(f"поле добавлено: {field_name} (строка, {length} симв.)")
        elif field_type == 'integer':
            new_field = QgsField(field_name, QVariant.Int)
            fields_to_add.append(new_field)
            print(f"=поле добавлено: {field_name} (целое число)")

if fields_to_add:
    provider.addAttributes(fields_to_add)
    layer.updateFields()
    print(f"всего добавили {len(fields_to_add)}")

#доп. поля
calculated_fields = {
    'lanes_numeric': ('Количество полос (число)', QVariant.Int),
    'maxspeed_numeric': ('Макс. скорость (число)', QVariant.Int),
    'is_oneway': ('Односторонняя', QVariant.Int),  # 0/1 для вычислений
    'road_category': ('Категория дороги', QVariant.String, 20),
}

for field_name, (description, field_type, *args) in calculated_fields.items():
    if layer.fields().indexFromName(field_name) == -1: #проверка существования
        if field_type == QVariant.String:
            length = args[0] if args else 50 
            new_field = QgsField(field_name, field_type, len=length)
        else:
            new_field = QgsField(field_name, field_type)
        provider.addAttributes([new_field]) #список с одним полем
        print(f"поле добавлено: {field_name}")

layer.updateFields()

#добавляю слой данными из osm
layer.startEditing()
updated_count = 0
calculated_count = 0

#для преобразования значений
def parse_lanes(lanes_str):
    if not lanes_str:
        return None  
    #- пробелы и + разделители
    lanes_str = str(lanes_str).strip()
    #+ первое число
    match = re.search(r'(\d+)', lanes_str)
    if match:
        return int(match.group(1))
    #если типа '1|2' берем среднее
    if '|' in lanes_str:
        parts = lanes_str.split('|')
        numbers = [int(p) for p in parts if p.isdigit()]
        if numbers:
            return sum(numbers) // len(numbers)
    
    return None

def parse_maxspeed(speed_str):
    if not speed_str:
        return None
    
    speed_str = str(speed_str).lower().strip()
    
    #- "km/h", "kph" и т.д.
    speed_str = re.sub(r'[^\d.]', '', speed_str) #простенькая регулярка
    
    if speed_str and speed_str.replace('.', '').isdigit():
        return int(float(speed_str))
    
    return None

def categorize_road(highway_value):
    if not highway_value:
        return 'unknown'
    
    highway_value = highway_value.lower()
    
    if highway_value in ['motorway', 'motorway_link']:
        return 'motorway'
    elif highway_value in ['trunk', 'trunk_link']:
        return 'trunk'
    elif highway_value in ['primary', 'primary_link']:
        return 'primary'
    elif highway_value in ['secondary', 'secondary_link']:
        return 'secondary'
    elif highway_value in ['tertiary', 'tertiary_link']:
        return 'tertiary'
    elif highway_value in ['unclassified', 'residential', 'living_street']:
        return 'local'
    elif highway_value in ['service', 'track', 'path']:
        return 'service'
    else:
        return 'other'

for feature in layer.getFeatures():
    osm_id = feature['osm_id'] 
    if osm_id and osm_id in osm_to_tags: #айди есть в словаре
        updated = False
        tags = osm_to_tags[osm_id] 
        #обновляем основные поля из osm
        for tag_name in TAGS_TO_FETCH.keys():
            if tag_name in tags and tags[tag_name] is not None:
                current_value = feature[tag_name]
                new_value = tags[tag_name]
                #только если значение изменилось
                if current_value != new_value and (not current_value or current_value != new_value):
                    feature[tag_name] = new_value
                    updated = True
        
        #доп.поля
        if updated:
            #количество полос
            lanes_str = feature['lanes'] if 'lanes' in feature.attributes() else None
            lanes_numeric = parse_lanes(lanes_str)
            if lanes_numeric is not None:
                feature['lanes_numeric'] = lanes_numeric
            
            #макс скорость
            maxspeed_str = feature['maxspeed'] if 'maxspeed' in feature.attributes() else None
            maxspeed_numeric = parse_maxspeed(maxspeed_str)
            if maxspeed_numeric is not None:
                feature['maxspeed_numeric'] = maxspeed_numeric
            
            #стороны движения (0/1)
            oneway_val = feature['oneway'] if 'oneway' in feature.attributes() else None
            if oneway_val:
                feature['is_oneway'] = 1 if str(oneway_val).lower() == 'yes' else 0
            
            #категория дороги
            highway_val = feature['highway'] if 'highway' in feature.attributes() else None
            if highway_val:
                feature['road_category'] = categorize_road(highway_val)
            
            layer.updateFeature(feature)
            updated_count += 1
            calculated_count += 1
            
            if updated_count % 500 == 0:
                print(f"обновлено {updated_count}, рассчитано {calculated_count} полей")

layer.commitChanges()

#стата по заполненности полей
print("\n" + "="*60)
print("статистика заполненности полей:")
print("="*60)

field_stats = {}
for field_name in list(TAGS_TO_FETCH.keys()) + list(calculated_fields.keys()):
    if layer.fields().indexFromName(field_name) != -1:
        non_null = 0
        total = 0
        
        #первые 1000 объектов
        sample_size = min(1000, layer.featureCount())
        features = list(layer.getFeatures())[:sample_size]
        
        for feature in features:
            total += 1
            if feature[field_name] and str(feature[field_name]).strip():
                non_null += 1
        
        if total > 0:
            percentage = (non_null / total) * 100
            field_stats[field_name] = percentage

#сорт по заполненности
for field_name, percentage in sorted(field_stats.items(), key=lambda x: x[1], reverse=True):
    print(f"{field_name:20} : {percentage:5.1f}% заполнено")

print("="*60)
print(f"\nобновлено {updated_count} объектов дорог")
print(f"рассчитано {len(fields_to_add) + len(calculated_fields)} полей")