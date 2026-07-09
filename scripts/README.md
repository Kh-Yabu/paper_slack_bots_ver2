# Scripts

## estimate_springer_volume.sh

Estimates how many papers Springer Nature Meta API queries return over a date window. Use this to tune `springer_page_size` and `springer_max_pages` in `config.yaml`.

```bash
export SPRINGER_API_KEY='your-key'
CONFIG_FILE=config.yaml DAYS=31 sh scripts/estimate_springer_volume.sh
```

Outputs CSV files with daily counts and recommended `max_pages` values.
