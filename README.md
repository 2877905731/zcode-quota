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

- **每次 ZCode 升级都要重打一次**（升级会覆盖 app.asar），重跑 `应用界面补丁.cmd` 即可；
- 这是非官方手段，不受 ZCode 支持，请自行评估风险；
- 状态条依赖本地数据服务，服务没起来时状态条不显示。

## 数据服务

状态条和悬浮窗都依赖它（只监听 127.0.0.1，不对外）：

```bat
python scripts\quota-server.py      :: 手动启动
启动余额服务.cmd                     :: 双击启动
```

接口：`GET /quota`（30 秒缓存）、`GET /quota-status.js`（界面脚本）、`GET /health`。

## 数据来源

- **余额**：`GET https://api.deepseek.com/user/balance`（目前只实现 DeepSeek，其它服务商提示"暂不支持"）
- **速度**：`~/.zcode/cli/rollout/model-io-*.jsonl`，取 `outputTokens / durationMs`，用最近 10 次主模型调用的**中位数**
- **服务商**：读 `~/.zcode/v2/config.json`，按最近一次调用记录里的 `providerId` 自动匹配

## 配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `API_QUOTA_PORT` | `8788` | 数据服务端口 |
| `API_QUOTA_REFRESH` | `60` | 悬浮窗刷新间隔（秒） |
| `API_QUOTA_CACHE` | `30` | 余额查询缓存（秒） |
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

1. `python scripts\patch-zcode.py --restore`（重启 ZCode 后状态条消失）；
2. 删除启动文件夹里的 `api-quota-server.vbs`；
3. 把本目录路径从 `~/.zcode/cli/config.json` 的 `plugins.dirs` 里删掉；
4. 删除本目录。

## 已知限制

- 速度包含预填充时间，是偏保守的下界；缓存命中率高时更接近真实解码速度。
- 余额只支持 DeepSeek，接 GLM / Z.ai 需要补它们的额度接口。
- 界面补丁仅 Windows 有效（依赖 `app.asar` 的路径与 PowerShell 探测进程）。
- ZCode 升级后状态条会消失（app.asar 被覆盖），重跑一次 `应用界面补丁.cmd` 即可。

## License

[MIT](LICENSE)
