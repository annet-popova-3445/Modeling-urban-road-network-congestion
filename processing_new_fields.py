from qgis.core import (
    QgsProject, QgsField, QgsExpression, 
    QgsExpressionContext, QgsExpressionContextScope,
    QgsDistanceArea, QgsCoordinateReferenceSystem
)
from qgis.PyQt.QtCore import QVariant

layer = QgsProject.instance().mapLayersByName('novosibirsk_db — roads')[0]
layer.startEditing()

fields_to_add = {
    'length_km': QVariant.Double,
    'lanes_numeric': QVariant.Int,
    'maxspeed_numeric': QVariant.Int,
    'capacity': QVariant.Int,
    'free_flow_time': QVariant.Double
}

for field_name, field_type in fields_to_add.items():
    if layer.fields().indexFromName(field_name) == -1:
        layer.dataProvider().addAttributes([QgsField(field_name, field_type)])
        layer.updateFields()
        print(f"дбавлено: {field_name}")

#метры, градусы
da = QgsDistanceArea()
da.setEllipsoid('WGS84')
crs = QgsCoordinateReferenceSystem('EPSG:4326')
da.setSourceCrs(crs, QgsProject.instance().transformContext())

#контекст
context = QgsExpressionContext()
scope = QgsExpressionContextScope() 
context.appendScope(scope) #область в пустой контекст

#длина
if 'length_km' in [f.name() for f in layer.fields()]:
    for i, feature in enumerate(layer.getFeatures()):
        geom = feature.geometry()
        if geom and not geom.isEmpty():
            length_m = da.measureLength(geom)  #длина в метрах
            length_km = length_m / 1000.0
            feature['length_km'] = length_km
            layer.updateFeature(feature)
        if i % 5000 == 0:
            print(f"обработано {i} объектов")
    print("длина рассчитана")

#количество полос
if 'lanes_numeric' in [f.name() for f in layer.fields()]:
    exp_lanes = QgsExpression('regexp_substr("lanes", \'\\\\d+\')') #страшная регулярка
    for feature in layer.getFeatures():
        scope.setFeature(feature)
        value = exp_lanes.evaluate(context) 
        if value is not None and value != '':
            try:
                lanes = int(float(value))
            except:
                lanes = 1
        else:
            #по типу дороги
            highway = feature['highway'] if feature['highway'] else ''
            if 'motorway' in highway or 'trunk' in highway:
                lanes = 2
            else:
                lanes = 1
        feature['lanes_numeric'] = lanes
        layer.updateFeature(feature)
    print("количество полос рассчитано")

#скорость
if 'maxspeed_numeric' in [f.name() for f in layer.fields()]:
    exp_speed = QgsExpression('regexp_substr("maxspeed", \'\\\\d+\')') #+ регулярка
    for feature in layer.getFeatures():
        scope.setFeature(feature)
        value = exp_speed.evaluate(context)
        if value is not None and value != '':
            try:
                speed = int(float(value))
            except:
                speed = None
        else:
            speed = None
        
        if not speed:
            #по типу
            highway = feature['highway'] if feature['highway'] else ''
            if 'motorway' in highway:
                speed = 90
            elif 'trunk' in highway:
                speed = 70
            elif 'primary' in highway:
                speed = 60
            elif 'secondary' in highway:
                speed = 50
            elif 'tertiary' in highway:
                speed = 40
            elif 'residential' in highway or 'living' in highway:
                speed = 30
            else:
                speed = 50
        feature['maxspeed_numeric'] = speed
        layer.updateFeature(feature)
    print("скорость рассчитана")

#пропускная способность
if 'capacity' in [f.name() for f in layer.fields()]:
    for feature in layer.getFeatures():
        lanes = feature['lanes_numeric'] if feature['lanes_numeric'] else 1
        if lanes >= 3:
            capacity = 5400
        elif lanes == 2:
            capacity = 3600
        else:
            capacity = 1800
        feature['capacity'] = capacity
        layer.updateFeature(feature)
    print("пропускная способность рассчитана")

#время свободного потока
if 'free_flow_time' in [f.name() for f in layer.fields()]:
    for feature in layer.getFeatures():
        length = feature['length_km'] if feature['length_km'] else 0.1
        speed = feature['maxspeed_numeric'] if feature['maxspeed_numeric'] else 50
        #время  = расстояние / скорость 
        time_h = length / speed
        feature['free_flow_time'] = time_h
        layer.updateFeature(feature)
    print("время рассчитано")

layer.commitChanges()
print("\n Все успешно")