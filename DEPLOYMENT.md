# Mise en production d’Events Connect

## Variables à configurer

Le démarrage en production échoue volontairement si une configuration indispensable manque. Dans Render, renseignez les variables privées déclarées dans `render.yaml` :

- `SECRET_KEY` : secret aléatoire propre à la production.
- `DATABASE_URL` : URL d’une base PostgreSQL persistante. Ne pas utiliser SQLite sur le disque temporaire de Vercel ou Render.
- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` : nouveau jeu de clés Cloudinary.
- `ADMIN_PASSWORD` : mot de passe administrateur (au moins 16 caractères), à saisir comme secret privé dans Render. Cette option est la plus simple; elle est comparée en temps constant et n’est jamais écrite en base ni dans les journaux.
- `ADMIN_PASSWORD_HASH` : alternative si vous préférez stocker une empreinte. Si `ADMIN_PASSWORD` est défini, il est prioritaire.
- `SUPPORT_EMAIL` ou `SUPPORT_WHATSAPP` : au moins un moyen de contact public. WhatsApp doit être au format international, chiffres uniquement.
- `LEGAL_OPERATOR_NAME`, `LEGAL_OPERATOR_ADDRESS`, `HOSTING_PROVIDER` : informations exactes de l’exploitant et de l’hébergeur.
- `LEGAL_OPERATOR_REGISTRATION` : identifiant d’immatriculation applicable, si requis.
- `RATELIMIT_STORAGE_URI` : facultatif pour le pilote à un worker; configurer un stockage Redis partagé avant d’augmenter le nombre de workers ou d’instances.

Si vous utilisez `ADMIN_PASSWORD_HASH`, générez l’empreinte localement avec une invite masquée, puis copiez uniquement le résultat dans le gestionnaire de secrets de l’hébergeur :

```powershell
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Mot de passe admin: ')))"
```

Ne mettez jamais de valeur secrète dans Git ou dans une commande conservée dans l’historique du terminal. Configurez une seule des deux variables admin pour éviter toute ambiguïté.

## Avant le premier déploiement

1. Créez et configurez la base PostgreSQL, puis sauvegardez les données existantes. Les migrations ajoutent les empreintes de mots de passe et placent les anciens avis en attente de modération.
2. Les anciennes clés Cloudinary étaient présentes dans une version du code. Révoquez-les et créez-en de nouvelles avant de configurer les variables Render.
3. Configurez toutes les variables ci-dessus, puis déployez. L’instance n’utilise plus de secrets Cloudinary ou de clé Flask par défaut.
4. Ouvrez `/admin`, connectez-vous, contrôlez manuellement chaque prestataire avant de lui attribuer le badge, puis contrôlez les avis avant publication.
5. Complétez et faites vérifier les mentions légales, conditions et politique de confidentialité selon l’identité de l’exploitant, les fournisseurs effectivement utilisés et les règles applicables au Sénégal.
6. Testez les pages, l’inscription, la connexion, l’envoi WhatsApp, les photos et la suppression depuis un téléphone et une connexion mobile avant d’inviter largement.

La vérification du badge doit se faire hors plateforme (par exemple, confirmer le numéro et l’activité avec le prestataire). L’approbation d’un avis doit reposer sur une vérification raisonnable de l’expérience déclarée; le nom fourni seul ne suffit pas à certifier un client.