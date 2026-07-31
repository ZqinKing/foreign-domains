# pure-foreign-domains

自动生成「纯境外」域名封禁清单，并发布适用于 **dnsmasq** 的配置，以及纯域名 list。

## 规则（方案 B）

```text
ban = (geolocation-!cn | union(*@!cn)) - union(*@cn)
```

| 集合 | 含义 |
|---|---|
| `geolocation-!cn` | 境外候选大类 |
| `*@!cn` | 明确非大陆属性（中国公司海外业务等） |
| `*@cn` | 有大陆接入/大陆业务，豁免不封 |

说明：

- 基础分类库：[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community)
- 提取工具：[snowie2000/geoview](https://github.com/snowie2000/geoview)
- 上游已声明 `geosite:geolocation-cn@!cn` 不可用；本项目直接并入全部 `*@!cn`，不依赖该废弃写法
- 参考：[#390](https://github.com/v2fly/domain-list-community/issues/390)、[#3119](https://github.com/v2fly/domain-list-community/pull/3119)、[#3198](https://github.com/v2fly/domain-list-community/pull/3198)

示例：

- `youtube.com` → 封禁
- `www.apple.com`（命中 `@cn`）→ 不封禁
- `bilibili.tv` / `aliexpress.ru`（`@!cn`）→ 封禁

## 发布产物

每次 Action 会发布：

| 文件 | 说明 |
|---|---|
| `pure-foreign.conf` | **dnsmasq 配置（推荐）**，`address=/domain/` → NXDOMAIN |
| `pure-foreign-0.0.0.0.conf` | dnsmasq 配置，解析到 `0.0.0.0` |
| `pure-foreign.txt` | 纯域名列表，一行一个域名 |
| `pure-foreign.json` | JSON 域名清单 + 元数据 |
| `build-meta.json` | 生成统计与校验信息 |

## 使用方式

### 1) 订阅 Release / latest 分支文件

把下面 URL 里的 `<USER>/<REPO>` 换成你的仓库：

```text
# 推荐：latest 分支稳定直链
https://raw.githubusercontent.com/<USER>/<REPO>/latest/pure-foreign.conf
https://raw.githubusercontent.com/<USER>/<REPO>/latest/pure-foreign.txt

# 或从 GitHub Releases 下载对应 tag 产物
```

### 2) dnsmasq

```conf
# /etc/dnsmasq.conf
conf-file=/etc/dnsmasq.d/pure-foreign.conf
```

更新示例：

```bash
curl -fsSL -o /etc/dnsmasq.d/pure-foreign.conf \
  https://raw.githubusercontent.com/<USER>/<REPO>/latest/pure-foreign.conf
systemctl restart dnsmasq
```

### 3) 只要域名 list

```bash
curl -fsSL -o pure-foreign.txt \
  https://raw.githubusercontent.com/<USER>/<REPO>/latest/pure-foreign.txt
```

## GitHub Actions

工作流：`.github/workflows/update-release.yml`

触发：

- 每天定时（UTC 16:20）
- 手动 `workflow_dispatch`
- 推送改动到 `scripts/` 或 workflow 本身

动作：

1. 下载最新 `dlc.dat` 与 `geoview`
2. 按方案 B 生成名单
3. 校验样本（YouTube / Apple / `@!cn` 等）
4. 上传 Artifact
5. 创建 GitHub Release（tag=`YYYYMMDDHHMM`）
6. 更新 `latest` 分支，提供稳定 raw 直链

## 本地生成

依赖：

- Python 3.10+
- 网络（首次自动下载 `geoview` 与 `dlc.dat`）

```bash
python scripts/build.py --dist dist --cache .cache --with-json
```

输出在 `dist/`：

```text
dist/pure-foreign.conf
dist/pure-foreign-0.0.0.0.conf
dist/pure-foreign.txt
dist/pure-foreign.json
dist/build-meta.json
```

常用参数：

```bash
python scripts/build.py --help
python scripts/build.py --geosite /path/to/dlc.dat --geoview /path/to/geoview
```

## 目录结构

```text
.
├── .github/workflows/update-release.yml
├── scripts/build.py
├── public/
├── LICENSE
└── README.md
```

## 免责声明

- 列表完全由公开 geosite 数据机械生成，不代表任何政治或商业立场
- 可能存在误伤/漏封；生产环境请先小流量验证
- 域名层封禁无法覆盖 IP 直连、DoH 绕过等情形
