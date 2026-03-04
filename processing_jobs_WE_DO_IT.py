from qgis.core import QgsProject, QgsField, QgsVectorLayer, QgsCoordinateReferenceSystem
from qgis.PyQt.QtCore import QVariant
import processing


buildings_src_name = 'novosibirsk_db — buildings_polygons'
zones_src_name = 'novosibirsk_db — boundary'

#+ исходные слои
buildings_src = QgsProject.instance().mapLayersByName(buildings_src_name)[0] #dayly remind про индекс, нам не нужен весь список слоев
zones_src = QgsProject.instance().mapLayersByName(zones_src_name)[0]
print("слои загружены")

#все во временных слоях
target_crs = QgsCoordinateReferenceSystem('EPSG:32643')  #зона 43 северного полушария, иначе будет в градусах, а не метрах

#+ здания во временный слой
params_buildings = {
    'INPUT': buildings_src,
    'TARGET_CRS': target_crs,
    'OUTPUT': 'memory:'
}
buildings_utm = processing.run('native:reprojectlayer', params_buildings)['OUTPUT']
print("слой зданий добавлен во временный")

#+ районы во временный слой
params_zones = {
    'INPUT': zones_src,
    'TARGET_CRS': target_crs,
    'OUTPUT': 'memory:'
}
zones_utm = processing.run('native:reprojectlayer', params_zones)['OUTPUT']
print("слой районов добавлен во временный")

#поле работы
def estimate_jobs(feature):
    btype = (feature['building'] or '').lower()
    amenity = (feature['amenity'] or '').lower()
    shop = (feature['shop'] or '').lower()
    office = (feature['office'] or '').lower()
    area = feature.geometry().area() #площадь в кв метрах
    
    if btype in ['apartments', 'house', 'residential', 'dormitory', 'terrace']:
        return 0 #предполагаю отсутствие рабочих мест в квартире
    if btype in ['commercial', 'retail', 'supermarket', 'mall'] or shop or office:
        if area > 0:
            return max(1, int(area / 20)) #место на 20 кв метр
        else:
            return 5
    if btype in ['industrial', 'warehouse', 'factory']:
        if area > 0:
            return max(5, int(area / 50)) #место на 50 кв метр
        else:
            return 10
    if amenity in ['school', 'college', 'university']:
        return 100
    if amenity in ['hospital', 'clinic']:
        return 200
    if amenity in ['kindergarten']:
        return 20
    if btype == 'hotel' or 'hotel' in amenity:
        return 30
    #для всех остальных - мои любимые building:yes
    if area > 0:
        return max(1, int(area / 50))
    else:
        return 1

#+ поле
if buildings_utm.fields().indexFromName('jobs_est') == -1:
    buildings_utm.dataProvider().addAttributes([QgsField('jobs_est', QVariant.Int)]) #целочисленное 32 бита
    buildings_utm.updateFields()
    print("'jobs_est' добавлено во временный слой")

#заполнение
buildings_utm.startEditing()
count = 0
for feat in buildings_utm.getFeatures():
    feat['jobs_est'] = estimate_jobs(feat)
    buildings_utm.updateFeature(feat)
    count += 1
    if count % 10000 == 0:
        print(f"обработано {count}")
buildings_utm.commitChanges()
print(f"'jobs_est' добавлено для {count} зданий")

#тотал
jobs_by_zone = {}
for zone_feat in zones_utm.getFeatures():
    zone_name = zone_feat['name']
    zone_geom = zone_feat.geometry()
    total = 0
    #проверяем пересечение
    for bld_feat in buildings_utm.getFeatures():
        if bld_feat.geometry().intersects(zone_geom):
            total += bld_feat['jobs_est'] or 0
    jobs_by_zone[zone_name] = total
    print(f"{zone_name}: {total} рабочих мест")

#обновляем в исходнике
if zones_src.fields().indexFromName('jobs') == -1:
    zones_src.dataProvider().addAttributes([QgsField('jobs', QVariant.Int)])
    zones_src.updateFields()
    print("jobs' добавлено в исходный слой")

zones_src.startEditing()
for zone_feat in zones_src.getFeatures():
    name = zone_feat['name']
    if name in jobs_by_zone:
        zone_feat['jobs'] = jobs_by_zone[name]
        zones_src.updateFeature(zone_feat)
zones_src.commitChanges()
print("'jobs' обновлено")

print("thats all")