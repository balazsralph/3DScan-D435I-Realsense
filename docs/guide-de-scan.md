# Guide de scan

## Avant de commencer

- Ferme `realsense-viewer` : la caméra ne sert qu'à un programme à la fois. Si elle est occupée,
  l'application réessaie puis la réinitialise toute seule.
- Branche-la sur un port **USB 3** (bleu). En USB 2, l'application te prévient.
- Lumière **blanche et diffuse** (LED). Évite les halogènes : leur infrarouge brouille la mesure.
- Surfaces **brillantes, noires ou transparentes** : voile de talc, shampoing sec ou spray matifiant.

## Mode Objet

| | Petit objet | Objet |
|---|---|---|
| Taille | jusqu'à 10 cm | 10 à 60 cm |
| Distance idéale | 15 – 27 cm | 30 – 65 cm |
| Finesse (voxel) | 0,8 mm | 2 mm |
| Unité de profondeur | 0,1 mm | 0,1 mm |

1. Pose l'objet sur une **grande surface plane** (table, sol), pas sur un petit socle, avec rien d'autre
   à moins de 2 cm. Des objets plus loin sur la table aident au contraire le suivi.
2. Place la croix sur l'objet et cherche **« Distance idéale »** en vert.
3. **Espace** (ou le bouton rond) : l'enregistrement démarre. Tourne **lentement** autour de l'objet,
   environ un tour en 40 à 60 s, caméra inclinée d'environ 45°, puis fais un second tour plus haut.
   Garde toujours l'objet dans l'image.
4. **Espace** : fin. Si « Construire le modèle à la fin » est coché, la construction démarre et le
   modèle apparaît dans l'aperçu 3D.
5. Vérifie une cote au **pied à coulisse** : d'un scan à l'autre, les cotes varient d'environ ±1 mm.

Le dessous de l'objet, posé sur le support, n'est pas scanné : le modèle est ouvert à la base.

### Plateau tournant

Caméra fixe et objet qui tourne : baisse **« Fond coupé à »** juste derrière le plateau, pour que seuls
l'objet et le plateau, qui tournent ensemble, soient vus. Sinon le décor immobile contredit le mouvement.

## Couverture en direct : ce qui reste à scanner

Pendant l'enregistrement (avec une carte NVIDIA), une grille de marqueurs se pose sur l'image caméra :

| Marqueur | Signification | Que faire |
|---|---|---|
| ▲ rouge | la caméra voit une surface que le modèle n'a pas encore, ou presque pas | passer plus de temps sur cette zone |
| ● ambre | surface vue quelques fois seulement | encore un passage |
| ● vert | surface vue assez longtemps (≈ 1,5 s) | rien, c'est bon |

La pastille en haut à droite donne la part de l'image à scanner et la part bien vue. Dans l'aperçu 3D,
le modèle en cours prend les mêmes couleurs : fais-le tourner à la souris pour repérer les zones rouges
ou les trous, puis va les filmer. La case **« Couverture en direct »** permet de revenir aux couleurs
réelles.

## Mode Salle entière

- Distance utile : 0,6 à 3 m (profondeur gardée jusqu'à 3,5 m), finesse 8 mm, unité 1 mm, laser au
  maximum.
- Pars d'un coin, caméra à hauteur de poitrine, et longe les murs **lentement**.
- Garde toujours du relief dans l'image : meubles, encadrements. Un mur blanc seul ne se suit pas.
- Reviens à ton point de départ : la correction de dérive s'en sert pour refermer la boucle.
- Le modèle est posé sur son sol (z = 0), en mètres.

## Fusionner plusieurs datasets

Utile pour **retourner un objet** et scanner son dessous, ou pour **compléter une salle** en plusieurs
passages.

1. Dans le panneau **Datasets**, coche les scans à réunir (case à gauche de chaque vignette).
2. Clique sur **« Fusionner »**. Les modèles manquants sont construits, puis chaque modèle est recalé sur
   le premier coché, sur la forme du sujet.
3. Une surface unique et étanche est calculée (Poisson). Le résultat apparaît comme un nouveau dataset
   « Fusion », qu'on peut exporter ou refaire.

Un scan qui ne coïncide pas assez avec le premier est **écarté** (indiqué dans le journal) plutôt que de
fausser le modèle. Pour un objet retourné, garde un peu de recouvrement entre les deux scans (les flancs).

## Exporter le modèle

Bouton **« Exporter ▾ »** en haut de l'aperçu 3D, quand un modèle est affiché :

| Format | Pour | Unités · orientation | Couleurs |
|---|---|---|---|
| STL | impression 3D, CAO | mm (objet) · z vers le haut | non |
| OBJ | Blender, Maya, 3ds Max | mètres · y vers le haut | oui |
| GLB (glTF) | Blender, web, réalité augmentée | mètres · y vers le haut | oui |
| PLY | MeshLab, CloudCompare | mm (objet) · z vers le haut | oui |

Dans Blender : *Fichier → Importer → glTF 2.0* (ou *Wavefront OBJ*). L'objet arrive à sa vraie taille,
debout, avec ses couleurs (attribut de couleur des sommets).

## Messages et solutions

| Message | Cause | Que faire |
|---|---|---|
| Trop près / Trop loin | sujet hors de la plage du mode | reculer / se rapprocher ; sinon changer de mode |
| Suivi perdu · ralentis | mouvement trop rapide ou image sans relief | ralentir, revenir vers une zone déjà scannée |
| Seules N images sur M ont servi | décrochages fréquents | refaire le scan plus lentement |
| Images perdues pendant l'enregistrement | disque trop lent | libérer de la place, disque plus rapide |
| Caméra indisponible | câble, autre programme | rebrancher, fermer realsense-viewer, « Reconnecter la caméra » |
| Sol non trouvé (salle) | sol peu visible | filmer davantage le sol en début de scan |
| Fusion : dataset écarté | les scans ne se recouvrent pas assez, ou ne montrent pas le même sujet | refaire un scan avec plus de recouvrement |

## Où sont mes fichiers ?

```
datasets/2026-11-23_14-03-12_objet/
├── couleur/000000.jpg …        images couleur
├── profondeur/000000.png …     profondeur 16 bits alignée sur la couleur
├── session.json                caméra, mode, intrinsèques, durée
├── thumbnail.jpg
└── reconstruction/
    ├── modele.ply              modèle en couleurs (mm pour un objet, m pour une salle)
    ├── modele.stl              même géométrie, pour l'impression ou la CAO
    ├── scene.ply               objet : scène entière, pour contrôle
    └── build.json              cotes, images utilisées, durées, réglages
```

Une session prend environ 0,6 Go par minute : supprime les anciens dossiers de `datasets/` au besoin.
