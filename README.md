# 3DScan-D435I-Realsense

Scanner 3D pour la caméra de profondeur **Intel® RealSense™ D435i** : capture guidée, reconstruction 3D
visible **en direct**, puis construction d'un modèle maillé précis (PLY en couleurs + STL).

---

## Version 2 : application « Scanner 3D »

Une fenêtre unique, pensée pour être intuitive :

1. **Accueil : que veux-tu scanner ?**
   - **Objet** : petit (≤ 10 cm) ou moyen (10 à 60 cm). Tu tournes autour d'un objet posé sur une
     surface plane ; il est isolé, posé à plat et coté en millimètres.
   - **Salle entière** : tu parcours une pièce caméra en main ; la scène est nettoyée et posée sur
     son sol, en mètres.

   Chaque mode règle la caméra (preset, unité de profondeur, portée, filtres) et la reconstruction
   (taille du voxel, garde-fous du suivi, nettoyage).
2. **Espace de travail en trois colonnes**
   - **Datasets** (à gauche) : liste avec vignettes, aperçu image par image (couleur ou profondeur),
     infos (durée, images perdues, modèle déjà construit et ses cotes), et le bouton
     **« Construire le modèle 3D »** (qualité Standard ou Détaillé, lissage).
   - **Caméra en direct** (au centre) : image couleur avec la profondeur superposée, vue profondeur
     avec légende, réticule, guidage de distance (« Trop près / Distance idéale / Trop loin »), état du
     suivi, coupe du fond réglable, gros bouton d'enregistrement (ou **Espace**).
   - **Aperçu 3D** (à droite du direct) : nuage de points de l'image courante, puis **modèle qui se
     remplit en direct pendant l'enregistrement** (GPU), puis le modèle construit. Rendus Couleur,
     Relief et Maillage, cotes sur les axes, vues iso et dessus, capture d'écran.
3. À la fin d'un enregistrement, le modèle peut se **construire automatiquement**. Pendant la
   construction, la reconstruction apparaît progressivement dans l'aperçu 3D.

### Installation

```bash
git clone https://github.com/balazsralph/3DScan-D435I-Realsense.git
cd 3DScan-D435I-Realsense
python3 -m venv .venv          # ou : uv venv
.venv/bin/pip install -e ".[dev]"
./scanner3d.sh
```

- Python ≥ 3.10, caméra branchée en **USB 3**, règles udev de librealsense installées.
- Carte NVIDIA conseillée : le paquet `open3d` pour Linux inclut CUDA (aperçu en direct et
  construction bien plus rapides). Sans CUDA, tout fonctionne sur CPU, sans la reconstruction en direct.
- Sous Wayland, l'application passe automatiquement par XWayland (exigé par la vue 3D VTK).

### Utilisation

```bash
./scanner3d.sh                       # écran d'accueil
./scanner3d.sh --mode objet_petit    # directement un mode (objet_petit, objet, salle)
python -m scanner3d.build DOSSIER    # construire un dataset sans interface
python -m scanner3d.build DOSSIER --qualite fin --profondeur-max 0.45 --profil objet
```

Les datasets sont enregistrés dans `datasets/<date>_<mode>/` (`couleur/`, `profondeur/`,
`session.json`, `thumbnail.jpg`). Le modèle est écrit dans `reconstruction/` à l'intérieur du dataset :
`modele.ply` (couleurs), `modele.stl`, `scene.ply` (objet : scène entière, pour contrôle) et `build.json`
(cotes, images utilisées, durées).

Le bouton **« + Dossier »** ajoute des datasets existants. Trois formats sont reconnus :

| Format | Fichier repère | Origine |
|---|---|---|
| Scanner 3D | `session.json` | cette application |
| scan3d | `intrinseques.json` + `couleur/` `profondeur/` | projet `scan3d` (petit objet) |
| Open3D | `camera_intrinsic.json` + `color/` `depth/` | scripts v1 de ce dépôt (profondeur en 0,1 mm) |

La reconstruction ne touche jamais aux fichiers existants : elle écrit uniquement dans `reconstruction/`.

