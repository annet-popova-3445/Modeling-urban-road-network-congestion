import processing
import urllib.request
import json
import time
import re
import math
import sqlite3
import os
import networkx as nx
import numpy as np
import pandas as pd
from collections import defaultdict
from scipy.spatial import cKDTree
from concurrent.futures import ThreadPoolExecutor, as_completed
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsProject, QgsField, QgsExpression, QgsExpressionContext, QgsExpressionContextScope,
    QgsDistanceArea, QgsCoordinateReferenceSystem, QgsFeature, QgsVectorLayer, QgsGeometry,
    QgsVectorFileWriter, QgsSpatialIndex, QgsCoordinateTransform, QgsPointXY, QgsWkbTypes
)   

#параметры модели
CONFIG = {
    'output_gpkg_path': '/Users/annapopova/Documents/QGIS/general/transport_model.gpkg', #путь до будущего файла GeoPackagе
    'layer_roads_name': 'roads — highway_', #слой дорог
    'layer_buildings_name': 'buildings_polygons', #слой зданий
    'layer_zones_name': 'novosibirsk_db — boundary', #слой границ районов
    'grid_size_m': 1500, #размер ячейки (в метрах)
    'max_centroids': 200, #максимальное количество центроидов
    'max_distance_m': 15000, #максимальное расстояние между центрами районов (в метрах), для которого строятся OD-пары
    'beta': 0.2, #коэффициент затухания (1 / минута)
    'desired_trips': 300000, #желаемое общее число поездок в час пик (масштабирующий коэффициент)
    'bpr_alpha': 0.15, #параметр чувствительности к загрузке для учета заторов
    'bpr_beta': 4.0,  #степень нелинейности
    'target_crs_epsg': 32643, #UTM зона 43N для Новосибирска (метры, EPSG:32643)
    'overpass_chunk_size': 300, #количество OSM ID в одном запросе к Overpass API
    'overpass_timeout': 100, #таймаут запроса к Overpass в секундах
}

#создается пустой GeoPackage
def init_geopackage(gpkg_path):
    #удаляется старый файл, если есть
    if os.path.exists(gpkg_path):
        os.remove(gpkg_path)
        print(f"Старый файл {gpkg_path} удалён")
    
    #создание пустого GPKG с помощью sqlite3
    conn = sqlite3.connect(gpkg_path)
    #создание таблицы gpkg_contents (обязательная для валидного GeoPackage)
    conn.execute("CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE, description TEXT DEFAULT '', last_change DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER, CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id))")
    conn.execute("CREATE TABLE gpkg_spatial_ref_sys (srs_id INTEGER NOT NULL PRIMARY KEY, organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT)")
    conn.commit()
    conn.close()

    print(f"Создан пустой GeoPackage: {gpkg_path}")

#созранение слоя в GeoPackage (перезапись слоя с таким именем)
def save_layer_to_gpkg(layer, gpkg_path, layer_name, if_exists='overwrite'):
    """Сохраняет векторный слой в GeoPackage (перезаписывает слой с таким именем)"""
    # Если требуется перезапись, пробуем удалить старый слой
    if if_exists == 'overwrite':
        try:
            # Проверяем, существует ли слой в GPKG (через sqlite3)
            import sqlite3
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (layer_name,))
            if cursor.fetchone():
                # Удаляем слой через OGR
                from qgis.core import QgsVectorLayer
                # Простой способ: удалить таблицу и записи из gpkg_contents
                conn.execute(f"DROP TABLE IF EXISTS '{layer_name}'")
                conn.execute("DELETE FROM gpkg_contents WHERE table_name=?", (layer_name,))
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"  Предупреждение при удалении старого слоя: {e}")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = 'GPKG'
    options.layerName = layer_name
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    error = QgsVectorFileWriter.writeAsVectorFormat(layer, gpkg_path, options)
    if error[0] == QgsVectorFileWriter.NoError:
        print(f"  Слой '{layer_name}' сохранён в {gpkg_path}")
    else:
        print(f"  Ошибка сохранения слоя '{layer_name}': {error}")

