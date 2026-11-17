# AuthAPI

License authentication API for **FCPX Studio** products.

AuthAPI issues license keys when an order is synced, and activates them on customer machines. Each activation returns an **Ed25519-signed token** that the product (FxPlug plugin / installer) verifies offline with the matching public key.

Built with FastAPI, SQLAlchemy, PostgreSQL and Redis Streams. The schema comes from the shared [`db`](https://github.com/FCPX-Studio/db) package, the events from the shared [`events`](https://github.com/FCPX-Studio/events) package.

---

## How it works

```
Paddle ─ webhook ─▶ EventsAPI ─ paddle:events ─▶ PaddleSync   (User, Order, OrderItem → Postgres)
                                                    ├─ orders:ready ─▶ AuthAPI   (UserLicense → Postgres)
                                                    │                    ├─ orders:licensed ─▶ EventsAPI (SSE to the website)
                                                    │                    └─ emails:license  ─▶ EmailTalker
                                                    └─ orders:refunded ─▶ AuthAPI   (revoke UserLicense)

Customer Mac (plugin) ─ POST /auth/v1/license/activate ─▶ AuthAPI: check product, expiry, seats → signed token
```

### License issuing (`orders:ready`)

AuthAPI never talks to Paddle and never creates customers. PaddleSync validates `transaction.completed` and commits the `User`, `Order` and `OrderItem` rows, then publishes `OrderReady(order_id)`.

AuthAPI consumes `orders:ready` (consumer group `authapi`, a background task in the FastAPI lifespan, see [app/events/handlers.py](app/events/handlers.py)):

1. Load the `Order`, its items, their `ProductLicense`, `Product` and current `ProductVersion` from Postgres.
2. For each item that is not a trial (`ProductLicense.is_trial`), not an asset (`Product.type = 'asset'`, a download: a key would unlock nothing) and not refunded (`OrderItem.refunded_at`), create `quantity` `UserLicense` rows (`item_index` 1..N), owned by `Order.user_id`, with the catalog `activation_seats` and `expires_at = now + life_duration`.
3. Publish `OrderLicensed(order_id, paddle_order_id, user_license_ids, items)` on `orders:licensed`, and `SendLicenseEmail` on `emails:license` when there is at least one license.
4. Return: the message is acked.

**`OrderLicensed.items`** is what the website's confirmation page shows, one entry per line item: the product (`bundle_id`, `name`, `type`, `version`), the license terms (`paddle_price_id`, `name`, `description`, `activation_seats`, `life_duration_days`, `is_trial`) and its active keys (`license_key`, `item_index`, `expires_at`). Every item is listed, trials and refunded ones included. It is published for every order, even one with no key (trial only, all refunded), so the website stops waiting.
- An item with `keys: []` is a trial (`is_trial: true`), an asset (`product.type: "asset"`), or was refunded before issuing, or revoked before a replay. All the keys of an order are committed at once, so no key follows later.
- The items are read in the same transaction that issues the keys, so they match what was committed.
- **`claim_hash`** is copied from `Order.claim_hash` (set by PaddleSync from the checkout custom_data). EventsAPI streams the items only to the browser that sends the matching secret, so knowing the `paddle_order_id` isn't enough to read the keys. It's read from the DB, so every path that issues licenses announces it. `None` means the order can't be claimed on the confirmation page. Logs only say `claim=yes/no`.

**Idempotent**: `UNIQUE (paddle_item_id, item_index)` makes a replay insert nothing. The existing licenses are read back and the events are published again with the same ids, so consumers deduplicate on `user_license_ids`. Any error (unknown order, DB down…) leaves the message pending: it is retried, then moved to `orders:ready:dead` after 5 deliveries.

**License keys in Redis**: `orders:licensed` is the one stream that carries keys, because its reader (EventsAPI's SSE to the website) never queries Postgres. The `events` package trims it by age (entries are dropped after 1 hour), and AuthAPI logs counts only, never the event. `emails:license` still carries ids only.

### License revocation (`orders:refunded`)

When Paddle approves a full refund or a chargeback of a line item, PaddleSync sets `OrderItem.refunded_at` / `refund_reason` and publishes `OrderRefunded(order_id, paddle_item_ids, reason)`. AuthAPI consumes `orders:refunded` (consumer group `authapi`) and sets `UserLicense.revoked_at` / `revoked_reason` on every license of those items. Partial refunds, credits and refund reversals revoke nothing.

- **Idempotent**: licenses already revoked keep their first date and reason.
- **Refund before issuing**: issuing skips refunded items, so no key is created or emailed. Issuing locks the order's items (`FOR SHARE`) until it commits, so a refund committed meanwhile waits for it and then revokes the new licenses.
- **Manual revocation**: `UPDATE "UserLicense" SET revoked_at = now(), revoked_reason = 'manual' WHERE license_key = '…'`.
- A revoked license can't be activated (`403`), even on a machine already registered. **Tokens already issued stay valid offline** until the product checks back with `/license/activate`: there is no token revocation yet.

### Archived products

Paddle never deletes a product or a price, it archives it (`Product.archived`, `ProductLicense.archived`, synced by PaddleSync). An archived product, or a product whose trial prices are all archived, offers no new trial (`404` on `/license/activate_trial`). This also refuses reactivating a trial already started (its offline token stays valid until it expires). Licenses already sold stay valid.

### Keys, seats, expiry

- **License keys** are 16 random characters from the Crockford Base32 alphabet, formatted `XXXX-XXXX-XXXX-XXXX`. Keys are normalized (trimmed, upper-cased) on activation.
- **Expiry**: `expires_at = created_at + ProductLicense.life_duration`, set from the Paddle price custom_data `{"duration": <days>}`. `NULL` (no duration) is a perpetual license.
- **Activation seats**: each license allows `activation_seats` machines (copied from `ProductLicense`). A machine uses one seat, identified by its fingerprint. Reactivating a registered machine refreshes its row and does not use another seat.
- **Trials** are not licenses: the installer calls `/license/activate_trial`, one trial per product and machine. A product offers a trial while it has an active (not archived) trial license, i.e. a Paddle price with custom_data `{"trial": true, "duration": <days>}` (usually a $0 price): the trial lasts its `life_duration`, the longest one if there are several. No license is issued if a customer checks out a trial price.

---

## Getting started

### Requirements

- Python 3.11+
- PostgreSQL, with the schema applied by the `db` migrations (`python -m db.migrate upgrade head`, see the `db` README). AuthAPI never creates or alters tables.
- Redis

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# local development against sibling checkouts:
pip install -e ../db -e ../events
```

### Configure

Copy [env.example](env.example) to `.env` and fill it in. On the VPS, the env file lives in the `deploy` repo (`env/auth-api.env`).

| Variable                       | Required | Default     | Description                                                        |
| ------------------------------ | -------- | ----------- | ------------------------------------------------------------------ |
| `LICENSE_PRIVATE_KEY`          | yes      | —           | Base64 of the raw 32-byte Ed25519 private key used to sign tokens  |
| `LICENSE_TOKEN_FORMAT_VERSION` | no       | `1`         | `formatVersion` written into signed tokens                         |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | yes | — | Postgres connection (or `DB_URL`), read by `db.DatabaseSettings` |
| `DB_POOL_SIZE`                 | no       | `5`         | Connections kept open in the pool                                  |
| `REDIS_HOST`, `REDIS_PORT`     | no       | `localhost`, `6379` | Redis connection, read by `events.RedisSettings`           |
| `REDIS_PASSWORD`               | VPS      | —           | Always set on the VPS                                              |
| `REDIS_DB`                     | no       | `0`         |                                                                    |
| `REDIS_STREAM_MAXLEN`          | no       | `10000`     | Approximate cap per published stream                               |

#### Generating a signing key pair

```python
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s

key = Ed25519PrivateKey.generate()
priv = key.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption())
pub = key.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)

