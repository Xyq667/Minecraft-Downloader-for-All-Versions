# MC 全版本下载器 Beta 2.0.0

发布日期：2026-10-30

## 更新内容

- 新增 Forge 与 NeoForge 加载器：自动准备 Java 8 / 17 / 21 / 25，下载官方安装器并运行无界面安装
- 新增镜像兜底：Forge / NeoForge 元数据与依赖优先官方源，失败自动切换 BMCLAPI 镜像
- 调整加载器入口：移到版本列表上方下拉框，在下载 MC 版本时选择加载器
- 调整加载器流程：原版下载与加载器安装合并为一个任务，共用进度、取消与取消后清理
- 移除独立的“模组加载器”按钮
- 修复 Forge / NeoForge 安装依赖下载失败时的错误提示

## 文件

- `MC全版本下载器.exe` — 主程序
- `MCVersionDownloader-beta2.0.0-source.zip` — 源码包
- `MCVersionDownloader-beta2.0.0-all.zip` — 源码 + 程序 + 文档

## 说明

- 加载器支持 Fabric / Quilt / Forge / NeoForge
- 与 Mojang / Microsoft 无关联，Minecraft 是 Mojang Studios 的商标
- 开源协议：MIT
