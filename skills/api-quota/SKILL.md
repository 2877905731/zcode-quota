---
name: api-quota
description: 查询当前 ZCode 会话正在使用的模型 API 的剩余余额与生成速度。当用户问"余额还有多少""还能用多久""现在速度多少""API 快不快""这个 key 还有钱吗"，或需要确认是否要充值、是否该换模型时使用。
---

# API 余额与速度查询

数据来源：`~/.zcode/cli/rollout/model-io-*.jsonl`（每次模型调用都记录了 `durationMs`、`outputTokens`、`inputTokens`、`cacheReadTokens`）以及服务商的余额接口。

## 怎么查

```bash
python "${ZCODE_PLUGIN_ROOT}/scripts/quota.py"
```

`${ZCODE_PLUGIN_ROOT}` 是 ZCode 提供的插件根目录变量；如果它没有被替换成实际路径，
脚本在本插件根目录的 `scripts/quota.py`（默认安装位置 `~/.zcode/local-plugins/api-quota/`）。

其它模式：

| 命令 | 用途 |
| --- | --- |
| `quota.py --json` | 结构化结果，便于程序消费 |
| `quota.py --hook` | ZCode `SessionStart` hook 的 JSON 输出 |
| `quota.py --watch 30` | 每 30 秒刷新一次 |

## 指标口径

- **余额**：调用服务商的余额接口。目前实现了 DeepSeek（`GET https://api.deepseek.com/user/balance`），其它服务商会返回"暂不支持自动查询"。
- **速度**：`outputTokens / durationMs`，单位 tok/s。注意这个值把预填充时间也算进去了，所以是偏保守的下界；命中缓存时预填充很快，数值更接近真实解码速度。
- **中位速度**：最近 10 次主模型（`role: main`）调用的中位数，比"最近一次"更能代表当前 API 的真实速度。样本会跨最近几个会话聚合，避免样本太少。
- **本次会话**：只统计当前会话文件。

## 展示建议

把脚本输出原样贴出即可，不要重新计算或改写数字。用户关心的是"还剩多少钱"和"现在多快"这两个结论，所以可以直接在开头给一句总结，例如"余额 USD 5.70，最近中位速度约 222 tok/s"。
