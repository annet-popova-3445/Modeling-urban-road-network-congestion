import networkx as nx
import itertools
giant_nodes = set(max(nx.connected_components(G), key=len))
missing = []
for fid, node in centroid_to_node.items():
    if node not in giant_nodes:
        missing.append(fid)
        name = centroids.getFeature(fid)['name'] if centroids.getFeature(fid) else str(fid)
        print(f"Центроид {fid} ({name}) вне гигантского компонента")

if not missing:
    print("Все центроиды в гигантском компоненте.")
else:
    print(f"Пропущено {len(missing)} центроидов")

#предполагается, что G и centroid_to_node уже определены
#фильтр только центроидов, которые в гигантском компоненте
giant_nodes = set(max(nx.connected_components(G), key=len))
valid_centroids = {fid: node for fid, node in centroid_to_node.items() if node in giant_nodes}
print(f"Центроидов в гигантском компоненте: {len(valid_centroids)}")
#проверка наличия путей между каждой парой
no_path_pairs = []
for (fid1, node1), (fid2, node2) in itertools.combinations(valid_centroids.items(), 2):
    if nx.has_path(G, node1, node2):
        print(f"Путь между {fid1} и {fid2} существует")
    else:
        print(f"Нет пути между {fid1} и {fid2}")
        no_path_pairs.append((fid1, fid2))
print(f"Всего пар без пути: {len(no_path_pairs)}")
