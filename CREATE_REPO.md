# 创建并推送 GitHub 仓库

本项目已经初始化为本地 Git 仓库，默认分支为 `main`。

## GitHub 网页

1. 在 GitHub 创建一个空仓库。
2. 不要勾选初始化 README、LICENSE 或 `.gitignore`。
3. 复制仓库的 SSH 或 HTTPS 地址。
4. 执行：

```bash
git remote add origin <REPOSITORY_URL>
git push -u origin main
```

第一次推送后，GitHub Actions 会自动运行并发布：

- `foreign-domains-dnsmasq.conf`
- `foreign-domains-dnsmasq-0.0.0.0.conf`
- `foreign-domains.txt`
- `foreign-domains.json`
- `foreign-domains-meta.json`
- `gfw.txt`
- `gfw-meta.json`

## GitHub CLI

如果已经安装并登录 `gh`：

```bash
gh repo create foreign-domains --public --source . --remote origin --push
```

## 稳定直链

工作流完成后，`latest` 分支会提供稳定文件：

```text
https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/foreign-domains-dnsmasq.conf
https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/foreign-domains.txt
https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/gfw.txt
https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/gfw-meta.json
```

`dnsmasq` 引入方式：

```conf
conf-file=/etc/dnsmasq.d/foreign-domains.conf
```
