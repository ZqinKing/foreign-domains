# Create and Publish the GitHub Repository

This project is already initialized as a local Git repository on branch `main`.

## GitHub Web

1. Create a new empty repository on GitHub.
2. Do not initialize it with README, license, or `.gitignore`.
3. Copy the repository SSH or HTTPS URL.
4. Run:

```bash
git remote add origin <REPOSITORY_URL>
git push -u origin main
```

After the first push, GitHub Actions will run automatically and publish:

- `pure-foreign.conf`
- `pure-foreign-0.0.0.0.conf`
- `pure-foreign.txt`
- `pure-foreign.json`
- `build-meta.json`

## GitHub CLI

If GitHub CLI is installed and authenticated:

```bash
gh repo create foreign-domains --public --source . --remote origin --push
```

## Stable Raw URLs

After the workflow finishes, the `latest` branch contains stable raw files:

```text
https://raw.githubusercontent.com/<USER>/<REPO>/latest/pure-foreign.conf
https://raw.githubusercontent.com/<USER>/<REPO>/latest/pure-foreign.txt
```

Use `pure-foreign.conf` with dnsmasq:

```conf
conf-file=/etc/dnsmasq.d/pure-foreign.conf
```
