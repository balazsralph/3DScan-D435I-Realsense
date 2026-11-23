# Fonctionnement

## Chaîne de traitement

1. **Acquisition** (`acquisition/camera.py`) : le preset JSON du mode est chargé en mode avancé (activé
   automatiquement), l'unité de profondeur est imposée, la couleur et la profondeur sont lues en
   848 × 480 à 30 img/s (résolution recommandée par Intel pour le D435). Les modes Objet et Salle
   ajoutent le filtre spatial d'Intel dans le domaine des disparités, sans bouchage de trous.
   La profondeur est alignée sur la couleur.
2. **Enregistrement** (`acquisition/recorder.py`) : écriture dans un thread (JPEG 95 + PNG 16 bits),
   file de 8 s pour absorber un disque lent, contrôle d'espace libre, `session.json` à la fin.
3. **Nettoyage de la profondeur** (`depth.py`) : plage utile du mode et pixels « volants » retirés. Ces
   pixels, à mi-chemin entre un bord et le fond, sont détectés par un saut de profondeur relatif dans
   leur voisinage 3 × 3, en ignorant les trous voisins.
4. **Suivi** (`reconstruction/slam.py`) : odométrie image → modèle d'Open3D (TSDF sur GPU si CUDA).
   - Prédiction à vitesse constante : le modèle est raycasté à la pose attendue.
   - Bande de troncature de 8 voxels pour le suivi.
   - Score de recouvrement rapporté aux pixels mesurés.
   - Garde-fous : saut de pose maximal par image (seuil qui grandit après un rejet), recouvrement minimal.
   - Après N rejets consécutifs, un nouveau morceau commence plutôt que de risquer un mauvais
     raccrochage.
5. **Recalage** (`reconstruction/registration.py`)
   - Les morceaux sont recalés sur le plus long par RANSAC sur caractéristiques FPFH, puis ICP point-à-plan
     de plus en plus fin. Pour un objet, ce recalage se fait sur la forme de l'objet seul, le support plat
     tromperait RANSAC. Un morceau qui coïncide mal est rejeté.
   - Correction de dérive : chaque tronçon de 200 images est recalé par ICP sur l'union des précédents.
     Une correction douteuse est ignorée, les images restant à la pose du suivi.
6. **Fusion finale** : toutes les images retenues sont intégrées avec leurs poses corrigées, avec une
   bande fine (4 voxels pour un objet) qui préserve les parois minces.
7. **Finition**
   - Objet (`reconstruction/extraction.py`) : plan du support estimé par RANSAC puis affiné localement,
     choix du groupe que la caméra visait (point le plus proche de tous les axes de visée), coupe nette le
     long du plan, mise à plat, emprise alignée sur x/y, cotes.
   - Salle (`reconstruction/room.py`) : retrait des petits morceaux flottants, recherche du sol (plan
     horizontal le plus bas, « haut » déduit de la tenue de la caméra), sol posé à z = 0.
   - Lissage de Taubin (préserve le volume), export PLY + STL, `build.json`.

## Couverture en direct (`reconstruction/coverage.py`)

Chaque voxel de la TSDF compte ses observations (poids `uint16`, +1 par image intégrée). Toutes les
0,15 s, le modèle est raycasté depuis la pose courante sur une grille de 53 × 30 points (un tous les
16 pixels). Le poids de chaque point est interpolé sur les 8 voxels voisins (`index` × `interp_ratio`).
Ce raycast prend environ 5 ms ; le facteur de sous-échantillonnage interne d'Open3D doit être mis à 1,
sinon une grille aussi grossière ne renvoie rien. Niveaux : la caméra mesure mais le modèle est vide →
à scanner ; < 12 observations → faible ; < 45 → partiel ; sinon bien vu.

Pour le nuage 3D, la surface est extraite à trois seuils d'observations (1, 12, 45). Les extractions à
seuil plus élevé sont des sous-ensembles exacts de la première, ce qui permet de colorer chaque point par
simple comparaison de positions.

## Fusion (`reconstruction/merge.py`)

