# Проверка мостов
layer = QgsProject.instance().mapLayersByName('novosibirsk_db — roads')[0]
bridge_count = 0
for feat in layer.getFeatures():
    if feat['bridge'] == 'yes':
        bridge_count += 1
        if bridge_count <= 5:
            print(f"Мост: {feat['name']} (OSM ID {feat['osm_id']})")
print(f"Всего мостов: {bridge_count}")