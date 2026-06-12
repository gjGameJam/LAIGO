# LEGO Browser Host — VPS + Residential Proxy (Cloudflare bypass)

> **Status (2026-06-11):** This approach is **selected** (chosen over self-host,
> managed browser, and bare VPS). Nothing is provisioned yet. **Next action:**
> make the `§7` provider decisions, then stand up the VPS per `§6`. The
> `order_from_lego` code side (`LEGO_BROWSER_CDP_URL` / `LEGO_PLAYWRIGHT_HEADLESS`)
> is already in place — see `scripts/checkout/clients/lego_client.py`.

## 1. Why this exists

LEGO.com (Pick-a-Brick) is fronted by **Cloudflare**, which hard-blocks
**headless** Chromium — verified 2026-06-11 across bundled Chromium, real
Chrome channel, init-script stealth, and **patchright** (patched undetectable
driver); all fail headless, only **headed** passes, and there is **no
interactive challenge** when headed. See `docs/ORDER_OPTIMIZER.md §6.1` and
memory `project_lego_cloudflare_headless`.

LAIGO's app runs on Render's **native Python runtime**, which can't install
Xvfb (no root), so it can't run a headed browser itself. Solution: run the
headed browser **off-Render** and have the saga connect to it over CDP. The
`order_from_lego` launch is already env-driven for this (`LEGO_BROWSER_CDP_URL`).

## 2. Architecture

```
┌────────────────────────┐         CDP over wss (token + TLS)        ┌──────────────────────────────┐
│ Render (native runtime) │  ───────────────────────────────────────▶ │ VPS (Docker)                  │
│ FastAPI app + saga      │   LEGO_BROWSER_CDP_URL=wss://host/?token=… │  ┌────────────────────────┐  │
│ order_from_lego         │                                            │  │ caddy (TLS, reverse px) │  │
│   connect_over_cdp(...) │                                            │  └───────────┬────────────┘  │
└────────────────────────┘                                            │  ┌───────────▼────────────┐  │
        ▲                                                              │  │ browserless/chromium    │  │
        │ Render static egress IPs                                     │  │ (HEADED via Xvfb)       │  │
        │ allow-listed on VPS firewall                                 │  └───────────┬────────────┘  │
        └──────────────────────────────────────────────────────────── │              │ --proxy-server │
                                                                       └──────────────┼───────────────┘
                                                                                      │ TLS end-to-end (CONNECT tunnel)
                                                                                      ▼
                                                                       ┌──────────────────────────────┐
                                                                       │ Residential proxy (IP-whitelist│
                                                                       │ the VPS IP) → exits residential │
                                                                       └──────────────┬───────────────┘
                                                                                      ▼
                                                                               lego.com (Cloudflare) ✅
```

**Trust/data flow:** the LEGO `storage_state` cookies travel from Render →
VPS browser only (both hosts you control). lego.com sees the **residential
proxy IP** + the **real headed-Chromium TLS fingerprint**. Cloudflare passes.

## 3. Security model (load-bearing)

The CDP endpoint controls a browser logged into LEGO with a **saved card** —
anyone who reaches it can place orders. It MUST NOT be public. Three layers:

1. **Network:** VPS firewall (`ufw`) allows the browser port **only from
   Render's static outbound IPs** (Render dashboard → service → "Outbound IPs";
   paid plans get static egress). Everything else denied.
2. **Auth:** browserless `TOKEN` (a long random secret) required on every
   connection — `?token=…`.
3. **Transport:** terminate **TLS** (wss) at Caddy so the token + CDP traffic
   aren't in cleartext. Caddy auto-provisions a cert for a hostname you point
   at the VPS.

Proxy auth uses **IP-whitelist** (whitelist the VPS IP at the proxy provider),
NOT user:pass — because `connect_over_cdp` can't pass Playwright proxy
credentials, and Chromium's `--proxy-server` flag doesn't accept inline creds.
IP-whitelist sidesteps the 407 problem entirely. Pick a residential proxy
provider that supports IP-whitelist auth + sticky sessions.

## 4. docker-compose (VPS)

```yaml
# deploy/lego-browser/docker-compose.yml
services:
  browser:
    image: ghcr.io/browserless/chromium:latest   # headed via built-in Xvfb
    restart: unless-stopped
    environment:
      - TOKEN=${BROWSERLESS_TOKEN}                # long random secret
      - CONCURRENT=1                              # LAIGO places one order at a time
      - TIMEOUT=120000                            # ms; generous for checkout
      - PROXY_SERVER=${RESIDENTIAL_PROXY}         # host:port (IP-whitelisted)
    expose:
      - "3000"                                    # not published to host; caddy fronts it

  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
    depends_on:
      - browser

volumes:
  caddy_data:
```

```
# deploy/lego-browser/Caddyfile
lego-browser.YOURDOMAIN.com {
    reverse_proxy browser:3000
}
```

> Note: `PROXY_SERVER` passthrough depends on the browser image's supported
> env/launch flags — confirm against the chosen image's docs. If the image
> doesn't honor a default proxy env, pass `&--proxy-server=host:port` on the
> connect URL query string instead (browserless forwards launch flags).

## 5. Render wiring

Set on the Render service (env):

```
LEGO_BROWSER_CDP_URL = wss://lego-browser.YOURDOMAIN.com/?token=<BROWSERLESS_TOKEN>
LEGO_PLAYWRIGHT_HEADLESS = false   # (ignored when CDP_URL is set, but keep consistent)
```

`order_from_lego` already branches on `LEGO_BROWSER_CDP_URL`
(`scripts/checkout/clients/lego_client.py`): when set it
`connect_over_cdp(...)` instead of launching locally.

## 6. Setup runbook (order of operations)

1. **Provision VPS** (any provider; small box is fine — one Chromium).
2. **Buy residential proxy** with IP-whitelist auth + sticky session; whitelist
   the VPS public IP; note the `host:port`.
3. **DNS:** point `lego-browser.YOURDOMAIN.com` A-record at the VPS.
4. **Firewall:** `ufw default deny incoming`; `ufw allow 22`; `ufw allow from
   <each Render outbound IP> to any port 443`. (Render dashboard lists them.)
5. **Deploy** the compose above with a strong `BROWSERLESS_TOKEN` and the
   proxy `host:port` in `.env`.
6. **Smoke test** from the VPS / a whitelisted host:
   `python -m scripts.checkout._cf_probe` adapted to `connect_over_cdp` — or
   just set `LEGO_BROWSER_CDP_URL` locally and run the inspector. Confirm PaB
   renders (title `LEGO® Pick a Brick…`, not Cloudflare) AND that the exit IP
   is residential (`whatismyip` via the browser).
7. **Set Render env** (§5) and exercise a quote→confirm against TEST Stripe.

## 7. Decisions still needed (fill these in before provisioning)

- [ ] **Residential proxy provider** + confirm it supports IP-whitelist auth.
- [ ] **VPS provider/size** + region (close to Render's region for latency).
- [ ] **Hostname** for the browser endpoint (for Caddy TLS).
- [ ] **Browser image**: `ghcr.io/browserless/chromium` (check current license
      terms for self-host) vs a hand-rolled Chromium+Xvfb image vs an
      open-source alternative (e.g. `steel-browser`). Affects the proxy-flag
      and connect-URL specifics in §4.

## 8. Failure mode (already safe)

If the VPS/browser is unreachable, `connect_over_cdp` raises → `order_from_lego`
fails → the saga preserves the Stripe hold + any BrickOwl orders and routes to
MANUAL_REVIEW. No money lost; operator completes out-of-band. So host downtime
degrades automation, not safety.
