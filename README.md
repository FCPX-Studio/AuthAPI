# AuthAPI

License authentication API for **FCPX Studio** products.

AuthAPI issues license keys when an order is synced, and activates them on customer machines. Each activation returns an **Ed25519-signed token** that the product (FxPlug plugin / installer) verifies offline with the matching public key.

Built with FastAPI, SQLAlchemy, PostgreSQL and Redis Streams. The schema comes from the shared [`db`](https://github.com/FCPX-Studio/db) package, the events from the shared [`events`](https://github.com/FCPX-Studio/events) package.

---

## How it works

```
Paddle ─ webhook ─▶ EventsAPI ─ paddle:events ─▶ PaddleSync   (User, Order, OrderItem → Postgres)
                                                    └─ orders:ready ─▶ AuthAPI   (UserLicense → Postgres)
                                                                         ├─ licenses:issued ─▶ EventsAPI (SSE to the website)
                                                                         └─ emails:license  ─▶ EmailTalker

Customer Mac (plugin) ─ POST /auth/v1/license/activate ─▶ AuthAPI: check product, expiry, seats → signed token
```

### License issuing (`orders:ready`)

AuthAPI never talks to Paddle and never creates customers. PaddleSync validates `transaction.completed` and commits the `User`, `Order` and `OrderItem` rows, then publishes `OrderReady(order_id)`.

AuthAPI consumes `orders:ready` (consumer group `authapi`, a background task in the FastAPI lifespan, see [app/events/handlers.py](app/events/handlers.py)):

1. Load the `Order`, its items and their `ProductLicense` from Postgres.
2. For each item that is not a trial (`ProductLicense.is_trial`), create `quantity` `UserLicense` rows (`item_index` 1..N), owned by `Order.user_id`, with the catalog `activation_seats` and `expires_at = now + life_duration`.
3. Publish `LicenseIssued(order_id, paddle_order_id, user_license_ids)` on `licenses:issued` (even for a trial-only order, with an empty list), and `SendLicenseEmail` on `emails:license` when there is at least one license.
4. Return: the message is acked.

**Idempotent**: `UNIQUE (paddle_item_id, item_index)` makes a replay insert nothing. The existing licenses are read back and the events are published again with the same ids, so consumers deduplicate on `user_license_ids`. Any error (unknown order, DB down…) leaves the message pending: it is retried, then moved to `orders:ready:dead` after 5 deliveries.

License keys never go through Redis: consumers read them from Postgres.

### Keys, seats, expiry

- **License keys** are 16 random characters from the Crockford Base32 alphabet, formatted `XXXX-XXXX-XXXX-XXXX`. Keys are normalized (trimmed, upper-cased) on activation.
- **Expiry**: `expires_at` is `NULL` for a perpetual license (`ProductLicense.life_duration` is `NULL`).
- **Activation seats**: each license allows `activation_seats` machines (copied from `ProductLicense`). A machine uses one seat, identified by its fingerprint. Reactivating a registered machine refreshes its row and does not use another seat.
- **Trials** are not licenses: the installer calls `/license/activate_trial`, one trial per product and machine, lasting `Product.trial_duration`.

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
| `403`  | License has expired                                    |
| `409`  | No activation seat left for this license               |

### `POST /auth/v1/license/activate_trial`

Same body without `license_key`. Creates (or refreshes) the trial of the product on this machine. Reactivating keeps the original expiration date.

| Status | Reason                                       |
| ------ | -------------------------------------------- |
| `404`  | Unknown product, or the product has no trial |
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
├── main.py                      FastAPI app, lifespan (Licensing service + orders:ready consumer task)
├── events/handlers.py           orders:ready handler: issue licenses, publish licenses:issued / emails:license
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
