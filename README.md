# foreign-domains

Automatically builds and publishes foreign-domain block lists for dnsmasq.

## Rule

```text
ban = (geolocation-!cn | union(*@!cn)) - union(*@cn)
```

This is the only rule used by this project.

| Set | Meaning |
|---|---|
| `geolocation-!cn` | Foreign geolocation candidate set |
| `*@!cn` | Explicitly non-mainland-China entries, including overseas services from China-based companies |
| `*@cn` | Mainland China access/business entries, excluded from blocking |

Data source: [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) `dlc.dat`.

Extraction tool: [snowie2000/geoview](https://github.com/snowie2000/geoview).

Note: upstream has removed `@!cn` rules from `cn` lists, so `geosite:geolocation-cn@!cn` is no longer available. This project directly unions all `*@!cn` entries instead. See [#390](https://github.com/v2fly/domain-list-community/issues/390), [#3119](https://github.com/v2fly/domain-list-community/pull/3119), and [#3198](https://github.com/v2fly/domain-list-community/pull/3198).

Expected examples:

| Domain | Result |
|---|---|
| `youtube.com` | blocked |
| `www.apple.com` | not blocked because it matches `@cn` |
| `bilibili.tv` | blocked because it matches `@!cn` |
| `aliexpress.ru` | blocked because it matches `@!cn` |

## Release Files

Each GitHub Actions run publishes:

| File | Description |
|---|---|
| `pure-foreign.conf` | dnsmasq config, NXDOMAIN mode via `address=/domain/` |
| `pure-foreign-0.0.0.0.conf` | dnsmasq config, resolves blocked domains to `0.0.0.0` |
| `pure-foreign.txt` | plain domain list, one domain per line |
| `pure-foreign.json` | JSON domain list and metadata |
| `build-meta.json` | build metadata, counts, and validation results |

## Stable URLs

After the workflow finishes, stable files are available from the `latest` branch:

```text
https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/pure-foreign.conf
https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/pure-foreign.txt
```

## dnsmasq

Download the dnsmasq file:

```bash
curl -fsSL -o /etc/dnsmasq.d/pure-foreign.conf \
  https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/pure-foreign.conf
```

Then include it from `dnsmasq.conf`:

```conf
conf-file=/etc/dnsmasq.d/pure-foreign.conf
```

Restart dnsmasq after updating the file:

```bash
systemctl restart dnsmasq
```

## GitHub Actions

Workflow: `.github/workflows/update-release.yml`

Triggers:

- daily schedule
- manual `workflow_dispatch`
- push to `main` when `scripts/**` or `.github/workflows/**` changes

The workflow:

1. Downloads the latest `dlc.dat` and `geoview`.
2. Builds the domain list with the rule above.
3. Validates samples such as YouTube, Apple, `@!cn`, and deprecated `geolocation-cn@!cn`.
4. Uploads build artifacts.
5. Creates a GitHub Release with generated files.
6. Publishes stable raw files to the `latest` branch.

## Local Build

Requirements:

- Python 3.10+
- network access, unless `--geosite` and `--geoview` point to local files

```bash
python scripts/build.py --dist dist --cache .cache --with-json
```

Outputs:

```text
dist/pure-foreign.conf
dist/pure-foreign-0.0.0.0.conf
dist/pure-foreign.txt
dist/pure-foreign.json
dist/build-meta.json
```

Use existing local inputs:

```bash
python scripts/build.py --geosite /path/to/dlc.dat --geoview /path/to/geoview --with-json
```

## Disclaimer

This project mechanically derives lists from public geosite data. It may contain false positives or false negatives. Test before using it in production.