print("LICENSE_PRIVATE_KEY =", base64.b64encode(priv).decode())  # → .env (keep secret)
print("public key          =", base64.b64encode(pub).decode())   # → embed in the product
```

> **Never commit the private key.** `.env` is git-ignored. If the private key changes, every shipped product must be updated with the new public key.

### Run

```bash
uvicorn app.main:app --reload
```

This starts both the HTTP API and the `orders:ready` consumer. Interactive API docs are served at `http://localhost:8000/auth/v1/docs`.

### Deploy

Every push to `main` builds `ghcr.io/fcpx-studio/auth-api` and restarts `auth-api` on the VPS (see [.github/workflows/deploy.yml](.github/workflows/deploy.yml)). The stack (Caddy, Postgres, Redis, migrations) is defined in the `deploy` repo.

---

## API

### `POST /auth/v1/license/activate`

Validates a license key for a product and registers the calling machine.

**Request**

```json
{
  "license_key": "abcd-efgh-jkmn-pqrs",
  "product_id": "com.fcpxstudio.example",
  "machine_fingerprint": "SHA256:<64 hex chars>",
  "machine_name": "Studio Mac",
  "app_version": "1.2.0",
  "fcpx_version": "11.0",
  "macOS_version": "15.1"
}
```

`product_id` is the FxPlug bundle ID (`Product.bundle_id`).

