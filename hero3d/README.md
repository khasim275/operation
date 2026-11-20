# Dental landing experience

## What this delivers

`index.html` is a no-build, responsive landing experience. It uses a scroll-scrubbed Three.js scene with six named camera moves, real HTML headings and controls, WCAG-visible keyboard focus, a reduced-motion/static-poster fallback, and a deterministic `?render=1` hook. The six shot posters and the fallback poster are hand-authored SVG illustrations, not clinic photography. No patient statistics, awards, reviews, or credentials are fabricated.

The other repositories supplied in the chat are separate projects and are not runtime dependencies of this page.

## Configure before public use

Edit the `clinic` object near the start of the module in `index.html`:

- `name`: the clinic's approved public name
- `city`: the clinic's city
- `phone`: clinic phone digits or international `+` number
- `whatsapp`: WhatsApp phone digits in international format, without `+`

Add verified opening hours, address, privacy and consent links, and approved clinic imagery only after the clinic supplies them. The current placeholders intentionally avoid inventing those details. WhatsApp and call controls explain what configuration is missing rather than pretending a booking was made.

## Run locally on Windows

From the project root:

```powershell
python -m http.server 8001
```

Open `http://127.0.0.1:8001/hero3d/`. The page loads pinned Three.js modules and Google Fonts from their CDNs, so normal internet access is required for the animated version. If WebGL is unavailable or reduced motion is enabled, it uses the local poster assets instead.

## Deterministic capture

Open `http://127.0.0.1:8001/hero3d/?render=1`. The page exposes `window.renderFrame(seconds)` for deterministic frame selection from 0 to 18 seconds. A capture runner can call that function at a fixed frame rate and record the page at 1920x1080. The HTML does not fabricate an MP4; capture/export is a separate Playwright step.

## Static deployment

Publish the `hero3d` directory as static files on the clinic's HTTPS host. Keep `index.html` and `posters/` together. Test the deployed page on a keyboard, a reduced-motion device, a phone-sized viewport, and a WebGL-capable desktop before linking it from the CRM. Configure only verified contact details and use the clinic's real privacy/consent policies.