#сохранение pandas DataFrame в GeoPackage как таблицу (без геометрии)
def save_dataframe_to_gpkg(df, gpkg_path, table_name):
    # Подключаемся к GPKG
    conn = sqlite3.connect(gpkg_path)
    # Удаляем старую таблицу, если есть
    conn.execute(f"DROP TABLE IF EXISTS '{table_name}'")
    # Сохраняем DataFrame
    df.to_sql(table_name, conn, index=False, if_exists='replace')
    # Регистрируем таблицу в gpkg_contents (чтобы QGIS её увидел)
    try:
        conn.execute("""
            INSERT OR REPLACE INTO gpkg_contents (table_name, data_type, identifier, description, last_change)
            VALUES (?, 'attributes', ?, '', CURRENT_TIMESTAMP)
        """, (table_name, table_name))
        conn.commit()
    except Exception as e:
        print(f"  Предупреждение: не удалось зарегистрировать таблицу в gpkg_contents: {e}")
    conn.close()
    print(f"  Таблица '{table_name}' сохранена в {gpkg_path}")

#функция получения слоя из QGIS
def get_layer_by_name(name):
    layers = QgsProject.instance().mapLayersByName(name)
    if not layers:
        raise ValueError(f"Слой '{name}' не найден")
    return layers[0]

#функция перепроицирования в UTM
def reproject_to_utm(layer, epsg=CONFIG['target_crs_epsg']):
    target_crs = QgsCoordinateReferenceSystem(f'EPSG:{epsg}')
    if layer.crs() == target_crs:
        return layer
    return processing.run('native:reprojectlayer', {
        'INPUT': layer, 'TARGET_CRS': target_crs, 'OUTPUT': 'memory:'
    })['OUTPUT']

#функция добавления поля
def add_field_if_missing(layer, field_name, field_type, length=50):
    if layer.fields().indexFromName(field_name) != -1:
        return
    if field_type == QVariant.String:
        field = QgsField(field_name, field_type, len=length)
    else:
        field = QgsField(field_name, field_type)
    layer.dataProvider().addAttributes([field])
    layer.updateFields()

#\\\\\\\\\\\\\\\\\\\\\\\\\\
#№1 - Подготовка слоя дорог
#\\\\\\\\\\\\\\\\\\\\\\\\\\

#функция получения тегов из OSM
def fetch_osm_tags(layer_roads):
    TAGS_TO_FETCH = {
        'name': 'string', 'maxspeed': 'string', 'lanes': 'string', 'oneway': 'string',
        'highway': 'string', 'bridge': 'string', 'tunnel': 'string', 'surface': 'string',
        'lit': 'string', 'access': 'string', 'width': 'string', 'junction': 'string',
        'smoothness': 'string'
    }
    osm_ids = [str(feat['osm_id']) for feat in layer_roads.getFeatures() if feat['osm_id']]
    print(f"Найдено {len(osm_ids)} объектов с OSM ID")
    chunk_size = CONFIG['overpass_chunk_size']
    chunks = [osm_ids[i:i+chunk_size] for i in range(0, len(osm_ids), chunk_size)]
    osm_to_tags = {}
    for idx, chunk in enumerate(chunks):
        print(f"Запрос {idx+1}/{len(chunks)} ({len(chunk)} объектов)")
        query = f"[out:json][timeout:{CONFIG['overpass_timeout']}]; way(id:{','.join(chunk)}); out tags;"
        try:
            req = urllib.request.Request(
                "http://overpass-api.de/api/interpreter",
                data=query.encode('utf-8'),
                headers={'User-Agent': 'QGIS-Diploma/2.0'}
            )
            response = urllib.request.urlopen(req, timeout=120)
            data = json.load(response)
            for elem in data.get('elements', []):
                elem_id = elem['id']
                osm_to_tags[elem_id] = {}
                for tag in TAGS_TO_FETCH:
                    if tag in elem.get('tags', {}):
                        osm_to_tags[elem_id][tag] = elem['tags'][tag]
            print(f"  Загружено {len(data.get('elements', []))} элементов")
            time.sleep(15)
        except Exception as e:
            print(f"  Ошибка: {e}")
            time.sleep(10)
    print(f"Получены теги для {len(osm_to_tags)} объектов")
    return osm_to_tags, list(TAGS_TO_FETCH.keys())

