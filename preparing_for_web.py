from qgis.core import QgsProject, QgsVectorFileWriter
import os

roads = QgsProject.instance().mapLayersByName('roads_split')[0]
fields_to_keep = ['total_flow', 'load_factor', 'name', 'highway']
field_indices = [roads.fields().indexFromName(f) for f in fields_to_keep]

output_path = '/tmp/roads_for_web.geojson'
if os.path.exists(output_path):
    os.remove(output_path)

options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = 'GeoJSON'
options.attributes = field_indices
options.layerName = 'roads_for_web'
options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer

err = QgsVectorFileWriter.writeAsVectorFormat(roads, output_path, options)
if err[0] == QgsVectorFileWriter.NoError:
    print(f"Успешно экспортировано в {output_path}")
else:
    print(f"Ошибка: {err}")