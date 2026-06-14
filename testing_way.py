from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsPointXY
from qgis.PyQt.QtCore import QVariant

#выберите пару, для которой есть путь (замените fid1, fid2 на реальные)
fid1 = 1   #пример
fid2 = 2
if (fid1 in valid_centroids and fid2 in valid_centroids and 
    nx.has_path(G, valid_centroids[fid1], valid_centroids[fid2])):
    path = nx.shortest_path(G, valid_centroids[fid1], valid_centroids[fid2], weight='time_min')
    #создание слоя маршрута
    route_layer = QgsVectorLayer("LineString?crs=EPSG:4326", "test_route", "memory")
    provider = route_layer.dataProvider()
    provider.addAttributes([QgsField("name", QVariant.String)])
    route_layer.updateFields()
    points = [QgsPointXY(node[0], node[1]) for node in path]
    geom = QgsGeometry.fromPolylineXY(points)
    feat = QgsFeature()
    feat.setGeometry(geom)
    feat.setAttributes([f"Route {fid1}->{fid2}"])
    provider.addFeature(feat)
    QgsProject.instance().addMapLayer(route_layer)
    print("Маршрут добавлен на карту")
else:
    print("Пути нет")
