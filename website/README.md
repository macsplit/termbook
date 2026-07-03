# termbook.dev Website

This directory contains the landing page and website assets for termbook on termbook.dev.

## Files

- `index.html` - Main landing page with installation instructions and feature overview
- `style.css` - Dark-mode stylesheet
- `screenshot.png` - Screenshot of termbook in action
- `README.md` - This file

## Deployment

The website is deployed to the NUC via the `deploy-flatpak.sh` script in the `scripts/` directory.

The deploy script:
1. Copies website files to `/var/www/termbook` on the NUC
2. Copies the Flatpak ostree repository to `/var/www/termbook/repo`
3. Generates `.flatpakref` and `.flatpakrepo` files
4. Configures nginx with appropriate MIME type handlers
5. Reports the HTTP port number

### Manual Deployment

If not using the script, manually copy files to the NUC:

```bash
scp index.html style.css screenshot.png nuc:/var/www/termbook/
scp *.flatpakref *.flatpakrepo nuc:/var/www/termbook/
rsync -av repo/ nuc:/var/www/termbook/repo/
```

## Hosting on termbook.dev

The Cloudflare tunnel should be configured separately to route termbook.dev to the NUC's HTTP port.

See `docs/FLATPAK_DEPLOYMENT.md` for more details.
