# Examples: dashboard, QR labels and scanning

A complete setup built on the integration, as used on a P2S with one AMS: a dashboard with printer, storage and label views, an A4 label sheet with one QR code per spool, and an automation that opens the scanned spool on your phone.

| File | What it does |
|---|---|
| [`dashboard.yaml`](dashboard.yaml) | Dashboard "Filament" with three views: **Printer** (status tiles, AMS slots with the bound spool, remaining grams and a link to the spool), **Storage** (the card, reload from cloud, 30-day history of the total), **Labels** (the label sheet) |
| [`labels/labels.html`](labels/labels.html) | A4 label sheet: pick spools, print, stick a label on each spool. English or German, following the Home Assistant language |
| [`labels/qrcode.min.js`](labels/qrcode.min.js) | [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4 by Kazuhiko Arase, MIT license, bundled so the sheet works offline |
| [`automation_spool_label_scanned.yaml`](automation_spool_label_scanned.yaml) | A scanned label sends a push notification to the scanning phone; tapping it opens the spool |

Entity ids are those of an English Home Assistant install. German installs use German ids (e.g. `sensor.bambu_filament_library_spulen`); the files say where to adjust.

## Requirements

- **Bambu Filaments** (this integration): storage view, labels and the scan automation need nothing else.
- **[Bambu Lab integration (ha-bambulab)](https://github.com/greghesp/ha-bambulab)** for the printer view: status tiles, chamber light and the AMS slots all use its entities.
- Optional: [ha-bambulab-cards](https://github.com/greghesp/ha-bambulab-cards) for the `custom:ha-bambulab-ams-card` on the printer view.
- Home Assistant Companion app on the phone for scanning the labels.

## How the QR labels work

Each label carries the Home Assistant tag `https://www.home-assistant.io/tag/<spool_id>`. The phone camera hands that link to the Companion app, which fires a `tag_scanned` event with the spool id as tag id. No tags need to be created beforehand. The automation only reacts to tag ids that are spool ids of the library and sends a notification that opens the storage view with `?spool=<spool_id>`. The card then opens the spool's details dialog with the remaining-weight history and quick actions: enter the weighed grams, mark the spool as full, or open the edit dialog.

## Setup

1. Copy `labels/labels.html` and `labels/qrcode.min.js` to `/config/www/bambu-filaments/` (a new `www` folder needs one Home Assistant restart).
2. Create a dashboard with the URL `dashboard-filament`, open the raw configuration editor and paste `dashboard.yaml`. Replace the placeholders listed at the top of the file. Without ha-bambulab-cards, remove the `custom:ha-bambulab-ams-card` card.
3. Create a new automation, switch to YAML mode and paste `automation_spool_label_scanned.yaml`.
4. Open the **Labels** view, select spools and print. Turn off the browser's headers and footers; the QR codes are 15 mm wide.
5. Scan a label with the phone camera and tap the notification.

The dashboard path appears in the automation (`/dashboard-filament/storage`) and in the ⚙️ links of the printer view; change it in both places if you use another one.
