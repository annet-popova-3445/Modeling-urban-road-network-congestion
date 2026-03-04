zones = QgsProject.instance().mapLayersByName('novosibirsk_db — boundary')[0]
print("{:<25} {:>15} {:>15}".format("район", "население", "рабочие места"))
print("-" * 55)
for feat in zones.getFeatures():
    name = feat['name'] if feat['name'] else "неизвестно"
    pop = feat['population'] if feat['population'] else 0
    jobs = feat['jobs'] if feat['jobs'] else 0
    print("{:<25} {:>15} {:>15}".format(name, pop, jobs))