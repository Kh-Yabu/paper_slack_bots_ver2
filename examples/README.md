# Example configurations

These files are reference configurations — copy relevant sections into your own `config.yaml`.

| File | Description |
|------|-------------|
| [`config.solid-earth.yaml`](config.solid-earth.yaml) | Production-scale setup for solid Earth geophysics (seismology, geodesy, volcanology). Includes 40+ journal feeds with LLM prefilters, Springer API queries, and Copernicus sources. Slack channel IDs are placeholders — replace with your own. |

To use an example as your starting point:

```bash
cp examples/config.solid-earth.yaml config.yaml
# Edit slack_channel_id values and trim journals you don't need
```
