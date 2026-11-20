# Dental landing experience

## What this delivers

`index.html` is a no-build, responsive landing experience. It uses a scroll-scrubbed Three.js scene with six named camera moves, real HTML headings and controls, WCAG-visible keyboard focus, a reduced-motion/static-poster fallback, and a deterministic `?render=1` hook. The six shot posters and the fallback poster are hand-authored SVG illustrations, not clinic photography. No patient statistics, awards, reviews, or credentials are fabricated.

The other repositories supplied in the chat are separate projects and are not runtime dependencies of this page.

## Configure before public use

The page is preconfigured with the supplied name, **Gangayya Dental Hospital**, location **Marlapur**, and postal code **523316**. It also shows the supplied 9 am–8 pm hours, while clearly noting that operating days are unconfirmed. Edit the `clinic` object near the start of the module in `index.html` only to add clinic-approved details:

- `name`: the clinic's approved public name
- `city`: the clinic's city
- `phone`: clinic phone digits or international `+` number
- `whatsapp`: WhatsApp phone digits in international format, without `+`
- `mapUrl`: the clinic's exact verified Google Maps share URL (`https`)

The map pin, full street address, complete phone number, complete WhatsApp number, operating days, privacy and consent links, logo, and approved clinic imagery have not yet been supplied. The submitted phone fields contained only the country prefix, so they are intentionally left unconfigured. Add these details only after verification. A map button appears only when a valid HTTPS Google Maps URL is configured; call and WhatsApp controls do not pretend to work without a complete number.

## Run locally on Windows

Run this exact command in PowerShell to serve the website folder directly:

```powershell
python -m http.server 8001 --directory "C:\Users\DELL\Downloads\operation A\dental-hospital-website"
```

Open `http://127.0.0.1:8001/`. The page loads pinned Three.js modules and Google Fonts from their CDNs, so normal internet access is required for the animated version. If WebGL is unavailable or reduced motion is enabled, it uses the local poster assets instead.

## Deterministic capture

Open `http://127.0.0.1:8001/?render=1`. The page exposes `window.renderFrame(seconds)` for deterministic frame selection from 0 to 18 seconds. A capture runner can call that function at a fixed frame rate and record the page at 1920x1080. The HTML does not fabricate an MP4; capture/export is a separate Playwright step.

## Static deployment

Publish this directory as static files on the clinic's HTTPS host. Keep `index.html` and `posters/` together. Test the deployed page on a keyboard, a reduced-motion device, a phone-sized viewport, and a WebGL-capable desktop before linking it from the CRM. Configure only verified contact and location details and use the clinic's real privacy/consent policies.
