This directory contains the build context for `xmploryg/makemkv`.

Local build:

```bash
docker build -t xmploryg/makemkv:latest ./makemkv
```

GitHub Actions in `.github/workflows/docker-publish-makemkv.yml` publishes the image on push to `main` when:

- `vars.DOCKERHUB_USERNAME` is set
- `secrets.DOCKERHUB_TOKEN` is set

The workflow also publishes to GHCR as `ghcr.io/<owner>/makemkv:latest`.