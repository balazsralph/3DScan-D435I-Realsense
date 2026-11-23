# Précision et mesures

Toutes les valeurs ci-dessous ont été mesurées sur cette machine (RTX 5070 Ti Laptop, Open3D 0.20, CUDA).

## Scène synthétique (test automatique)

`tests/integration/test_pipeline.py` rend par lancer de rayons une boîte de **150 × 100 × 80 mm** posée
sur une table avec quelques objets autour, vue depuis 600 positions sur un tour complet (≈ 5 mm par image,
comme un scan à la main). Le dataset passe ensuite par toute la chaîne.

| Configuration du suivi | Dérive max (600 images) | Cotes obtenues |
|---|---|---|
| Bande de suivi 4 voxels, sans prédiction ni correction | 36 mm · 1,7° | — |
| Bande de suivi 8 voxels | 18 mm · 0,5° | 157,2 × 106,0 × 83,2 mm |
| + correction de dérive par tronçons (réglage actuel) | — | **155,3 × 106,1 × 80,8 mm** |
| Poses exactes (borne : ce que la fusion peut faire de mieux) | 0 | 152,1 × 102,2 × 80,1 mm |

Sans aucun objet autour de la boîte (un seul objet sur un plan infini, cas dégénéré pour un suivi
point-à-plan), la dérive passe de 350 mm / 44° à 94 mm / 11° grâce à la bande de suivi large.

## Scan réel d'un petit objet (1676 images, à la main)

| | Avant (réglages repris de scan3d) | Maintenant |
|---|---|---|
| Morceaux créés par des décrochages | 11 | 3 à 6 |
| Images utilisées | 1592 | 1595 à 1613 |
| Cotes | 96,7 × 92,2 × 23,0 mm | 95,5 à 97,4 × 91,1 à 93,9 × 22,3 à 24,5 mm |
| Durée de construction | 36 s | 45 à 48 s |

Le suivi sur GPU n'est pas parfaitement déterministe d'une exécution à l'autre : les cotes d'un même scan
varient d'environ ±1 mm. C'est cohérent avec la précision de concordance du D435i entre deux angles de vue
(1 à 2 mm à 20 cm). **Vérifie toujours une cote au pied à coulisse.**

## Correctifs de fiabilité

- **Score de recouvrement** : Open3D le rapporte à toute l'image. Avec le fond coupé (ex. 24 % de pixels
  mesurés), il plafonnait sous le seuil même pour un suivi parfait (92 % des pixels mesurés coïncidaient) :
  un ancien dataset sur plateau tournant était entièrement rejeté. Il est désormais rapporté aux pixels
  mesurés.
- **Prédiction de mouvement** : l'odométrie projective ne retrouvait que 2,25 mm d'un déplacement de
  4,71 mm le long d'une table, contre 4,46 mm pour un ICP par plus proches voisins. Partir de la pose
  prédite ne lui laisse à corriger que l'accélération.
- **Caméra occupée au démarrage** (« Device or resource busy ») : nouvel essai, puis réinitialisation
  matérielle avec attente du retour de la caméra sur le bus USB.

## Limites connues

- Pas de graphe de poses global : la correction par tronçons réduit la dérive sans l'annuler.
- La fusion TSDF épaissit légèrement l'emprise (≈ +2 mm sur 150 mm avec des poses exactes).
- Le dessous d'un objet posé n'est pas reconstruit.
