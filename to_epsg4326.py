import processing
from qgis.core import QgsProject

roads = QgsProject.instance().mapLayersByName('roads_split')[0]
processing.run("native:reprojectlayer", {
    'INPUT': roads,
    'TARGET_CRS': 'EPSG:4326',
    'OUTPUT': 'tmp/roads_for_web.geojson'
})
print("GeoJSON перепроецирован в градусы.")
