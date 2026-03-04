
zones = QgsProject.instance().mapLayersByName('novosibirsk_db — boundary')[0] #индекс! иначе выведет еще кучу слоев, нужен первый
buildings = QgsProject.instance().mapLayersByName('novosibirsk_db — buildings_polygons')[0]
print("проверка:\n")
for zone in zones.getFeatures():
    name = zone['name']
    geom = zone.geometry()
    count = 0
    for bld in buildings.getFeatures():
        if bld.geometry().intersects(geom):
            count += 1
    print(f"{name}: {count} зданий")