**Response** `200`

```json
{
  "activation_id": "42",
  "activation_seats": 2,
  "activations_used": 1,
  "license": {
    "payload": "<base64 JSON>",
    "signature_algorithm": "Ed25519",
    "signature": "<base64 signature>"
  }
}
```

**Errors**

| Status | Reason                                                 |
| ------ | ------------------------------------------------------ |
| `404`  | Unknown key, or the key belongs to a different product |
| `403`  | License has been revoked (refund, chargeback, manual) or has expired |
| `409`  | No activation seat left for this license               |

### `POST /auth/v1/license/activate_trial`

Same body without `license_key`. Creates (or refreshes) the trial of the product on this machine. Reactivating keeps the original expiration date.

| Status | Reason                                       |
| ------ | -------------------------------------------- |
| `404`  | Unknown or archived product, or the product has no trial |
| `403`  | Trial expired on this machine                |

---

## Signed token

`license.payload` is the base64 encoding of compact JSON (no spaces). The Ed25519 signature covers the **decoded payload bytes** exactly as sent:

```json
{
  "formatVersion": 1,
  "licenseId": "42",
  "productId": "com.fcpxstudio.example",
  "machineFingerprint": "SHA256:…",
  "issuedAt": "2026-11-13T10:00:00Z",
  "expiresAt": null
}
```

Dates are ISO 8601 UTC (`yyyy-MM-ddTHH:mm:ssZ`) so Swift's `ISO8601DateFormatter` can parse them. `license.signature` is the base64 encoding of the raw 64-byte Ed25519 signature: decode it (`Data(base64Encoded:)`) and pass it, with the decoded payload, to CryptoKit's `Curve25519.Signing.PublicKey.isValidSignature(_:for:)`.

The product should check that:

1. the signature is valid for the payload with the embedded public key,
2. `productId` matches the product,
3. `machineFingerprint` matches the current machine,
4. `expiresAt` is `null` or in the future.

The license key itself is never written into the token.

---

## Project structure

```
app/
├── main.py                      FastAPI app, lifespan (Licensing service + orders:ready and orders:refunded consumer tasks)
├── events/handlers.py           orders:ready (issue licenses, publish orders:licensed / emails:license), orders:refunded (revoke)
├── routers/license.py           /license/activate, /license/activate_trial
├── services/licensing.py        Business rules: license issuing, validation, activation, trials
├── repositories/                LicenseRepository, ProductRepository (data access, on db.Database)
├── keytool/keytool.py           Key generation and Ed25519 token signing
└── schemas/                     Pydantic models, request bodies, domain exceptions
config.py                        AuthAPI settings (DB_* and REDIS_* are read by the shared packages)
```

### Tables

Defined in the `db` package. AuthAPI writes `UserLicense`, `UserLicenseActivation`, `UserMachine` and `UserTrial`, and only reads `Product`, `ProductLicense`, `User`, `Order` and `OrderItem` (written by PaddleSync).

A license reaches its order through `UserLicense.paddle_item_id → OrderItem.paddle_item_id → Order`.
