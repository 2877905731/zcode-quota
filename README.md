# zcode-quota

在 ZCode 输入框下方常驻显示当前模型 API 的**剩余余额**与**生成速度**。

![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![platform](https://img.shields.io/badge/platform-Windows-lightgrey)

```
                                              余额 USD 5.70 · 速度 222 tok/s · 8 次采样 · 10:26 更新
```

## 功能

| 方式 | 怎么用 | 说明 |
| --- | --- | --- |
| **界面内状态条** | 双击 `应用界面补丁.cmd`，然后重启 ZCode | 输入框正下方一行小字，真正的"内嵌" |
| 悬浮窗 | 双击 `启动余额悬浮窗.cmd` | 置顶小窗，每 60 秒刷新，单实例 |
| `/quota` 命令 | 输入框打 `/quota` | 结果输出在对话里 |
| 会话启动注入 | 自动 | 新会话开始时把一行余额/速度注入上下文 |

## 安装

**方式一：下载发布包**（推荐）

从 [Releases](https://github.com/2877905731/zcode-quota/releases) 下载 zip，
解压到 `~/.zcode/local-plugins/api-quota`，然后按顺序双击：

1. `安装.cmd` —— 注册插件目录 + 启动数据服务
2. `应用界面补丁.cmd` —— 注入状态条
3. 重启 ZCode

**方式二：git clone**

```bash
git clone https://github.com/2877905731/zcode-quota.git ~/.zcode/local-plugins/api-quota
cd ~/.zcode/local-plugins/api-quota
python scripts/install.py               # 等价于双击 安装.cmd
python scripts/patch-zcode.py --apply   # 等价于双击 应用界面补丁.cmd
```

`安装.cmd` 会把插件目录写进 `~/.zcode/cli/config.json` 的 `plugins.dirs`
（`dirs` 里的每一项就是一个插件根目录），改之前先备份成 `config.json.bak`。
它也会检查 Python 版本和 tkinter。

依赖：Python 3.10+（只用标准库，不需要 pip 安装任何东西）、Windows、ZCode 桌面版。

## 界面内状态条：原理与代价

ZCode **没有**官方的"输入框下方"扩展点（插件只能提供 skills / commands / hooks / MCP / agents），
所以状态条是通过修改 ZCode 自己的 `resources/app.asar` 实现的。

补丁用的是**等长就地改写**：

1. 在 `out/renderer/index.html` 的 `</body>` 前插入一段 226 字节的 loader；
2. 同时把 `<style>` 块里的空白压掉同样多的字节，让**文件长度一个字节都不变**；
3. 于是 asar 头部和后面所有文件的数据偏移完全不用动——风险最小，
   而且 ZCode 正在运行时也能直接写入（**不需要退出 ZCode**）。

loader 自己不干活，它从本机 `http://127.0.0.1:8788/quota-status.js` 拉真正的脚本。
好处是：**改状态条样式只要改那个 js，不用再动 app.asar**。

### 操作

```bat
python scripts\patch-zcode.py --check     :: 查看状态
python scripts\patch-zcode.py --apply     :: 打补丁（不用退出 ZCode）
python scripts\patch-zcode.py --restore   :: 还原
python scripts\patch-zcode.py --dry-run   :: 只校验长度，不写入
```

双击 `应用界面补丁.cmd` 等价于 `--check` + `--apply`。

补丁写完会读回校验，并把原始 index.html 备份到 `backup/index.html.orig`（24 KB）。
还原时如果发现 ZCode 已升级（大小对不上），脚本会拒绝写入，避免破坏文件。

> **打完补丁要重启 ZCode 才能看到**：loader 是在页面加载时执行的，
> 当前已经打开的窗口不会重新加载 index.html。

### 代价

- **ZCode 升级会覆盖 app.asar**，补丁随之失效——但下次启动时会自动补回，
  重启一次 ZCode 即可；也可以手动重跑 `应用界面补丁.cmd`；
- 这是非官方手段，不受 ZCode 支持，请自行评估风险；
- 状态条依赖本地数据服务，服务没起来时状态条不显示。

## 数据服务

状态条和悬浮窗都依赖它（只监听 127.0.0.1，不对外）：

```bat
python scripts\quota-server.py               :: 前台运行，随 ZCode 启停
python scripts\quota-server.py --ensure      :: 没在跑就后台拉起（钩子用）
python scripts\quota-server.py --standalone  :: 不跟随 ZCode，一直运行
启动余额服务.cmd                              :: 双击手动启动（--standalone）
```

接口：`GET /quota`（30 秒缓存）、`GET /quota-status.js`（界面脚本）、`GET /health`。

### 生命周期：跟随 ZCode，不用开机自启

服务**不需要开机自启**，也不需要你手动开：

- **ZCode 启动** → 插件的 `SessionStart` 钩子执行 `quota-server.py --ensure`，
  发现没在跑就后台拉起一个；
- **ZCode 退出** → 服务每 5 秒检查一次 ZCode 进程，连续 20 秒不在就自己退出。

顺带地，同一个钩子还会跑 `patch-zcode.py --ensure`：**ZCode 升级后补丁被覆盖时，
下次启动会自动重新注入**（重启一次 ZCode 即可恢复状态条）。

不想让它自动重打？在环境变量里设 `API_QUOTA_AUTOPATCH=0`。
另外 `--restore` 会写下 `.patch-disabled` 标记，自动重打不会把补丁加回来——
这也保证了你手动卸载后它不会自己复活。

## 数据来源

### 余额 / 额度（按服务商自动匹配）

| 服务商 | 接口 | 显示成什么 |
| --- | --- | --- |
| DeepSeek | `GET /user/balance` | 账户余额（多币种，含充值与赠送） |
| 智谱 / Z.ai | `GET /api/monitor/usage/quota/limit` | Coding Plan 套餐额度：5 小时 / 每周 / 月度工具，带百分比和重置时间 |
| OpenRouter | `GET /api/v1/credits` | 剩余 credits（折算成 USD） |

其它服务商会显示"暂不支持自动查询"。想再加一家：在 `scripts/quota.py` 的
`fetch_balance()` 里加一个分支，返回 `kind` 为 `balance`（多币种）或 `quota`（多窗口）的结构即可。

### 速度

优先读 ZCode 自己的用量库 `~/.zcode/cli/db/db.sqlite` 的 `model_usage` 表（只读），
里面有 `time_to_first_token_ms`，所以能算**纯解码速度**；读不到时回退到
`~/.zcode/cli/rollout/model-io-*.jsonl`（没有 TTFT）。`--json` 里的 `source` 会告诉你用了哪个。

服务商识别：读 `~/.zcode/v2/config.json`，按最近一次调用记录里的 `providerId` 自动匹配。

### 指标口径

| 指标 | 算法 | 说明 |
| --- | --- | --- |
| 纯解码速度 | `output / (duration - ttft)` | 模型真正吐字的速度，最有参考价值 |
| 含预填充速度 | `output / duration` | 把首字等待算进去，偏保守的下界 |
| 首字延迟 | `time_to_first_token_ms` | 缓存命中时很低 |
| 缓存命中率 | `cacheRead / input` | 越高越省钱 |
| 按模型 | 最近 200 次调用分组 | 换模型时可以直接对比 |

速度默认取最近 10 次调用的**中位数**（`API_QUOTA_WINDOW` 可调）。

## 配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `API_QUOTA_PORT` | `8788` | 数据服务端口 |
| `API_QUOTA_REFRESH` | `60` | 悬浮窗刷新间隔（秒） |
| `API_QUOTA_CACHE` | `30` | 余额查询缓存（秒） |
| `API_QUOTA_WINDOW` | `10` | 速度取最近多少次调用的中位数 |
| `API_QUOTA_EXIT_GRACE` | `20` | ZCode 退出后多少秒停止数据服务 |
| `API_QUOTA_AUTOPATCH` | `1` | 设为 `0` 关闭升级后自动重打补丁 |
| `ZCODE_HOME` | `~/.zcode` | ZCode 数据目录 |

## 安全说明

这个工具会接触你的 API Key，所以设计上做了这些约束：

- **仓库里没有任何密钥**。脚本在运行时从 `~/.zcode/v2/config.json` 读取 Key，
  只发给你自己的服务商（DeepSeek），不经过任何第三方。
- **数据服务只监听 `127.0.0.1`**，不对外网开放，也不写日志。
- **仓库带密钥扫描**：`scripts/check-secrets.py` 会扫 `sk-`、JWT、Bearer 字面量、
  硬编码密钥赋值、私钥文件头等模式。
  - 提交前自动跑：`git config core.hooksPath .githooks`
  - CI 里也会跑：`.github/workflows/secret-scan.yml`
  - 手动跑：`python scripts/check-secrets.py --all`
- **建议在 GitHub 仓库设置里打开** `Secret scanning` 和 `Push protection`（公开仓库免费）。
- `.gitignore` 已排除 `.env`、`*.key`、`*.pem`、`credentials*` 和本地产物 `backup/`。

## 卸载

1. `python scripts\patch-zcode.py --restore` —— 还原补丁并写下 `.patch-disabled`
   （否则下次 ZCode 启动会被自动重打），重启 ZCode 后状态条消失；
2. 把本目录路径从 `~/.zcode/cli/config.json` 的 `plugins.dirs` 里删掉；
3. 结束正在跑的 `quota-server.py` 进程（或等 ZCode 退出后它会自己停）；
4. 删除本目录。

## 已知限制

- 纯解码速度依赖 ZCode 用量库里的 `time_to_first_token_ms`；回退到日志模式时只有含预填充的速度。
- ZCode 的 `db.sqlite` 和 rollout 日志都是它的内部实现，字段可能随版本变化——
  两边都读不到时插件会显示"暂无调用记录"，不会崩。
- 余额/额度支持 DeepSeek、智谱·Z.ai、OpenRouter 三家，其它服务商提示"暂不支持"。
- 智谱的套餐额度接口是社区实测的（`/api/monitor/usage/quota/limit`），不同套餐代际
  返回的窗口数不一样（V1 个人套餐可能只有 5 小时窗口），插件按实际返回渲染，不硬编码。
- 界面补丁仅 Windows 有效（依赖 `app.asar` 的路径与进程检测）。
- ZCode 升级后状态条会消失（app.asar 被覆盖），但下次启动时会自动重新注入，
  重启一次 ZCode 即恢复；也可以手动重跑 `应用界面补丁.cmd`。

## License

[MIT](LICENSE)
