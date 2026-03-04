import networkx as nx
import pandas as pd
import numpy as np
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField, QgsFeature, QgsGeometry,
    QgsPointXY, QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant
from math import radians, sin, cos, sqrt, asin


CENTROIDS_LAYER_NAME = 'centroids'
ROADS_LAYER_NAME = 'roads — highway_'
TOTAL_TRIPS = 2_000_000 
EDGES_OUTPUT_LAYER_NAME = 'graph_edges_flow' # имя выходного слоя


centroids_layer = QgsProject.instance().mapLayersByName(CENTROIDS_LAYER_NAME)[0] #индекс!
data = []
for feat in centroids_layer.getFeatures():
    pop = feat['population'] if feat['population'] else 0
    jobs = feat['jobs'] if feat['jobs'] else 0
    geom = feat.geometry().asPoint()
    data.append({
        'id': feat.id(),
        'name': feat['name'],
        'population': pop,
        'jobs': jobs,
        'x': geom.x(),
        'y': geom.y()
    })

df_zones = pd.DataFrame(data)
n_zones = len(df_zones)
print(f"загружено {n_zones} районов")

#+ граф (ничего нового)
roads_layer = QgsProject.instance().mapLayersByName(ROADS_LAYER_NAME)[0]
G = nx.Graph()
for feat in roads_layer.getFeatures():
    geom = feat.geometry()
    if geom and geom.type() == QgsWkbTypes.LineGeometry:
        polyline = geom.asPolyline()
        if len(polyline) >= 2:
            start = (polyline[0].x(), polyline[0].y())
            end = (polyline[-1].x(), polyline[-1].y())
            #атрибуты с запасными значениями если поля нет
            length_km = feat['length_km'] if feat['length_km'] else 0.1
            free_flow_time = feat['free_flow_time'] if feat['free_flow_time'] else 0.01
            capacity = feat['capacity'] if feat['capacity'] else 1800
            road_id = feat['osm_id'] if feat['osm_id'] else 0
            name = feat['name'] if feat['name'] else ''
            G.add_edge(start, end,
                       road_id=road_id,
                       name=name,
                       length_km=length_km,
                       free_flow_time=free_flow_time,
                       capacity=capacity,
                       flow=0.0)   #инициализируем поток
print(f"граф создан: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")

#расстояние между центроидами (гаверсинус)
def haversine(lon1, lat1, lon2, lat2):
    R = 6371
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return R * c

dist_matrix = np.zeros((n_zones, n_zones))
for i in range(n_zones):
    for j in range(n_zones):
        if i == j:
            dist_matrix[i, j] = 0.5   #внутризонное расстояние
        else:
            dist_matrix[i, j] = haversine(
                df_zones.iloc[i]['x'], df_zones.iloc[i]['y'],
                df_zones.iloc[j]['x'], df_zones.iloc[j]['y']
            )
print("матрица расстояний рассчитана")

#преобразуем столбцы в числа, + пропуски на 0
pop_vals = pd.to_numeric(df_zones['population'], errors='coerce').fillna(0).values
jobs_vals = pd.to_numeric(df_zones['jobs'], errors='coerce').fillna(0).values

#+ матрица произведений (население i * рабочие места j)
prod = np.outer(pop_vals, jobs_vals)

#+ матрица расстояний
d2 = dist_matrix ** 2
d2[d2 == 0] = 0.1   # избегаем деления на ноль
T = prod / d2

#нормировка на общее число поездок
scale = TOTAL_TRIPS / T.sum()
T_scaled = T * scale

print(f"суммарное число после нормировки: {T_scaled.sum():.0f}")

#все или ничего
for u, v, data in G.edges(data=True):
    data['flow'] = 0.0 #сброс потока

#список узлов графа для поиска ближайшего
nodes = list(G.nodes())
nodes_arr = np.array(nodes)

def nearest_node(point):
    dists = np.linalg.norm(nodes_arr - point, axis=1)
    return nodes[np.argmin(dists)]

#для пары ij
for i in range(n_zones):
    source_point = (df_zones.iloc[i]['x'], df_zones.iloc[i]['y']) #реальные центроиды
    source_node = nearest_node(source_point) #ближайштй

    for j in range(n_zones):
        if i == j: #пока без межрайонных
            continue
        flow = T_scaled[i, j]
        if flow < 1:
            continue
        target_point = (df_zones.iloc[j]['x'], df_zones.iloc[j]['y'])
        target_node = nearest_node(target_point) #ближайший центроид к пункту назначения

        try:
            path = nx.shortest_path(G, source=source_node, target=target_node, weight='free_flow_time') #возвращает список узлов по кратчайшему пути по времени потока
            for u, v in zip(path[:-1], path[1:]):
                G[u][v]['flow'] += flow
        except nx.NetworkXNoPath:
            print(f"нет пути между {df_zones.iloc[i]['name']} и {df_zones.iloc[j]['name']}")

    if i % 2 == 0:
        print(f"обработано районов: {i+1}/{n_zones}") #стата каждые два района

print("распределение потоков завершено.")

#(V/C ratio)
edges_layer = QgsVectorLayer("LineString?crs=EPSG:4326", EDGES_OUTPUT_LAYER_NAME, "memory") #новый вектор
provider = edges_layer.dataProvider()
provider.addAttributes([
    QgsField("road_id", QVariant.Int),
    QgsField("name", QVariant.String, len=100),
    QgsField("length_km", QVariant.Double),
    QgsField("capacity", QVariant.Int),
    QgsField("flow", QVariant.Double),
    QgsField("vcr", QVariant.Double)
])
edges_layer.updateFields()

features = []
for u, v, data in G.edges(data=True):
    line = QgsGeometry.fromPolylineXY([QgsPointXY(u[0], u[1]), QgsPointXY(v[0], v[1])]) #для каждого ребра:
    feat = QgsFeature()
    feat.setGeometry(line)
    flow = data['flow']
    cap = data['capacity']
    vcr = flow / cap if cap > 0 else 0
    feat.setAttributes([
        data.get('road_id', 0),
        data.get('name', ''),
        data.get('length_km', 0),
        cap,
        flow,
        vcr
    ])
    features.append(feat)

provider.addFeatures(features)
edges_layer.updateExtents() #пересчитывает экстент слоя

QgsProject.instance().addMapLayer(edges_layer)
print(f"'{EDGES_OUTPUT_LAYER_NAME}' добавлен на карту.")
