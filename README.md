# VidioAI

Site de génération de vidéos par IA. Chaque visiteur utilise ses propres clés Replicate ou Luma AI.

## Fichiers

- `index.html` : le site complet.
- `app.py` : serveur Flask qui lance les vraies générations et enregistre les vidéos.
- `requirements.txt` : les paquets Python à installer.
- `.gitignore` : évite d'envoyer les vidéos générées sur GitHub.

## Tester sur le téléphone (Termux)

```
pip install -r requirements.txt
python app.py
```

Ouvrez `http://localhost:5000`. Le site détecte le serveur tout seul et lance de vraies générations.
Sans serveur, le site passe en mode aperçu : la génération est simulée et aucune vidéo n'est produite.

## Publier

**Le site seul (GitHub Pages).** Envoyez `index.html` sur GitHub. GitHub Pages n'exécute pas Python, donc le site reste en mode aperçu.

**Avec la génération réelle.** Hébergez `app.py` chez un hébergeur Python. Render convient (offre gratuite, sans carte bancaire d'après sa documentation) : créez un « Web Service » relié à votre dépôt GitHub. PythonAnywhere ne convient pas en offre gratuite : ses comptes gratuits ne joignent que des sites autorisés, et rien ne garantit que Replicate et Luma en fassent partie. Commande de démarrage :

```
gunicorn app:app --timeout 120 --threads 4
```

Sur Render, la commande de build est `pip install -r requirements.txt`.

Deux possibilités :

1. Le serveur affiche aussi le site (rien à régler).
2. Le site reste sur GitHub Pages et le serveur est ailleurs :
   - dans `index.html`, remplacez `const BACKEND_URL = '';` par l'adresse du serveur, par exemple `'https://mon-serveur.example.com'` ;
   - sur le serveur, définissez la variable `ALLOWED_ORIGIN` avec l'adresse du site, par exemple `https://monpseudo.github.io`.

## Réglages (variables d'environnement)

| Variable | Rôle | Valeur par défaut |
|---|---|---|
| `REPLICATE_MODEL` | Modèle Replicate utilisé | `wan-video/wan-2.2-t2v-fast` |
| `LUMA_MODEL` | Modèle Luma | `ray-3.2` |
| `LUMA_RESOLUTION` | Résolution Luma (`360p` pour un brouillon moins cher, `540p`, `720p`, `1080p`) | `720p` |
| `LUMA_DURATION` | Durée Luma (`5s` ou `10s`) | `5s` |
| `LUMA_ASPECT_RATIO` | Format de l'image Luma | `16:9` |
| `ALLOWED_ORIGIN` | Adresse du site autorisée à appeler le serveur (plusieurs adresses séparées par des virgules) | vide |
| `HOST` / `PORT` | Adresse et port d'écoute | `127.0.0.1` / `5000` |

## À savoir

- Les comptes et les créations sont enregistrés dans le navigateur de chaque visiteur. Ils ne passent pas d'un appareil à l'autre.
- Les clés API restent dans le navigateur. Le serveur les reçoit seulement pour relayer la demande et ne les enregistre pas.
- Chaque génération est facturée par Replicate ou Luma sur le compte du visiteur.
- Luma : créez la clé sur platform.lumalabs.ai (elle commence par `luma-api-`). Il n'y a pas d'offre gratuite : ajoutez des fonds sur le compte avant le premier essai. Les anciennes clés Dream Machine sont encore acceptées tant que Luma les laisse fonctionner.
- Les vidéos sont copiées dans le dossier `videos/` du serveur, car Replicate supprime ses fichiers au bout d'une heure. Videz ce dossier de temps en temps. Sur une offre gratuite, ce dossier peut être effacé à chaque redémarrage : les anciennes vidéos ne s'ouvrent alors plus.
- Sur Render gratuit, le serveur s'endort après 15 minutes sans visite. Le premier chargement suivant prend environ une minute.
- L'offre Pro Studio est affichée « Bientôt » : aucun paiement n'est branché.
