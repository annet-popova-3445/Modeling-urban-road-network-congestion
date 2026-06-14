zones = QgsProject.instance().mapLayersByName('boundary')[0] #индекс! иначе выведет еще слои, нужен первый
buildings = QgsProject.instance().mapLayersByName('buildings_polygons')[0]
print("Проверка:\n")
for zone in zones.getFeatures():
    name = zone['name']
    geom = zone.geometry()
    count = 0
    for bld in buildings.getFeatures():
        if bld.geometry().intersects(geom):
            count += 1
    print(f"{name}: {count} зданий")