#функция обогащения слоя дорог атрибутами
def update_road_attributes(layer_roads, osm_to_tags, tag_names):
    layer_roads.startEditing()
    for tag in tag_names:
        add_field_if_missing(layer_roads, tag, QVariant.String, 100 if tag=='name' else 20)
    add_field_if_missing(layer_roads, 'lanes_numeric', QVariant.Int)
    add_field_if_missing(layer_roads, 'maxspeed_numeric', QVariant.Int)
    add_field_if_missing(layer_roads, 'is_oneway', QVariant.Int)
    add_field_if_missing(layer_roads, 'road_category', QVariant.String, 20)

    def parse_lanes(s):
        if not s: return None
        m = re.search(r'\d+', str(s))
        return int(m.group(0)) if m else None
    def parse_maxspeed(s):
        if not s: return None
        s = re.sub(r'[^\d]', '', str(s))
        return int(s) if s else None
    def road_category(highway):
        if not highway: return 'unknown'
        h = highway.lower()
        if h in ('motorway','motorway_link'): return 'motorway'
        if h in ('trunk','trunk_link'): return 'trunk'
        if h in ('primary','primary_link'): return 'primary'
        if h in ('secondary','secondary_link'): return 'secondary'
        if h in ('tertiary','tertiary_link'): return 'tertiary'
        if h in ('unclassified','residential','living_street'): return 'local'
        if h in ('service','track','path'): return 'service'
        return 'other'

    updated = 0
    for feat in layer_roads.getFeatures():
        osm_id = feat['osm_id']
        if not osm_id or osm_id not in osm_to_tags:
            continue
        tags = osm_to_tags[osm_id]
        changed = False
        for tag in tag_names:
            if tag in tags and tags[tag] is not None:
                if feat[tag] != tags[tag]:
                    feat[tag] = tags[tag]
                    changed = True
        if changed:
            lanes = parse_lanes(feat['lanes'] if feat['lanes'] is not None else None)
            if lanes: feat['lanes_numeric'] = lanes
            speed = parse_maxspeed(feat['maxspeed'] if feat['maxspeed'] is not None else None)
            if not speed:
                hw = feat['highway'] if feat['highway'] is not None else ''
                if 'motorway' in hw: speed = 90
                elif 'trunk' in hw: speed = 70
                elif 'primary' in hw: speed = 60
                elif 'secondary' in hw: speed = 50
                elif 'tertiary' in hw: speed = 40
                elif 'residential' in hw: speed = 30
                else: speed = 50
            feat['maxspeed_numeric'] = speed
            oneway = str(feat['oneway'] if feat['oneway'] is not None else 'no').lower()
            feat['is_oneway'] = 1 if oneway in ('yes','1','true') else 0
            feat['road_category'] = road_category(feat['highway'] if feat['highway'] is not None else '')
            layer_roads.updateFeature(feat)
            updated += 1
            if updated % 500 == 0:
                print(f"Обновлено {updated} дорог")
    layer_roads.commitChanges()
    print(f"Атрибуты обновлены для {updated} дорог")

#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
#№2 - Подготовка слоя зданий и районов
#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\

#функция оценки количества рабочих мест
def estimate_jobs(feature):
    btype = str(feature.attribute('building') or '').lower()
    amenity = str(feature.attribute('amenity') or '').lower()
    shop = str(feature.attribute('shop') or '').lower()
    office = str(feature.attribute('office') or '').lower()
    geom = feature.geometry()
    area = geom.area() if geom and not geom.isEmpty() else 0.0
    #жилая площадь
    if btype in ('apartments','house','residential','dormitory','terrace'):
        return 0
    #коммерция, офисы
    if btype in ('commercial','retail','supermarket','mall') or shop or office:
        return max(1, int(area/20.0)) if area>0 else 5
    #промышленность/производство
    if btype in ('industrial','warehouse','factory'):
        return max(5, int(area/50.0)) if area>0 else 10
    #образовательные учреждения
    if amenity in ('school','college','university'):
        return 100
    #медицинские учреждения
    if amenity in ('hospital','clinic'):
        return 200
    #детские сады и ясли
    if amenity == 'kindergarten':
        return 20
    #отели
    if btype == 'hotel' or 'hotel' in amenity:
        return 30
    return max(1, int(area/50.0)) if area>0 else 1

