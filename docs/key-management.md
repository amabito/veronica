# Key Management

This document covers API key generation, rotation, and revocation for the VERONICA control-plane API.

---

## How Authentication Works

The VERONICA API uses a single bearer key passed via the `X-Veronica-Key` request header.
The key is loaded from the `VERONICA_API_KEY` environment variable at startup.

Comparison is timing-safe (`hmac.compare_digest`) to prevent timing attacks.

The following paths are exempt from authentication and require no key:

- `GET /health`
- `GET /docs` and `/docs/*` (Swagger UI)
- `GET /openapi.json`
- `GET /redoc`

All other endpoints require a valid `X-Veronica-Key` header.

---

## Key Generation

Use a cryptographically secure random generator. Do not use passwords, UUIDs, or human-readable strings.

**Python:**

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

This produces a 64-character hex string (256 bits of entropy). That is sufficient for all deployments.

**OpenSSL:**

```bash
openssl rand -hex 32
```

**Store the key immediately.** The value is not hashed or stored by VERONICA -- if you lose it,
you must rotate.

---

## Setting the Key

Set `VERONICA_API_KEY` in your environment before starting the server:

**.env file (recommended):**

```bash
VERONICA_API_KEY=a3f8c2d1e4b7f6a9c0d2e5b8a1f4c7d0e3b6a9f2c5d8e1b4a7f0c3d6e9b2a5f8
```

**Shell:**

```bash
export VERONICA_API_KEY="$(openssl rand -hex 32)"
veronica serve
```

The key is read from the environment at each request. Restarting the server is not required after
setting the variable if the process inherits the updated environment (e.g., via a process manager
that re-exports variables on config reload).

---

## Using the Key

Include the key in the `X-Veronica-Key` header on every request to a protected endpoint:

```bash
curl http://127.0.0.1:8000/policies \
  -H "X-Veronica-Key: your-key-here"
```

If the header is missing or the value is wrong, the API returns `401 Unauthorized`:

```json
{"detail": "Invalid or missing API key"}
```

If `VERONICA_API_KEY` is not set and `VERONICA_AUTH_DISABLED` is not `1`, all protected requests
return `503 Service Unavailable`:

```json
{"detail": "API key not configured"}
```

---

## Key Rotation

VERONICA supports one active key at a time. Rotation requires a brief window where both the old
and new key are valid. Because the server reads `VERONICA_API_KEY` from the environment at each
request, you can achieve zero-downtime rotation with a reverse proxy:

### Rolling rotation procedure

1. Generate a new key:

   ```bash
   NEW_KEY="$(python -c "import secrets; print(secrets.token_hex(32))")"
   echo "$NEW_KEY"
   ```

2. Update your reverse proxy to forward requests with either the old or new key accepted.
   (For a simpler setup without a proxy, accept a brief moment where old-key clients get 401.)

3. Update `VERONICA_API_KEY` in `.env` (or your secret manager) to the new key.

4. Restart (or send SIGHUP to) the VERONICA server process so it picks up the new value.

5. Update all clients to use the new key.

6. Remove the old-key forwarding rule from the proxy (if used).

### Simplified rotation (maintenance window)

If a brief outage is acceptable:

```bash
# 1. Generate new key
NEW_KEY="$(python -c "import secrets; print(secrets.token_hex(32))")"

# 2. Stop server
kill $(pgrep -f "veronica serve")

# 3. Update .env
sed -i "s/^VERONICA_API_KEY=.*/VERONICA_API_KEY=$NEW_KEY/" .env

# 4. Restart
veronica serve
```

Update all clients before restarting to avoid a 401 spike.

---

## Multiple Keys (Not Natively Supported)

VERONICA supports one key per deployment. If you need per-client keys or fine-grained access control:

- Run a separate VERONICA instance per client with a distinct `VERONICA_API_KEY`.
- Use a reverse proxy (nginx, Caddy, Kong) in front of VERONICA to authenticate and forward requests,
  translating per-client tokens to the single VERONICA key.

---

## Revocation

To revoke the current key, set `VERONICA_API_KEY` to a new value (or unset it) and restart the server.

All clients using the old key will immediately receive `401 Unauthorized` after the server restarts.

If you unset `VERONICA_API_KEY` without setting `VERONICA_AUTH_DISABLED=1`, the server will return
`503 Service Unavailable` to all protected requests -- effectively blocking all access until a new
key is configured.

---

## Development Mode

For local development where you do not want to set up a key:

```bash
VERONICA_AUTH_DISABLED=1 veronica serve
```

The server will log a warning once:

```
WARNING  veronica.api.auth: VERONICA_API_KEY is not set and VERONICA_AUTH_DISABLED=1 -- API auth is DISABLED. Set VERONICA_API_KEY in production.
```

Do not use `VERONICA_AUTH_DISABLED=1` in any environment accessible over a network.

---

## Best Practices

- Store keys in a secrets manager (AWS Secrets Manager, HashiCorp Vault, 1Password Secrets Automation)
  rather than in plain `.env` files on shared systems.
- Do not commit `.env` files to version control. Add `.env` to `.gitignore`.
- Rotate keys periodically and immediately if a key is suspected to be compromised.
- Use different keys per environment (development, staging, production).
- Treat the key like a password -- do not log it, do not include it in URLs, do not share it
  in plaintext over email or chat.
