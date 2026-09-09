---
description: 查看当前 API 的剩余余额与生成速度
allowed-tools: Bash
---

运行下面的命令，并把它的输出**原样**展示给用户（不要改写数字、不要总结、不要额外解释）：

```
python "${ZCODE_PLUGIN_ROOT}/scripts/quota.py"
```

`${ZCODE_PLUGIN_ROOT}` 会被 ZCode 替换成本插件的实际安装目录。

如果脚本执行失败，把错误信息完整贴出来，并说明可能原因（网络不通、API Key 失效、余额接口变更）。

如果余额低于 2（美元）或速度为 `--`，在结尾补一句简短提醒。
