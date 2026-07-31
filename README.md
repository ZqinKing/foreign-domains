# foreign-domains

自动生成并发布「境外域名」封禁清单，主要用于 `dnsmasq`。

## 规则

```text
ban = (geolocation-!cn | union(*@!cn)) - union(*@cn)
```

本项目只使用这一条规则。

| 集合 | 含义 |
|---|---|
| `geolocation-!cn` | 境外候选集合 |
| `*@!cn` | 明确标记为非中国大陆的条目，例如中国公司海外业务 |
| `*@cn` | 有中国大陆接入或中国大陆业务的条目，从封禁清单中排除 |

数据源：[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) 的 `dlc.dat`。

提取工具：[snowie2000/geoview](https://github.com/snowie2000/geoview)。

上游已经将 `@!cn` 规则从 `cn` 列表中剔除，因此 `geosite:geolocation-cn@!cn` 不再可用。本项目直接合并全部 `*@!cn` 条目，不依赖这个已废弃写法。参考：[v2fly/domain-list-community#390](https://github.com/v2fly/domain-list-community/issues/390)、[#3119](https://github.com/v2fly/domain-list-community/pull/3119)、[#3198](https://github.com/v2fly/domain-list-community/pull/3198)。

## 示例

| 域名 | 结果 |
|---|---|
| `youtube.com` | 封禁 |
| `www.apple.com` | 不封禁，因为命中 `@cn` |
| `bilibili.tv` | 封禁，因为命中 `@!cn` |
| `aliexpress.ru` | 封禁，因为命中 `@!cn` |

## 发布产物

所有发布文件统一使用 `foreign-domains-*` 命名：

| 文件 | 说明 |
|---|---|
| `foreign-domains-dnsmasq.conf` | dnsmasq 配置，使用 `address=/domain/` 返回 NXDOMAIN |
| `foreign-domains-dnsmasq-0.0.0.0.conf` | dnsmasq 配置，将封禁域名解析到 `0.0.0.0` |
| `foreign-domains.txt` | 纯域名列表，一行一个域名 |
| `foreign-domains.json` | JSON 域名清单和元数据 |
| `foreign-domains-meta.json` | 构建时间、数量和校验结果 |

## 稳定下载地址

工作流完成后，`latest` 分支会提供稳定直链：

```text
https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/foreign-domains-dnsmasq.conf
https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/foreign-domains.txt
```

## dnsmasq 使用

下载配置文件：

```bash
curl -fsSL -o /etc/dnsmasq.d/foreign-domains.conf \
  https://raw.githubusercontent.com/ZqinKing/foreign-domains/latest/foreign-domains-dnsmasq.conf
```

在 `dnsmasq.conf` 中引入：

```conf
conf-file=/etc/dnsmasq.d/foreign-domains.conf
```

更新后重启 `dnsmasq`：

```bash
systemctl restart dnsmasq
```

## GitHub Actions

工作流文件：`.github/workflows/update-release.yml`

触发方式：

- 每天定时运行
- 手动 `workflow_dispatch`
- 推送到 `main`，且改动涉及 `scripts/**` 或 `.github/workflows/**`

工作流会：

1. 下载最新 `dlc.dat` 和 `geoview`。
2. 按当前规则生成域名清单。
3. 校验 YouTube、Apple、`@!cn` 和已废弃的 `geolocation-cn@!cn` 等样本。
4. 上传构建产物。
5. 创建 GitHub Release。
6. 将稳定文件发布到 `latest` 分支。

## 本地构建

依赖：

- Python 3.10+
- 网络访问，除非通过 `--geosite` 和 `--geoview` 指定本地文件

```bash
python scripts/build.py --dist dist --cache .cache --with-json
```

输出文件：

```text
dist/foreign-domains-dnsmasq.conf
dist/foreign-domains-dnsmasq-0.0.0.0.conf
dist/foreign-domains.txt
dist/foreign-domains.json
dist/foreign-domains-meta.json
```

使用本地输入文件：

```bash
python scripts/build.py --geosite /path/to/dlc.dat --geoview /path/to/geoview --with-json
```

## 免责声明

本项目根据公开 geosite 数据机械生成清单，可能存在误封或漏封。生产环境使用前建议先测试。
