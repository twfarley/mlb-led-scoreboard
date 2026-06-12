# Home Assistant dashboard plugin for `mlb-led-scoreboard`

Turn your LED matrix into a live dashboard for **anything Home Assistant knows about** —
solar production, battery charge, indoor temperature, energy prices, door sensors, you
name it. The board polls Home Assistant's REST API and renders the entities you choose.

It ships with two layouts:

| Layout      | What it shows                                                                 |
|-------------|-------------------------------------------------------------------------------|
| `grid`      | A configurable grid of label/value tiles — one per entity. Great for sensors. |
| `powerwall` | A Tesla-style **solar → home → battery/grid** energy-flow screen.             |

This is a [`bullpen`](../bullpen/README.md) plugin, so it slots into the normal scoreboard
rotation alongside baseball, standings, and news.

---

## Table of contents

- [Why Home Assistant instead of the device directly?](#why-home-assistant-instead-of-the-device-directly)
- [1. Get a Home Assistant access token](#1-get-a-home-assistant-access-token)
- [2. Find your entity IDs](#2-find-your-entity-ids)
- [3. Install the plugin](#3-install-the-plugin)
- [4. Configure it](#4-configure-it)
  - [Connection settings](#connection-settings)
  - [Grid dashboard](#grid-dashboard)
  - [Powerwall dashboard](#powerwall-dashboard)
- [5. Customize colors (optional)](#5-customize-colors-optional)
- [Full configuration reference](#full-configuration-reference)
- [Troubleshooting](#troubleshooting)
- [Building your own layout](#building-your-own-layout)

---

## Why Home Assistant instead of the device directly?

Pulling from Home Assistant (HA) instead of talking to each device's own API has real
advantages:

- **One token, one endpoint.** No per-device logins, no rate-limited cloud APIs, no
  juggling local passwords for a Powerwall, a thermostat, and a doorbell.
- **HA already normalizes the data.** Units, friendly names, and availability are handled
  for you.
- **Anything HA tracks, you can display.** The same plugin shows your solar array today and
  your garage door tomorrow — just change the entity IDs.

The trade-off is that you need a running Home Assistant instance reachable from the device
running the scoreboard (a Raspberry Pi on the same LAN is the typical setup).

---

## 1. Get a Home Assistant access token

The plugin authenticates with a **Long-Lived Access Token**.

1. Open Home Assistant in your browser.
2. Click your **user name** at the bottom-left to open your profile.
3. Go to the **Security** tab (older versions: scroll to the bottom of the profile page).
4. Under **Long-Lived Access Tokens**, click **Create Token**.
5. Name it something memorable, e.g. `led-scoreboard`.
6. **Copy the token immediately** — Home Assistant only shows it once.

> 🔒 Treat this token like a password. It grants full API access to your Home Assistant.
> Keep it out of screenshots and public repos.

You'll also need your Home Assistant **base URL**, e.g. `http://homeassistant.local:8123`
or `http://192.168.1.50:8123`. If you use HTTPS with a self-signed certificate, see
[`verify_ssl`](#connection-settings).

---

## 2. Find your entity IDs

Every value in Home Assistant is an **entity** with an ID like `sensor.solar_power` or
`binary_sensor.front_door`.

To find one:

1. In Home Assistant, go to **Settings → Devices & Services → Entities**.
2. Use the search box to find what you want (e.g. type `solar`).
3. Click the entity and note its **Entity ID** (shown in the dialog, and editable under
   the gear/settings icon).

Quick check from a terminal — confirm a token + entity works before touching the scoreboard:

```bash
curl -s -H "Authorization: Bearer YOUR_TOKEN" \
  http://homeassistant.local:8123/api/states/sensor.solar_power
```

A JSON blob with a `"state"` field means you're good to go.

---

## 3. Install the plugin

From the scoreboard project root, with the same Python environment the scoreboard uses:

```bash
pip install ./homeassistant
```

This registers the plugin under the entry-point name **`homeassistant`**, which is the key
you'll use in `config.json`.

---

## 4. Configure it

Add a `homeassistant` block under `plugins` in your `config.json`, and make sure the plugin
is in your board rotation. See [`config.example.json`](./config.example.json) for a
complete copy-pasteable file.

### Connection settings

```json
"plugins": {
  "homeassistant": {
    "base_url": "http://homeassistant.local:8123",
    "token": "YOUR_LONG_LIVED_ACCESS_TOKEN",
    "verify_ssl": true,
    "update_interval": 30,
    "timeout": 10,
    "dashboard": { "...": "see below" }
  }
}
```

| Key               | Default | Notes                                                                       |
|-------------------|---------|-----------------------------------------------------------------------------|
| `base_url`        | `""`    | Your HA URL including port. No trailing slash needed.                        |
| `token`           | `""`    | The long-lived access token from step 1.                                     |
| `verify_ssl`      | `true`  | Set to `false` only if HA uses a self-signed HTTPS certificate.             |
| `update_interval` | `30`    | Seconds between API polls. Don't go too low — be kind to your HA box.        |
| `timeout`         | `10`    | Seconds to wait for the HA API before counting the poll as failed.          |

### Grid dashboard

The grid layout shows one tile per entity, arranged in a configurable number of columns.

```json
"dashboard": {
  "layout": "grid",
  "title": "Home",
  "columns": 2,
  "tiles": [
    { "entity": "sensor.living_room_temperature", "label": "Living", "decimals": 0 },
    { "entity": "sensor.outdoor_temperature",      "label": "Outside", "decimals": 0 },
    { "entity": "sensor.electricity_price",        "label": "Price", "unit": "¢", "decimals": 1 },
    { "entity": "sensor.solar_power",              "label": "Solar", "scale": 0.001, "unit": "kW", "decimals": 2,
      "color": [255, 200, 40] }
  ]
}
```

Each tile understands:

| Key           | Default          | Notes                                                                          |
|---------------|------------------|--------------------------------------------------------------------------------|
| `entity`      | *(required)*     | The Home Assistant entity ID to display.                                        |
| `label`       | HA friendly name | Short caption above the value. Falls back to the entity's friendly name.        |
| `unit`        | HA unit          | Override the unit suffix. Use `""` to hide it entirely.                          |
| `decimals`    | `1`              | Decimal places for numeric values. `0` shows a whole number.                    |
| `scale`       | `1.0`            | Multiplier applied to numeric values, e.g. `0.001` to turn watts into kW.       |
| `color`       | white            | `[r, g, b]` for the value text.                                                 |
| `label_color` | gray             | `[r, g, b]` for the label text.                                                 |

Non-numeric states (like `home`, `on`, `open`) are shown title-cased automatically.

### Powerwall dashboard

The powerwall layout reproduces the Tesla app's energy-flow view — sun, house, and battery
icons with animated flow dots and live kW readouts — but every value comes from Home
Assistant. It works with the official **Tesla** integration out of the box, and with any
other setup as long as you point it at the right entities.

```json
"dashboard": {
  "layout": "powerwall",
  "power_unit": "W",
  "entities": {
    "solar":       "sensor.solar_power",
    "home":        "sensor.load_power",
    "grid":        "sensor.grid_power",
    "battery":     "sensor.battery_power",
    "charge":      "sensor.percentage_charged",
    "solar_today": "sensor.solar_energy_today",
    "grid_status": "binary_sensor.grid_status"
  }
}
```

| Entity role   | Meaning                                                              | Required? |
|---------------|---------------------------------------------------------------------|-----------|
| `solar`       | Solar production power.                                              | yes       |
| `home`        | Home/load power draw.                                                | yes       |
| `grid`        | Grid power (positive = importing).                                  | yes       |
| `battery`     | Battery power (negative = charging, positive = discharging).        | yes       |
| `charge`      | Battery charge percentage (0–100).                                  | yes       |
| `solar_today` | Solar energy produced today, in kWh. Scrolls along the bottom.      | optional  |
| `grid_status` | Grid up/down sensor. Reserved for future use.                       | optional  |

**`power_unit`** tells the plugin how your power entities report values. The Tesla
integration reports **watts**, so the default is `"W"` and the plugin converts to kW for
display. If your entities are already in kilowatts, set `"power_unit": "kW"`.

> The animated energy-flow look fits a 128×64 panel best. On smaller panels the plugin
> scales the icons and text down automatically, but readability is limited — the `grid`
> layout is a better fit for 64×32 and below.

---

## 5. Customize colors (optional)

The plugin draws with a built-in palette, so **no color file edits are required**. If you
want to recolor it, add a `homeassistant` block to `colors/scoreboard.json`. Any key you
omit keeps its default.

```json
"homeassistant": {
  "value":        { "r": 255, "g": 255, "b": 255 },
  "label":        { "r": 120, "g": 120, "b": 120 },
  "solar_value":  { "r": 255, "g": 200, "b": 40  },
  "home_value":   { "r": 90,  "g": 170, "b": 255 },
  "battery_value":{ "r": 120, "g": 220, "b": 120 }
}
```

The full list of color keys: `background`, `title`, `label`, `value`, `solar_icon`,
`home_icon`, `battery_icon`, `battery_fill_high`, `battery_fill_mid`, `battery_fill_low`,
`battery_wave`, `battery_arrow`, `grid_icon`, `solar_flow_active`, `solar_flow_idle`,
`battery_flow`, `grid_flow`, `solar_value`, `home_value`, `battery_value`.

---

## Full configuration reference

```jsonc
"homeassistant": {
  "base_url": "http://homeassistant.local:8123", // HA URL incl. port
  "token": "YOUR_LONG_LIVED_ACCESS_TOKEN",       // Profile -> Security
  "verify_ssl": true,                            // false for self-signed HTTPS
  "update_interval": 30,                         // seconds between polls
  "timeout": 10,                                 // seconds per request

  "dashboard": {
    "layout": "grid",                            // "grid" | "powerwall"
    "title": "Home",                             // grid: optional header
    "columns": 2,                                // grid: tiles per row

    "tiles": [                                   // grid: one entry per value
      {
        "entity": "sensor.living_room_temperature",
        "label": "Living",
        "unit": "°F",
        "decimals": 0,
        "scale": 1.0,
        "color": [255, 255, 255],
        "label_color": [120, 120, 120]
      }
    ],

    "power_unit": "W",                           // powerwall: "W" | "kW"
    "entities": {                                // powerwall: role -> entity id
      "solar":       "sensor.solar_power",
      "home":        "sensor.load_power",
      "grid":        "sensor.grid_power",
      "battery":     "sensor.battery_power",
      "charge":      "sensor.percentage_charged",
      "solar_today": "sensor.solar_energy_today",
      "grid_status": "binary_sensor.grid_status"
    }
  }
}
```

---

## Troubleshooting

**A red `!` shows on the board.**
That's the scoreboard's generic "plugin update failed" indicator. Check the logs (run with
`debug` enabled) for a `[HOMEASSISTANT]` line explaining the failure.

**"Home Assistant unavailable" scrolls across the screen.**
The plugin connected but the last poll failed. Common causes:
- Wrong `base_url` or port — confirm with the `curl` command from
  [step 2](#2-find-your-entity-ids).
- Expired or mistyped `token`.
- HA unreachable from the scoreboard device (firewall, VLAN, DNS).

**`Entity not found` warning in the logs.**
The entity ID is wrong or the entity was renamed. Re-check it under
**Settings → Devices & Services → Entities**.

**Values show `—`.**
The entity exists but reported `unknown`/`unavailable`, or hasn't published a state yet.

**SSL certificate errors.**
If HA uses a self-signed certificate, set `"verify_ssl": false`. Prefer a proper
certificate where you can.

---

## Building your own layout

The plugin is intentionally small and self-contained — a good template for any
Home-Assistant-backed display. The three pieces are:

- [`config.py`](./src/mlb_led_scoreboard_homeassistant/config.py) — parses the user's
  `dashboard` block into typed settings.
- [`data.py`](./src/mlb_led_scoreboard_homeassistant/data.py) — throttled polling of the HA
  REST API, with helpers like `get_float(entity_id)` and `get(entity_id).is_on()`.
- [`renderer.py`](./src/mlb_led_scoreboard_homeassistant/renderer.py) — draws a frame. Add a
  new `layout` branch in `render()` and you have a brand-new dashboard.

To add a layout: handle a new `layout_mode` string in `Config`, then dispatch to a new
`_render_<name>` method in `Renderer`. The data layer already fetches whatever entity IDs
`Config.all_entity_ids()` returns, so wiring up new entities is just adding them to the
config schema.

See the [`bullpen` developer guide](../bullpen/README.md#for-developers) for the plugin API
contract.
