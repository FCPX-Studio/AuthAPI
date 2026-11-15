# AuthAPI dans l'architecture événementielle (Postgres + Redis)

## Contexte
Postgres devient la seule source de vérité. Paddle sert d'interface d'administration : ses webhooks alimentent
la base via PadSync, et les événements transitent par Redis. AuthAPI n'appelle plus l'API client de Paddle
et ne reçoit plus de webhook HTTP. Il consomme l'événement de commande depuis Redis, crée les licences,
puis publie `licenseCreated` pour Web et MailTalker. Ce plan ne couvre que ce dépôt (AuthAPI).
Les autres services y figurent uniquement comme contrats.

## Points d'architecture à intégrer (au-delà du schéma proposé)
1. **Paddle ne peut pas écrire dans Redis.** Un webhook est un POST HTTPS. Il faut un point d'entrée HTTP
   qui vérifie la signature Paddle, puis publie l'événement brut dans Redis. Proposition : c'est PadSync
   (seul service exposé à Paddle). La vérification de signature et `Paddle.validate_webhook_signature`
   sortent donc d'AuthAPI.
2. **Redis Streams + consumer groups, pas Pub/Sub.** Pub/Sub perd les messages si un service est arrêté.
   Avec les Streams, chaque service a son propre groupe, acquitte avec `XACK`, et les messages non acquittés
   sont relivrés (`XAUTOCLAIM`).
   - `paddle:events` : événements Paddle bruts (produits par l'ingress PadSync). Consommés par le groupe
     `padsync` (tous les types) et le groupe `auth-api` (filtre sur `transaction.completed`).
   - `license:events` : `licenseCreated` (produit par AuthAPI). Consommé par les groupes `web` et
     `mailtalker`. Le `sendLicense` du schéma devient simplement le groupe `mailtalker` sur ce même
     événement : un seul événement, plusieurs consommateurs.
3. **L'ordre n'est pas garanti.** Paddle peut envoyer `transaction.completed` avant `customer.created`, ou
   PadSync peut être en retard. AuthAPI n'a besoin que de `customer_id` (déjà dans la transaction) :
   pas de FK de `UserLicense.customer_id` vers `User`, et pas d'attente du user. MailTalker lit l'email en
   base au moment d'envoyer, et réessaie si le user n'est pas encore synchronisé.
   Le catalogue (`ProductLicense`) reste une dépendance réelle. S'il manque, on ne fait pas d'`XACK`, le
   message est relivré plus tard, et après N essais il part dans `paddle:events:dead`.
4. **Pas de clé de licence en clair dans Redis.** `licenseCreated` transporte `license_id`, `order_id`,
   `customer_id` et `product_id`. MailTalker lit la clé en base.
5. **Livraison au moins une fois, sans table outbox.** Séquence : créer les licences (transaction DB,
   idempotente grâce à `uq_UserLicense_order_product_license` et au court-circuit `get_order_licenses`),
   puis `XADD license:events`, puis `XACK paddle:events`. Après un crash, la relivraison recrée l'état
   existant et republie. Les consommateurs de `licenseCreated` doivent donc être idempotents (dédupliquer
   par `license_id`).
6. **Propriété du schéma partagé.** Aujourd'hui chaque repository appelle `create_all` sur tout `Base.metadata`.
   Avec plusieurs services sur la même base, chaque service ne crée que ses propres tables. PadSync possède
   User, Product, ProductLicense, ProductVersion et ProductPageDisplay. AuthAPI possède UserLicense,
   UserActivation, UserTrial et UserMachine, et lit le catalogue. À terme : migrations Alembic centralisées.

## Changements dans ce dépôt
- **Dépendance** : `redis` (redis-py, client asyncio) dans [requirements.txt](requirements.txt). Paramètres
  `REDIS_URL`, `PADDLE_EVENTS_STREAM`, `LICENSE_EVENTS_STREAM`, `CONSUMER_GROUP`, `CONSUMER_NAME`,
  `MAX_DELIVERIES` dans [config.py](config.py). On retire `PADDLE_API_KEY` et `PADDLE_ORDER_WEBHOOK_SECRET`.
- **Nouveau** `app/events/` :
  - `bus.py` : wrapper Redis (`ensure_group` avec `XGROUP CREATE ... MKSTREAM`, `read`, `ack`, `publish`,
    `reclaim` via `XAUTOCLAIM`, `dead_letter`).
  - `schemas.py` : enveloppe d'événement (`event_id`, `event_type`, `occurred_at`, `data`) et `LicenseCreatedEvent`.
  - `consumer.py` : boucle `XREADGROUP` qui filtre `transaction.completed`, valide avec
    `PaddleTransactionCompletedEvent`, appelle `Licensing.create_licenses`, publie puis acquitte.
    Les erreurs de validation (message empoisonné) partent directement en dead-letter.
    `ProductLicenseNotFoundError` ne déclenche pas d'ACK (relivraison).
  - `Licensing` est synchrone (SQLAlchemy sync) : les appels passent par `run_in_threadpool`, comme aujourd'hui pour le SDK Paddle.
- [app/main.py](app/main.py) : le lifespan crée le client Redis et lance le consumer en `asyncio.Task`,
  puis l'annule proprement à l'arrêt. On retire `app.state.paddle`.
- [app/routers/license.py](app/routers/license.py) : on supprime `POST /license/create`. On garde
  `/activate` et `/activate_trial` (HTTP synchrone, appelés par l'installeur).
- [app/services/paddle.py](app/services/paddle.py) : supprimé (signature et `get_customer` disparaissent).
  On retire aussi `paddle-python-sdk` si plus rien ne l'utilise.
- [app/schemas/paddle.py](app/schemas/paddle.py) : on supprime `PaddleCustomer`. On garde `PaddlePrice`,
  `PaddleTransactionItem` et `PaddleTransaction`.
- [app/schemas/requests.py](app/schemas/requests.py) : `build_order_request()` ne prend plus de customer.
  `ProductLicenseRequest.customer` devient `customer_id: str`.
- [app/schemas/user.py](app/schemas/user.py) : on supprime `User`, et `UserLicense.user_id` devient `customer_id`.
- [app/services/licensing.py](app/services/licensing.py) : `create_licenses` ne construit plus d'utilisateurs
  et retourne les `UserLicense` créées (id inclus) pour l'événement. La logique d'activation ne change pas.
- [app/db/models/users.py](app/db/models/users.py) : on retire `UserRow` de ce service (il appartient à
  PadSync). `UserLicense.user_id` devient `customer_id` (String, index, sans FK). Les FK vers
  `ProductLicense` et `Product` sont conservées (PadSync crée le catalogue avant toute vente).
- [app/db/repositories/license_repository.py](app/db/repositories/license_repository.py) : `save_licenses(licenses)`
  sans l'upsert `UserRow`, avec `RETURNING` de l'id et de la clé.
- [app/db/database.py](app/db/database.py) : `create_tables(tables=...)` ne crée que les tables d'AuthAPI.
  Les modèles du catalogue restent déclarés (lecture et FK) sans être créés ici.
- [docker-compose.yml](docker-compose.yml) : service `redis` (avec AOF activé pour conserver les streams)
  sur le réseau interne. AuthAPI en dépend.

## Contrat `licenseCreated` (stream `license:events`)
```json
{ "event_type": "license.created", "event_id": "<uuid>", "occurred_at": "...",
  "data": { "license_id": "lic_…", "order_id": "txn_…", "customer_id": "ctm_…",
            "product_id": "<fxplug>", "product_license_id": "pro_…", "expires_at": null } }
```
Un événement par licence. `event_id` est déterministe (dérivé de `license_id`) pour que les consommateurs puissent dédupliquer.

## Vérification
- `docker compose up` avec Redis et Postgres, et un catalogue inséré à la main (Product, ProductLicense).
- `XADD paddle:events * payload '<transaction.completed sandbox>'` : licence en base, un message dans
  `license:events`, `XPENDING paddle:events auth-api` vide.
- Rejouer le même message : pas de doublon en base, `licenseCreated` republié avec le même `event_id`.
- Message avec un `pro_…` inconnu : reste en pending, puis passe en `paddle:events:dead` après `MAX_DELIVERIES`.
- Tuer le conteneur entre la création et l'ACK, puis le relancer : message repris via `XAUTOCLAIM`, état cohérent.
- `/v1/license/activate` et `/activate_trial` : comportement inchangé.
