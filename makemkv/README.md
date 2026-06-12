# xmploryg/makemkv

A self-contained media ingest image that converts disc sources (ISO, Blu-ray, DVD, M2TS, VOB) into
a single feature MKV and attempts to produce a synced English subtitle sidecar.

Available on:
- Docker Hub: `docker.io/xmploryg/makemkv:latest`
- GHCR: `ghcr.io/xmploryg/makemkv:latest`

---

## Supported Source Types

| Input | Detection | Tool used |
|-------|-----------|-----------|
| `.iso` file | file extension | `makemkvcon` — picks longest feature title |
| Directory with `BDMV/` | Blu-ray root | ffmpeg `bluray:` protocol, longest playlist |
| Directory with `VIDEO_TS/` | DVD root | `makemkvcon` — picks longest feature title |
| `VIDEO_TS/` directory itself | DVD video_ts | same as DVD root via parent |
| Directory with `.m2ts` files | BDMV stream | ffmpeg concat |
| Directory with `.vob` files | raw VOB dir | ffmpeg concat, menu VOBs filtered |
| `.mkv` file | pass-through | subtitle processing only |

Paths matching game/ROM/emulator hints (see `DEFAULT_SKIP_HINTS` in `rip_media.py`) are silently skipped.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RIP_MIN_LENGTH` | `1200` | Minimum title length in seconds passed to `makemkvcon --minlength` |
| `FEATURE_MIN_SECONDS` | `2400` | Minimum duration threshold for selecting the feature title |
| `SUBTITLE_CLONE_POLICY` | `never` | `always`, `missing`, or `never` — whether to clone a non-English subtitle track to English |
| `SUBTITLE_CLONE_LANGS` | `zho,chi,cmn,yue,jpn,kor,spa,fre,fra,deu,ger,ita,und` | Preferred source languages for subtitle cloning, in priority order |
| `WATCH_INTERVAL_SECONDS` | `300` | Seconds between scan loops (watcher mode only) |
| `SCAN_PATHS` | `/data` | Space-separated list of directories to scan (watcher mode only) |

---

## Usage

### One-shot: process a single source

```bash
docker run --rm \
  -v /your/media:/media \
  xmploryg/makemkv:latest \
  process /media/Downloads/some-movie.iso
```

### One-shot: scan a directory recursively

```bash
docker run --rm \
  -v /your/media:/media \
  xmploryg/makemkv:latest \
  scan /media/Downloads/complete
```

### Watcher loop (default entrypoint)

The default `CMD` runs a continuous scan loop. Mount your media volume and set `SCAN_PATHS`:

```bash
docker run -d \
  -v /your/media:/media \
  -e SCAN_PATHS=/media/Downloads/complete \
  -e WATCH_INTERVAL_SECONDS=300 \
  xmploryg/makemkv:latest
```

### Kubernetes (with ConfigMap script override)

The entrypoint runs scripts from `/opt/makemkv/` if present, falling back to the baked-in
copies at `/usr/local/bin/`. This lets you update the Python scripts without rebuilding the image
by mounting a ConfigMap at `/opt/makemkv/`.

```yaml
# Create ConfigMap from your local script files:
#   kubectl create configmap makemkv-scripts \
#     --from-file=ingest_media.py \
#     --from-file=rip_media.py \
#     --from-file=subtitle_workflow.py \
#     --from-file=entrypoint.sh

containers:
  - name: watcher
    image: docker.io/xmploryg/makemkv:latest
    volumeMounts:
      - name: makemkv-scripts
        mountPath: /opt/makemkv
        readOnly: true
      - name: media
        mountPath: /media
volumes:
  - name: makemkv-scripts
    configMap:
      name: makemkv-scripts
      defaultMode: 0555
  - name: media
    # your PVC or hostPath here
```

---

## MakeMKV Beta Key

MakeMKV requires a valid registration key or a recent binary version. The image ships with a
beta key baked in at `/root/.MakeMKV/settings.conf`. Beta keys are posted periodically at:

> https://forum.makemkv.com/forum/viewtopic.php?t=1053

When the key expires, update the key line in the `Dockerfile` `RUN` block and rebuild:

```dockerfile
echo 'app_Key = "T-<new-key>"' > /root/.MakeMKV/settings.conf
```

You can also override at runtime by mounting a `settings.conf`:

```bash
docker run --rm \
  -v /your/settings.conf:/root/.MakeMKV/settings.conf:ro \
  -v /your/media:/media \
  xmploryg/makemkv:latest \
  process /media/some-movie.iso
```

---

## Output

For each processed source the scripts emit a tab-separated `INGEST_RESULT` line to stdout:

```
INGEST_RESULT  <status>  <source_path>  <video_path>  <aux_path>  <output_dir>
```

`status` is either `ready` (subtitle found) or `review` (no syncable subtitle located — a
`.needs-english-subs-review.txt` marker is written next to the MKV).

---

## Building

```bash
docker build --platform linux/amd64 -t xmploryg/makemkv:latest ./makemkv
```

GitHub Actions in `.github/workflows/docker-publish-makemkv.yml` publishes on push to `main`
when `makemkv/**` changes. Requires:
- `vars.DOCKERHUB_USERNAME`
- `secrets.DOCKERHUB_TOKEN`

Also publishes to GHCR as `ghcr.io/<owner>/makemkv:latest`.
