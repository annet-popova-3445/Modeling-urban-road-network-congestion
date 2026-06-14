from qgis.core import QgsMapLayer

layers = QgsProject.instance().mapLayers()
for name, layer in layers.items():
    if layer.type() == QgsMapLayer.VectorLayer:
        print(f"{layer.name()} векторный")
        print(f"Геометрия: {layer.geometryType()}")
        print(f"Количество: {layer.featureCount()}")
        fields = [field.name() for field in layer.fields()]
        print(f"Поля: {', '.join(fields[:5])}..." if fields else "пусто")
    else:
        print(f"{layer.name()} не векторный")
