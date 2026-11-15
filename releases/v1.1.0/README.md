# MC 全版本下载器 v1.1.0

发布日期：2026-11-03

## 更新内容

- 修复下载被直接跳过：每个版本下载前先按版本名创建 `versions\<版本名>` 文件夹
- 版本目录创建失败时明确报错，不再静默跳过该版本
- 跳过判断与目录创建统一使用安全版本名，避免文件夹名不一致
- 新增日志记录“已创建版本目录 versions/xxx”
- 新增单元测试，覆盖先建目录再判断跳过的流程

## 历史版本

- [v1.0.0](../v1.0.0/README.md)
- [Beta 2.0.0](../beta2.0.0/README.md)
- [Beta 1.2.0](../beta1.2.0/README.md)
- [Beta 1.1.0](../beta1.1.0/README.md)

## 文件

- `MC全版本下载器.exe` — 主程序
- `MCVersionDownloader-v1.1.0-source.zip` — 源码包

## 说明

- 与 Mojang / Microsoft 无关联，Minecraft 是 Mojang Studios 的商标
- 开源协议：MIT License，Copyright (c) 2026 Xyq_yy
