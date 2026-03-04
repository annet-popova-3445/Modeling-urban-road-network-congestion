import networkx as nx
import geopandas as gpd
import numpy as np
from shapely.geometry import LineString
from qgis.core import QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsPointXY
from qgis.PyQt.QtCore import QVariant


layer_name = 'novosibirsk_db — roads'
layer = QgsProject.instance().mapLayersByName(layer_name)[0]

features = [f for f in layer.getFeatures()]
df = gpd.GeoDataFrame.from_features(features) #из кугис в геопандас для графа

try:
    df.crs = layer.crs().authid()
except Exception as e:
    print(f"{e} чет произошло")

print(f"загружено дорог: {len(df)}")

#граф
G = nx.Graph()

for idx, row in df.iterrows():
    geom = row.geometry
    if geom.geom_type == 'LineString':
        start = geom.coords[0]
        end = geom.coords[-1]
        
        G.add_edge(start, end,
                   road_id=row['osm_id'],
                   name=row.get('name', ''),
                   length_km=row.get('length_km', 0.1),
                   free_flow_time=row.get('free_flow_time', 0.01),
                   capacity=row.get('capacity', 1800))

print(f"граф создан: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
print(f"компонент связности: {nx.number_connected_components(G)}")

#поиск юлижайших узлов
def nearest_node(target, nodes):
#ближайший узел из списка nodes к точке target (долгота, широта)
    nodes_array = np.array(nodes) 
    dist = np.linalg.norm(nodes_array - target, axis=1) #нампай считает евклидову длину каждого вектора разности
    return nodes[np.argmin(dist)]

#точки проверки
start_target = (82.9206, 55.0302)   # пл. Ленина
end_target = (83.0999, 54.8422)     # Академгородок

#список всех узлов графа
all_nodes = list(G.nodes())

start_node = nearest_node(start_target, all_nodes)
end_node = nearest_node(end_target, all_nodes)

print(f"\nближайший узел к пл. Ленина: {start_node}")
print(f"ближайший узел к Академгородку: {end_node}")

#проверка связности
if nx.has_path(G, start_node, end_node):
    path = nx.shortest_path(G, source=start_node, target=end_node, weight='free_flow_time') #самый быстрый! через networkx
    dist = sum(G[u][v]['length_km'] for u, v in zip(path[:-1], path[1:]))
    tm = sum(G[u][v]['free_flow_time'] for u, v in zip(path[:-1], path[1:])) #не забыть про пары последовательных узлов 
    
    print(f"расстояние: {dist:.1f} км")
    print(f"время (без пробок): {tm*60:.0f} мин")
    print(f"количество сегментов: {len(path)-1}")
    
#проверка визуальная
    route_layer = QgsVectorLayer("LineString?crs=EPSG:4326", "Маршрут (без пробок)", "memory")
    provider = route_layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    route_layer.updateFields()
    
    points = [QgsPointXY(*node) for node in path] #распаковка кортежа
    line_geom = QgsGeometry.fromPolylineXY(points) 
    
    feat = QgsFeature()
    feat.setGeometry(line_geom)
    feat.setAttributes(["пл.Ленина → Академгородок"])
    provider.addFeatures([feat])
    
    QgsProject.instance().addMapLayer(route_layer)
    print("маршрут добавлен")

else:
    print("\nузлы находятся в разных компонентах связности")