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

## 数据来源

优先读 ZCode 自己的用量库 `~/.zcode/cli/db/db.sqlite` 的 `model_usage` 表（只读打开），
里面有 `time_to_first_token_ms`，所以能算**纯解码速度**；读不到时回退到
`~/.zcode/cli/rollout/model-io-*.jsonl`（没有 TTFT，只能算含预填充的速度）。
`--json` 输出里的 `source` 字段会告诉你这次用的是哪个。

## 指标口径

- **余额**：调用服务商的余额接口。目前实现了 DeepSeek（`GET https://api.deepseek.com/user/balance`），其它服务商会返回"暂不支持自动查询"。
- **纯解码速度**：`outputTokens / (durationMs - timeToFirstToken)`，模型真正吐字的速度，最有参考价值。
- **含预填充速度**：`outputTokens / durationMs`，把首字等待也算进去，是偏保守的下界。
- **首字延迟（TTFT）**：从发请求到第一个 token 的耗时，缓存命中时很低。
- **缓存命中率**：`cacheReadTokens / inputTokens`，越高越省钱。
- **中位数**：默认取最近 10 次调用（`API_QUOTA_WINDOW` 可调），比"最近一次"更能代表当前水平。
- **按模型**：最近 200 次调用里出现过的模型分别统计——换模型时可以直接对比速度。
- **本次会话**：只统计当前会话（SQLite 按 `session_id`，回退模式按最新的日志文件）。

## 展示建议

把脚本输出原样贴出即可，不要重新计算或改写数字。用户关心的是"还剩多少钱"和"现在多快"这两个结论，所以可以直接在开头给一句总结，例如"余额 USD 5.70，最近中位速度约 222 tok/s"。
