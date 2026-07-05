# Flatpak Deployment Guide for termbook

This guide describes how to build and deploy termbook as a Flatpak to termbook.dev.

## Prerequisites

On the build machine:
- `flatpak` and `flatpak-builder` installed
- `ostree` installed
- `gpg` (optional, for repository signing)
- Python 3.10+ with `pytest`

On the deployment server (NUC):
- `nginx` configured and running
- SSH access available
- `flatpak` installed (for end-users)

## Quick Start

### Build and Deploy to NUC

```bash
cd /home/user/Code/OpenSource/termbook
./scripts/deploy-flatpak.sh
```

This script will:
1. Run the test suite
2. Build the Flatpak using `flatpak-builder`
3. Create an ostree repository
4. Generate `.flatpakref` and `.flatpakrepo` files
5. Deploy website files and repository to the NUC
6. Configure nginx with a unique port
7. Report the HTTP port number

### Build Only (Local Testing)

```bash
./scripts/deploy-flatpak.sh --skip-nuc-deploy --local-only
```

(This would require modifying the script — currently it always deploys to NUC)

## Environment Variables

- `NUC_HOST`: SSH hostname of the NUC server (default: `nuc`)
- `NUC_DEPLOY_DIR`: Deployment directory on NUC (default: `/var/www/termbook`)
- `FLATPAK_ARCH`: Architecture to build (default: `x86_64`)
- `FLATPAK_BRANCH`: ostree branch name (default: `master`)

Example:

```bash
NUC_HOST=nuc-server.local NUC_DEPLOY_DIR=/srv/flatpak/termbook ./scripts/deploy-flatpak.sh
```

## Manual Steps (If Not Using the Script)

### 1. Build the Flatpak

```bash
flatpak-builder \
    --arch=x86_64 \
    --repo=build/flatpak/repo \
    --default-branch=master \
    --force-clean \
    build/flatpak/build \
    flatpak/uk.leehanken.termbook.json
```

### 2. Create Repository Files

Generate `.flatpakref` (for one-click install). `Name`, `Branch`, and a `Url`
pointing at the ostree repo (not the `.flatpakrepo` file) are required;
`RuntimeRepo` tells Flatpak where to fetch the freedesktop runtime from:

```ini
[Flatpak Ref]
Name=dev.termbook.Termbook
Branch=master
Title=termbook
Url=https://termbook.dev/repo
SuggestRemoteName=termbook
Homepage=https://github.com/macsplit/termbook
IsRuntime=false
RuntimeRepo=https://dl.flathub.org/repo/flathub.flatpakrepo
```

Generate `.flatpakrepo` (repository metadata):

```ini
[Flatpak Repo]
Title=termbook Repository
Url=https://termbook.dev/repo
Comment=EPUB reader for the terminal
Homepage=https://github.com/macsplit/termbook
```

### 3. Copy to NUC

```bash
rsync -av build/flatpak/repo/ nuc:/var/www/termbook/repo/
scp website/index.html nuc:/var/www/termbook/
scp website/style.css nuc:/var/www/termbook/
scp website/screenshot.png nuc:/var/www/termbook/
scp *.flatpakref *.flatpakrepo nuc:/var/www/termbook/
```

### 4. Configure nginx on NUC

Create `/etc/nginx/sites-available/termbook.dev`:

```nginx
server {
    listen 33336;
    server_name termbook.dev;

    root /var/www/termbook;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }

    location = /dev.termbook.Termbook.flatpakref {
        default_type application/vnd.flatpak.ref;
    }

    location = /termbook.flatpakrepo {
        default_type application/vnd.flatpak.repo;
    }

    location /repo/ {
        alias /var/www/termbook/repo/;
    }
}
```

Enable the site:

```bash
sudo ln -sf /etc/nginx/sites-available/termbook.dev /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## Repository Signing

The deploy script signs the ostree repository with the dedicated termbook key
(the key whose uid is `flatpak@termbook.dev`), or the key given via
`FLATPAK_GPG_KEY`. It never picks an arbitrary key from the keyring; if no
termbook key exists the repository is published unsigned.

To create the signing key:

```bash
gpg --quick-generate-key "termbook Flatpak Repo <flatpak@termbook.dev>" rsa4096 sign 2y
```

The public key is embedded (base64) as `GPGKey=` in the generated
`.flatpakref` and `.flatpakrepo`, so clients verify signatures automatically.
When the repository is unsigned, the `GPGKey=` line is omitted and clients
fall back to `gpg-verify=false`.

## Verification

### Check Flatpak Build

```bash
flatpak info dev.termbook.Termbook
flatpak ls
```

### Test Website Locally

```bash
curl -I http://localhost:$PORT/index.html
curl -I http://localhost:$PORT/dev.termbook.Termbook.flatpakref
```

### Test Installation

```bash
flatpak install --user --from https://termbook.dev/dev.termbook.Termbook.flatpakref
flatpak run dev.termbook.Termbook ~/path/to/book.epub
```

## Troubleshooting

### Flatpak Build Fails

- Ensure the Freedesktop runtime is installed: `flatpak install flathub org.freedesktop.Platform//24.08`
- Check that all Python dependencies have compatible wheel builds
- Review `/tmp/.flatpak-build-*/` logs for details

### Repository Not Found

- Verify `repo/` directory exists and is accessible
- Check nginx error logs: `sudo tail -f /var/log/nginx/error.log`
- Ensure MIME types are set correctly for `.flatpakref` and `.flatpakrepo`

### Installation Hangs

- Check network connectivity to the NUC
- Verify the Flatpak repository signature (if using GPG):
  ```bash
  flatpak remote-info --show-metadata termbook https://termbook.dev/termbook.flatpakrepo
  ```

## Updates

To release a new version:

1. Update `termbook/__init__.py` version number
2. Commit and push to GitHub
3. Run `./scripts/deploy-flatpak.sh --rebuild`

The script will automatically:
- Use the new version number
- Rebuild the Flatpak
- Update the ostree repository
- Deploy to NUC
- Retain backward compatibility (ostree is append-only)

## See Also

- [Flatpak Documentation](https://docs.flatpak.org/)
- [ostree Documentation](https://ostree.readthedocs.io/)
- [Flatpak Manifest Reference](https://docs.flatpak.org/en/latest/manifests.html)