#функиця подготовки слоев зданий и районов
def prepare_buildings_and_zones():
    buildings_src = get_layer_by_name(CONFIG['layer_buildings_name'])
    zones_src = get_layer_by_name(CONFIG['layer_zones_name'])
    buildings_utm = reproject_to_utm(buildings_src)
    zones_utm = reproject_to_utm(zones_src)
    add_field_if_missing(buildings_utm, 'jobs_est', QVariant.Int)
    buildings_utm.startEditing()
    for feat in buildings_utm.getFeatures():
        feat['jobs_est'] = estimate_jobs(feat)
        buildings_utm.updateFeature(feat)
    buildings_utm.commitChanges()
    print(f"jobs_est вычислено для {buildings_utm.featureCount()} зданий")

    #сохранение в GPKG для повторного использования
    # gpkg_path = '/tmp/buildings_with_jobs.gpkg'
    # options = QgsVectorFileWriter.SaveVectorOptions()
    # options.driverName = 'GPKG'
    # options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    # options.layerName = 'buildings_with_jobs'
    # QgsVectorFileWriter.writeAsVectorFormat(buildings_utm, gpkg_path, options)

    #пространственное суммирование
    params = {
        'INPUT': zones_utm,
        'JOIN': buildings_utm,
        'PREDICATE': [0],
        'JOIN_FIELDS': ['jobs_est'],
        'SUMMARIES': [5],
        'OUTPUT': 'memory:'
    }
    joined = processing.run('qgis:joinbylocationsummary', params)['OUTPUT']
    sum_field = None
    for f in joined.fields():
        if 'jobs_est' in f.name() and ('sum' in f.name().lower() or f.name() == 'jobs_est'):
            sum_field = f.name()
            break
    if not sum_field:
        raise Exception("Не найдено поле суммы рабочих мест")
    jobs_dict = {feat.id(): int(feat[sum_field] or 0) for feat in joined.getFeatures()}
    add_field_if_missing(zones_src, 'jobs', QVariant.Int)
    zones_src.startEditing()
    for feat in zones_src.getFeatures():
        if feat.id() in jobs_dict:
            feat['jobs'] = jobs_dict[feat.id()]
            zones_src.updateFeature(feat)
    zones_src.commitChanges()
    print(f"Поле 'jobs' обновлено для {len(jobs_dict)} районов")
    return buildings_utm, zones_utm

#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
#№3 - Разбиение дорог и построение графа
#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\

#функция разбивки слоя дорог 
def split_roads(roads_layer):
    split = processing.run('native:splitwithlines', {
        'INPUT': roads_layer, 'LINES': roads_layer, 'OUTPUT': 'memory:'
    })['OUTPUT']
    split.setName('roads_split')
    QgsProject.instance().addMapLayer(split)
    return split