### Conseils de scan

- **Objet** : grande surface plane, rien d'autre à moins de 2 cm, lumière blanche diffuse ; surfaces
  brillantes ou noires : voile de talc ou spray matifiant. Tourne lentement (un tour en 40-60 s), puis
  un second tour plus haut. Des objets plus loin sur la table aident le suivi.
- **Plateau tournant** : baisse « Fond coupé à » pour ne garder que l'objet et le plateau.
- **Salle** : pars d'un coin à hauteur de poitrine, longe les murs lentement, garde des meubles dans
  l'image (pas un mur blanc seul) et termine à ton point de départ.

### Pourquoi c'est plus fiable et plus précis (mesures)

| Amélioration | Effet mesuré |
|---|---|
| **Score de suivi rapporté aux pixels mesurés** (Open3D le calcule sur toute l'image) | Ancien dataset du dépôt (24 % de pixels mesurés) : suivi impossible → suivi complet. Scan `scan3d` réel : 11 → 4 à 6 morceaux. |
| **Bande de troncature large pour le suivi** (8 voxels), fine pour la fusion finale (parois minces préservées) | Orbite synthétique de 600 images : dérive 36 → 18 mm, rotation 1,7° → 0,5°. |
| **Prédiction à vitesse constante** avant le suivi (comme KinectFusion) | L'odométrie projective ne rattrape qu'environ la moitié d'un glissement le long d'une table ; on lui laisse seulement l'accélération à corriger. |
| **Correction de la dérive par tronçons** de 200 images, recalés par ICP sur tout ce qui précède | Boîte synthétique de 150 × 100 × 80 mm : 157,2 × 106,0 × 83,2 → 155,3 × 106,1 × 80,8 mm (poses exactes : 152,1 × 102,2 × 80,1). |
| Retrait des **pixels « volants »** aux bords, plage de profondeur par mode, **filtre spatial Intel** dans le domaine des disparités, sans bouchage de trous | La TSDF n'intègre que des mesures fiables. |
| **Morceaux** créés quand le suivi décroche, recalés par RANSAC + ICP sur la forme de l'objet, rejetés s'ils coïncident mal | Pas de sujet dédoublé par un mauvais raccrochage. |
| Isolement de l'objet (plan du support, groupe visé), mise à plat, **cotes** ; salle : poussières retirées, **sol à z = 0** | Fichiers directement utilisables (impression, CAO). |
| Lissage de Taubin | Gomme les marches de la grille de voxels sans réduire le volume. |
| Caméra : mode avancé activé automatiquement, reprise si elle est occupée puis réinitialisation matérielle, alerte USB 2, contrôle d'espace disque, écriture asynchrone (aucune image perdue) | Moins d'échecs au démarrage et pendant la capture. |

Temps mesurés (RTX 5070 Ti, scan réel de 1676 images d'un petit objet) : environ 45 s de construction.
Le tout premier lancement compile les noyaux CUDA d'Open3D (environ 3 min, une seule fois).
D'un scan à l'autre, les cotes d'un petit objet varient d'environ ±1 mm : vérifie une cote au pied à
coulisse.

### Architecture

```
scanner3d/
  profiles.py      profils de scan (caméra + reconstruction), qualités
  camera.py        RealSense : preset, filtres, profondeur alignée, reprise sur erreur
  depth.py         nettoyage de profondeur, coloration, nuage de points, guidage de distance
  dataset.py       lecture des 3 formats, création des sessions
  recorder.py      enregistrement asynchrone d'un dataset
  slam.py          suivi image -> modèle (Open3D, GPU), garde-fous, fusion TSDF
  registration.py  recalage des morceaux, correction de dérive par tronçons
  extraction.py    isolement de l'objet, mise à plat, cotes
  room.py          nettoyage de salle, mise à niveau du sol
  build.py         chaîne complète + ligne de commande
  presets/         presets caméra JSON par mode
  gui/             interface Qt (PySide6) et vue 3D VTK (PyVista)
tests/             63 tests, dont un test de bout en bout sur une scène synthétique rendue par lancer de rayons
```

```bash
.venv/bin/python -m pytest          # tests
.venv/bin/ruff check scanner3d tests
```

### Pistes étudiées

- [Open3D, Dense RGB-D SLAM](https://www.open3d.org/docs/latest/tutorial/t_reconstruction_system/index.html)
  et son exemple `dense_slam_gui.py` : modèle reconstruit affiché à côté de l'image d'entrée.
- [Filtres de post-traitement librealsense](https://dev.realsenseai.com/docs/post-processing-filters/) :
  ordre recommandé, filtrage dans le domaine des disparités, 848 × 480 pour le D435.
- Projets voisins : [Handy3DScanner](https://www.github.com/state-of-the-art/Handy3DScanner), le scanner
  portable RTAB-Map de Frank26080115, Scantastic (licence non commerciale : rien n'en est repris).
- Le projet local `scan3d` : garde-fous du suivi mesurés sur de vrais scans, isolement de l'objet.

### Limites et prochaines étapes

- Pas encore de graphe de poses global (fermeture de boucle complète) : la correction par tronçons
  réduit la dérive sans l'annuler.
- L'IMU du D435i n'est pas utilisée (elle pourrait fixer la verticale et aider le suivi).
- Le dessous d'un objet posé n'est pas scanné.

---

## Version 1 (scripts d'origine)

Les scripts d'origine sont conservés (`3D-Scan-Record-RGBD.py`, `3D-Construct-RGBD.py`, `files/`, presets JSON).

3D scan and reconstruction system using Intel® RealSense™ Depth Camera D435i

This project works in the system:
- Intel® RealSense™ Depth Camera D435i with its tripod
- Open 3D 12.0
- Python 3.8
- Ubuntu 20.04 LTS
- Processor: Intel® Core™ i5-9600K OC @5.1GHz
- Motherboard: Asus ROG Strix Z390-E Gaming
- RAM: Corsair Vengeance LPX 32GB DDR4 OC @4200MHz
- GPU: NVIDIA GeForce RTX2060 Super, with CUDA 11.5
- Storage: Samsung 850 Evo SATA III 1TB
- Turn table (set the first loop of frame as the indicator of one rotation by changing the `frame_loop_at`)
- Object above the turn table with the maximum recommended height of 25 cm

Procedure:
1. Place the camera 30 cm in front of the object.
2. Turn on the turn table with the object to be scanned on it.
3. Run `3D-Scan-Record-RGBD.py`.
4. Input the distance (cm) from the object's centre to the camera.
5. Input the object's maximum radius (cm) from the centre.
6. Let the camera scan the object until the 360-degree rotation is achieved (hit `spacebar` to pause and `ESC` to exit). The program will exit automatically and save the capture in the `dataset` directory.
7. Run `3D-Construct-RGBD.py` to begin the reconstruction system.
8. Program will show the results of the `.ply` file from the reconstruction system.

Note: if the object is quite complex in terms of the surface (rough surface with many indentations), the scanning process might need to be done twice, from a higher-level point-of-view and lower-level point-of-view to ensure all surfaces are already captured and not leaving holes in the reconstructed model.

Scan position example:

![Screenshot from 2022-10-31 22-43-22](https://user-images.githubusercontent.com/26058697/199151046-d4d7414f-839a-487c-9beb-714deefa5afa.png)

Depth scanning before (top) and after (bottom) optimization of the camera preset:

![Screenshot from 2022-10-31 21-06-55](https://user-images.githubusercontent.com/26058697/199151128-0f753cb9-3154-4647-a280-232bdab6b97c.png)

Result from default and custom/optimized camera preset and reconstruction configuration:

![Screenshot from 2022-10-31 21-07-56](https://user-images.githubusercontent.com/26058697/199151269-efa706c8-ad44-42eb-b1cb-76efcaa801c0.png)

Time elapsed for each of the reconstruction pipelines and its RMSE (Root Mean Square Error):

![Screenshot from 2022-10-31 21-09-58](https://user-images.githubusercontent.com/26058697/199151400-f32b206d-0b76-4fd2-97b3-c31a90466377.png)