1. Les modèles manquants sont construits.
2. Chaque modèle (en mètres, avec normales et couleurs) est recalé sur l'union des modèles déjà acceptés :
   RANSAC sur FPFH puis ICP. Il faut au moins 25 % de recouvrement et un écart inférieur à 1,5 fois la
   tolérance du recalage des morceaux.
3. Les points sont réunis (sous-échantillonnage au voxel du profil) et une **reconstruction de Poisson**
   donne une surface étanche. La profondeur de l'octree est choisie pour avoir des cellules un peu plus
   fines que le voxel du scan. Les 4 % de sommets les moins soutenus par des mesures sont retirés, puis la
   surface est recadrée sur la boîte englobante des points ; pour un objet, les petits morceaux isolés
   sont retirés aussi.
4. Le résultat est écrit dans `datasets/<date>_fusion/` (`fusion.json` liste les sources, ce qui permet
   de refaire la fusion).

## Export (`export.py`)

STL et PLY restent dans les unités du modèle, z vers le haut, comme l'attendent les trancheurs et la CAO.
OBJ et GLB sont convertis en mètres, y vers le haut (glTF et importeurs de Blender). Le GLB est écrit
directement (glTF 2.0 binaire : `POSITION`, `NORMAL`, `COLOR_0`, indices `uint32`), car celui
d'Open3D 0.20 perd les couleurs des sommets.

## Modes (`profiles.py`)

| | Petit objet | Objet | Salle |
|---|---|---|---|
| Preset | `objet_petit.json` (Medium Density, disparity shift 60) | `objet.json` (Medium Density) | `salle.json` (Default, laser 360 mW) |
| Profondeur gardée | 11,5 – 34 cm | 20 cm – 1 m | 30 cm – 3,5 m |
| Voxel standard / détaillé | 0,8 / 0,6 mm | 2 / 1,5 mm | 8 / 6 mm |
| Saut max par image | 10 mm, 6° | 20 mm, 6° | 60 mm, 8° |
| Filtre spatial | non | oui | oui |
| Export | mm | mm | m |

La qualité « Détaillé » réduit le voxel de 25 %. Toutes les images servent toujours : en sauter double
le mouvement entre deux images et fait décrocher le suivi.

## Formats de datasets reconnus

| Format | Repère | Particularités |
|---|---|---|
| Scanner 3D | `session.json` | format natif |
| scan3d | `intrinseques.json`, `couleur/`, `profondeur/` | mode Petit objet par défaut |
| Open3D | `camera_intrinsic.json`, `color/`, `depth/` | profondeur en 0,1 mm, mode Objet par défaut |

La reconstruction écrit uniquement dans `reconstruction/` et ne modifie jamais les images.

## Interface (`gui/`)

- `app.py` : démarrage (XWayland sous Wayland, neutralisation des greffons Qt d'OpenCV).
- `main_window.py` : accueil puis espace de travail ; orchestre caméra, enregistrement et construction.
- `workers.py` : threads caméra, reconstruction en direct et construction. Les images passent par
  « dernière valeur » : jamais d'empilement si l'affichage prend du retard.
- `viewer3d.py` : vue VTK (nuage ou maillage, rendus couleur, relief et maillage, cotes).
- `live_panel.py` : image caméra avec réticule, guidage, marqueurs de couverture et légende.
- `start_screen.py`, `dataset_panel.py`, `live_panel.py`, `viewer_panel.py`, `widgets.py`, `theme.py`.

## Références

- [Open3D : système de reconstruction (tenseurs) et SLAM dense](https://www.open3d.org/docs/latest/tutorial/t_reconstruction_system/index.html)
- [librealsense : filtres de post-traitement](https://dev.realsenseai.com/docs/post-processing-filters/)
- Newcombe et al., *KinectFusion: Real-Time Dense Surface Mapping and Tracking*, ISMAR 2011
  (prédiction de pose, suivi image → modèle).
- Taubin, *A Signal Processing Approach to Fair Surface Design*, SIGGRAPH 1995 (lissage).