#функция построения графа
def build_directed_graph(split_layer):
    #добавление недостающих полей
    add_field_if_missing(split_layer, 'length_km', QVariant.Double)
    add_field_if_missing(split_layer, 'free_flow_time', QVariant.Double)
    add_field_if_missing(split_layer, 'capacity', QVariant.Int)
    #вычисление длины, времени, пропускнуой способности
    da = QgsDistanceArea()
    da.setEllipsoid('WGS84')
    crs = QgsCoordinateReferenceSystem(f'EPSG:{CONFIG["target_crs_epsg"]}')
    da.setSourceCrs(crs, QgsProject.instance().transformContext())
    split_layer.startEditing()
    for feat in split_layer.getFeatures():
        geom = feat.geometry()
        if not geom or geom.isEmpty():
            continue
        length_m = da.measureLength(geom)
        length_km = length_m / 1000.0
        feat['length_km'] = length_km
        speed = feat.attribute('maxspeed_numeric')
        if not speed:
            speed = 50
        time_h = length_km / speed if speed>0 else 0.01
        feat['free_flow_time'] = time_h
        lanes = feat['lanes_numeric'] if feat['lanes_numeric'] is not None else 1
        if lanes >= 3:
            capacity = 5400
        elif lanes == 2:
            capacity = 3600
        else:
            capacity = 1800
        feat['capacity'] = capacity
        split_layer.updateFeature(feat)
    split_layer.commitChanges()

    #построение направленного графа
    G = nx.DiGraph()
    for feat in split_layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        road_id = feat['osm_id'] if feat['osm_id'] is not None else None
        name = feat['name'] if feat['name'] is not None else ''
        length_km = feat['length_km'] if feat['length_km'] is not None else 0.1
        time_min = (feat['free_flow_time'] if feat['free_flow_time'] is not None else 0.01) * 60.0
        capacity = feat['capacity'] if feat['capacity'] is not None else 1800
        oneway_val = str(feat['oneway'] if feat['oneway'] is not None else 'no').lower()
        junction = str(feat['junction'] if feat['junction'] is not None else '').lower()
        directed = (oneway_val in ('yes','1','true')) or (junction == 'roundabout')
        def process_points(points):
            if len(points) < 2:
                return
            seg_len = length_km / (len(points)-1)
            seg_time = time_min / (len(points)-1)
            for i in range(len(points)-1):
                u = (points[i].x(), points[i].y())
                v = (points[i+1].x(), points[i+1].y())
                attrs = {
                    'road_id': road_id, 'name': name,
                    'length_km': seg_len, 'time_min': seg_time,
                    'capacity': capacity,
                    'free_flow_time_min': seg_time
                }
                if directed:
                    G.add_edge(u, v, **attrs)
                else:
                    G.add_edge(u, v, **attrs)
                    G.add_edge(v, u, **attrs)
        if geom.isMultipart():
            for part in geom.asMultiPolyline():
                process_points(part)
        else:
            process_points(geom.asPolyline())
    print(f"Граф: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
    # Оставляем гигантский компонент
    # components = list(nx.weakly_connected_components(G))
    # if components:
    #     giant = max(components, key=len)
    #     G = G.subgraph(giant).copy()
    #     print(f"Гигантский компонент: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
    # else:
    #     print("Предупреждение: нет связных компонент")
    return G

#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
#№4 - Создание сетки, отбор активных ячеек и центроидов
#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\

#функция создания сетки
def create_grid(zones_utm, buildings_utm):
    extent = zones_utm
    grid = processing.run('native:creategrid', {
        'TYPE': 2,
        'EXTENT': extent,
        'HSPACING': CONFIG['grid_size_m'],
        'VSPACING': CONFIG['grid_size_m'],
        'CRS': QgsCoordinateReferenceSystem(f'EPSG:{CONFIG["target_crs_epsg"]}'),
        'OUTPUT': 'memory:'
    })['OUTPUT']
    add_field_if_missing(grid, 'jobs', QVariant.Double)
    add_field_if_missing(grid, 'pop', QVariant.Double)

    #агрегация jobs
    idx = QgsSpatialIndex(buildings_utm.getFeatures())
    jobs_dict = {}
    for cell in grid.getFeatures():
        total = 0.0
        for bid in idx.intersects(cell.geometry().boundingBox()):
            bld = buildings_utm.getFeature(bid)
            if bld.geometry().intersects(cell.geometry()):
                total += float(bld['jobs_est'] if bld['jobs_est'] is not None else 0)
        jobs_dict[cell.id()] = total
    grid.startEditing()
    for cell in grid.getFeatures():
        cell['jobs'] = jobs_dict[cell.id()]
        grid.updateFeature(cell)
    grid.commitChanges()

    #распределение населения по жилой площади
    add_field_if_missing(buildings_utm, 'res_area', QVariant.Double)
    buildings_utm.startEditing()
    for feat in buildings_utm.getFeatures():
        btype = str(feat['building'] if feat['building'] is not None else '').lower()
        if btype in ('apartments','house','residential','dormitory','terrace'):
            area = feat.geometry().area() if feat.geometry() else 0.0
            feat['res_area'] = area
        else:
            feat['res_area'] = 0.0
        buildings_utm.updateFeature(feat)
    buildings_utm.commitChanges()

    #сумма res_area по районам
    zones_m = reproject_to_utm(zones_utm)
    zone_res_area = {}
    zone_pop = {}
    for zone in zones_m.getFeatures():
        zone_id = zone.id()
        zone_res_area[zone_id] = 0.0
        pop_val = zone.attribute('population')
        zone_pop[zone_id] = float(pop_val) if pop_val is not None else 0.0
        for bld in buildings_utm.getFeatures():
            if bld.geometry().intersects(zone.geometry()):
                zone_res_area[zone_id] += bld['res_area'] if bld['res_area'] is not None else 0
    idx_res = QgsSpatialIndex(buildings_utm.getFeatures())
    grid.startEditing()
    for cell in grid.getFeatures():
        cell_res = 0.0
        cell_geom = cell.geometry()
        for bid in idx_res.intersects(cell_geom.boundingBox()):
            bld = buildings_utm.getFeature(bid)
            if bld.geometry().intersects(cell_geom):
                cell_res += bld['res_area'] if bld['res_area'] is not None else 0
        centroid = cell_geom.centroid().asPoint()
        assigned_zone = None
        for zone in zones_m.getFeatures():
            if zone.geometry().contains(centroid):
                assigned_zone = zone
                break
        if assigned_zone is not None:
            zone_id = assigned_zone.id()
            if zone_res_area[zone_id] > 0:
                pop_val = zone_pop[zone_id] * (cell_res / zone_res_area[zone_id])
            else:
                pop_val = 0.0
        else:
            pop_val = 0.0
        cell['pop'] = pop_val
        grid.updateFeature(cell)
    grid.commitChanges()

    #отбор активных ячеек
    active = [c for c in grid.getFeatures() if (c['jobs'] or 0) > 0 or (c['pop'] or 0) > 0]
    active.sort(key=lambda c: (c['pop'] or 0) + (c['jobs'] or 0), reverse=True)
    if len(active) > CONFIG['max_centroids']:
        active = active[:CONFIG['max_centroids']]
    print(f"Активных ячеек: {len(active)}")

    #создание центроидов
    centroids_layer = QgsVectorLayer(f"Point?crs=EPSG:{CONFIG['target_crs_epsg']}", "centroids_grid", "memory")
    pr = centroids_layer.dataProvider()
    pr.addAttributes([QgsField("pop", QVariant.Double), QgsField("jobs", QVariant.Double)])
    centroids_layer.updateFields()
    centroids_layer.startEditing()
    for cell in active:
        feat = QgsFeature()
        feat.setGeometry(cell.geometry().centroid())
        feat.setAttributes([cell['pop'], cell['jobs']])
        pr.addFeature(feat)
    centroids_layer.commitChanges()
    QgsProject.instance().addMapLayer(centroids_layer)
    return centroids_layer

#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
#№5 - Привязка центроидов к графу
#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\

#функция привязки центроидов
def attach_centroids_to_graph(centroids_layer, G):
    nodes = list(G.nodes())
    if not nodes:
        raise Exception("Граф не содержит узлов")
    node_coords = np.array(nodes)
    tree = cKDTree(node_coords)
    centroid_to_node = {}
    for feat in centroids_layer.getFeatures():
        pt = feat.geometry().asPoint()
        dist, idx = tree.query([pt.x(), pt.y()], k=1)
        if dist < 1000:  # 1 км
            centroid_to_node[feat.id()] = tuple(node_coords[idx])
        else:
            print(f"Центроид {feat.id()} далеко от графа (dist={dist:.1f} м), пропущен")
    print(f"Привязано центроидов: {len(centroid_to_node)}")
    if len(centroid_to_node) == 0:
        raise Exception("Не привязано ни одного центроида")
    return centroid_to_node

# \\\\\\\\\\\\\\\\\\\\\\\\
# №6 - Матрица стоимостей
# \\\\\\\\\\\\\\\\\\\\\\\\

#функция расчета матрицы корреспонденций
def compute_cost_matrix(centroid_to_node, G, max_dist_m):
    fids = list(centroid_to_node.keys())
    dist_from = {}
    
    #количество потоков (не больше числа центроидов и не больше ядер CPU)
    num_threads = min(os.cpu_count() or 4, len(centroid_to_node), 8)  # максимум 8 потоков
    print(f"Запуск {num_threads} потоков для {len(centroid_to_node)} центроидов...")
    
    def run_dijkstra(fid, node):
        try:
            return fid, nx.single_source_dijkstra_path_length(G, node, weight='time_min')
        except Exception as e:
            print(f"Ошибка Дейкстры для {fid}: {e}")
            return fid, {}
    
    #многопоточное выполнение
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        #отправление задач
        future_to_fid = {
            executor.submit(run_dijkstra, fid, node): fid 
            for fid, node in centroid_to_node.items()
        }
        #сборка результатов по мере завершения
        completed = 0
        for future in as_completed(future_to_fid):
            fid, result = future.result()
            dist_from[fid] = result
            completed += 1
            if completed % 10 == 0:
                print(f"  Выполнено Дейкстр: {completed}/{len(centroid_to_node)}")
    
    #построение пар
    pairs = []
    for i in range(len(fids)):
        fid_i = fids[i]
        node_i = centroid_to_node[fid_i]
        for j in range(i+1, len(fids)):
            fid_j = fids[j]
            node_j = centroid_to_node[fid_j]
            #евклидово расстояние в метрах для предфильтра
            dx = node_i[0] - node_j[0]
            dy = node_i[1] - node_j[1]
            if math.sqrt(dx*dx + dy*dy) > max_dist_m:
                continue
            t = dist_from[fid_i].get(node_j, np.inf)
            if np.isfinite(t):
                pairs.append({'i': fid_i, 'j': fid_j, 'time_min': t})
                pairs.append({'i': fid_j, 'j': fid_i, 'time_min': t})
    
    print(f"Сформировано {len(pairs)} пар после фильтрации по расстоянию")
    return pd.DataFrame(pairs)

#\\\\\\\\\\\\\\\\\\\\\\\\\\\
#№7 - Гравитационная модель
#\\\\\\\\\\\\\\\\\\\\\\\\\\\

#функция расчета гравитационной модели
def gravity_model(cost_df, centroids_layer, beta, desired_trips):
    pop_dict = {f.id(): f['pop'] for f in centroids_layer.getFeatures()}
    jobs_dict = {f.id(): f['jobs'] for f in centroids_layer.getFeatures()}
    cost_df['Oi'] = cost_df['i'].map(pop_dict).fillna(0)
    cost_df['Aj'] = cost_df['j'].map(jobs_dict).fillna(0)
    valid = (cost_df['Oi'] > 0) & (cost_df['Aj'] > 0) & (cost_df['time_min'] < 1e8)
    valid_df = cost_df[valid].copy()
    if valid_df.empty:
        raise Exception("Нет пар с Oi>0 и Aj>0")
    valid_df['attraction'] = valid_df['Aj'] * np.exp(-beta * valid_df['time_min'])
    sum_att = valid_df.groupby('i')['attraction'].transform('sum')
    valid_df['T_ij'] = valid_df['Oi'] * (valid_df['attraction'] / sum_att)
    total_model = valid_df['T_ij'].sum()
    if total_model > 0 and desired_trips > 0:
        scale = desired_trips / total_model
        valid_df['T_ij'] *= scale
        print(f"Масштабирование: {total_model:.0f} -> {desired_trips} (коэф. {scale:.4f})")

    #добавление внутризональных поездкок (петли)
    loop_rows = []
    for fid in pop_dict.keys():
        if pop_dict.get(fid, 0) > 0 and jobs_dict.get(fid, 0) > 0:
            #20% поездок из ячейки остаются внутри
            T_ii = 0.2 * pop_dict[fid]
            loop_rows.append({'i': fid, 'j': fid, 'T_ij': T_ii})
    if loop_rows:
        df_loops = pd.DataFrame(loop_rows)
        valid_df = pd.concat([valid_df, df_loops], ignore_index=True)
        print(f"Добавлено {len(loop_rows)} внутризональных поездок, суммарный объём: {df_loops['T_ij'].sum():.0f}")

    return valid_df[['i','j','T_ij']]

# \\\\\\\\\\\\\\\\\\\\\\\
# №8 - Назначение потоков
# \\\\\\\\\\\\\\\\\\\\\\\

#функция назначения потоков
def assign_traffic(od_df, centroid_to_node, G):
    edge_flow = defaultdict(float)
    for _, row in od_df.iterrows():
        vol = float(row['T_ij'])
        if vol <= 0:
            continue
        u = centroid_to_node[int(row['i'])]
        v = centroid_to_node[int(row['j'])]
        try:
            path = nx.shortest_path(G, u, v, weight='time_min')
        except nx.NetworkXNoPath:
            continue
        for k in range(len(path)-1):
            a, b = path[k], path[k+1]
            edge_flow[(a, b)] += vol
    print(f"Рёбер с нагрузкой: {len(edge_flow)}")
    return edge_flow

def apply_bpr(G, edge_flow, alpha, beta):
    for (u, v), flow in edge_flow.items():
        if G.has_edge(u, v):
            attrs = G[u][v]
            freeflow = attrs['time_min']
            cap = attrs['capacity']
            if cap > 0:
                congested = freeflow * (1 + alpha * (flow/cap)**beta)
                attrs['time_congested'] = congested
            else:
                attrs['time_congested'] = freeflow
    print("BPR применён")

#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
#№9 - Запись нагрузки в roads_split
#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\

#функция записи нагрузки в исходный слой
def save_flow_layer(edge_flow, split_layer):
    add_field_if_missing(split_layer, 'total_flow', QVariant.Double)
    split_layer.startEditing()
    flow_geoms = []
    for (u, v), flow in edge_flow.items():
        geom = QgsGeometry.fromPolylineXY([QgsPointXY(u[0], u[1]), QgsPointXY(v[0], v[1])])
        flow_geoms.append((geom, flow))
    updated = 0
    for feat in split_layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.type() != QgsWkbTypes.LineGeometry:
            continue
        best_flow = 0.0
        best_dist = 10.0
        for fg, fv in flow_geoms:
            d = geom.distance(fg)
            if d < best_dist:
                best_dist = d
                best_flow = fv
        if best_flow > 0:
            feat['total_flow'] = best_flow
            split_layer.updateFeature(feat)
            updated += 1
    split_layer.commitChanges()
    print(f"Обновлено {updated} дорог с ненулевым потоком.")

#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
#№10 - Скрипт запуска всей модели
#\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\\
def run_transport_model():
    print("=" * 70)
    print("Запуск транспортной модели")
    print("=" * 70)

    #1. Инициализация GeoPackage
    gpkg_path = CONFIG['output_gpkg_path']
    if not gpkg_path:
        raise ValueError("Укажите путь к выходному GeoPackage в CONFIG['output_gpkg_path']")
    init_geopackage(gpkg_path)

    #2. Загрузка и подготовка дорог
    roads_layer = get_layer_by_name(CONFIG['layer_roads_name'])
    roads_utm = reproject_to_utm(roads_layer) #перевод в UTM (метры)

    #загрузка тегов OSM (можно закомментировать, если данные уже есть)
    # osm_tags, tag_names = fetch_osm_tags(roads_utm)
    # update_road_attributes(roads_utm, osm_tags, tag_names)

    #3. Подготовка зданий и районов
    buildings_utm, zones_utm = prepare_buildings_and_zones()
    save_layer_to_gpkg(buildings_utm, gpkg_path, 'buildings_with_jobs')
    save_layer_to_gpkg(zones_utm, gpkg_path, 'zones_with_jobs')

    #4. Разбиение дорог и построение направленного графа
    roads_split = split_roads(roads_utm)
    #save_layer_to_gpkg(roads_split, gpkg_path, 'roads_split')
    G = build_directed_graph(roads_split)

    #5. Создание сетки и центроидов
    centroids_layer = create_grid(zones_utm, buildings_utm)
    save_layer_to_gpkg(centroids_layer, gpkg_path, 'centroids')

    #6. Привязка центроидов к графу
    centroid_to_node = attach_centroids_to_graph(centroids_layer, G)

    #7. Матрица стоимостей и гравитационная модель
    cost_df = compute_cost_matrix(centroid_to_node, G, CONFIG['max_distance_m'])
    od_df = gravity_model(cost_df, centroids_layer, CONFIG['beta'], CONFIG['desired_trips'])
    save_dataframe_to_gpkg(od_df, gpkg_path, 'od_matrix')

    #8. Назначение потоков и BPR
    edge_flow = assign_traffic(od_df, centroid_to_node, G)
    apply_bpr(G, edge_flow, CONFIG['bpr_alpha'], CONFIG['bpr_beta'])

    #9. Запись потоков в слой дорог и сохранение
    save_flow_layer(edge_flow, roads_split) #добавление поля total_flow
    save_layer_to_gpkg(roads_split, gpkg_path, 'roads_split', if_exists='overwrite')

    #10. Создание и сохранение слоя загруженных рёбер
    flow_lines = QgsVectorLayer(f"LineString?crs=EPSG:{CONFIG['target_crs_epsg']}", "loaded_edges", "memory")
    pr = flow_lines.dataProvider()
    pr.addAttributes([QgsField("flow", QVariant.Double), QgsField("time_cong", QVariant.Double)])
    flow_lines.updateFields()
    flow_lines.startEditing()
    for (u, v), flow in edge_flow.items():
        if flow <= 0:
            continue
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(u[0], u[1]), QgsPointXY(v[0], v[1])]))
        time_cong = G[u][v].get('time_congested', G[u][v]['time_min'])
        feat.setAttributes([flow, time_cong])
        pr.addFeature(feat)
    flow_lines.commitChanges()
    save_layer_to_gpkg(flow_lines, gpkg_path, 'loaded_edges')

    #11. Добавление слоёв в проект QGIS
    QgsProject.instance().addMapLayer(roads_split)
    QgsProject.instance().addMapLayer(centroids_layer)
    QgsProject.instance().addMapLayer(flow_lines)

    print(f"\n Модель успешно завершена! Все результаты сохранены в {gpkg_path}")
    print(f"   - Граф: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
    print(f"   - OD-пар: {len(od_df)}, поездок: {od_df['T_ij'].sum():.0f}")
    print(f"   - Загружено рёбер: {len(edge_flow)}")

run_transport_model()