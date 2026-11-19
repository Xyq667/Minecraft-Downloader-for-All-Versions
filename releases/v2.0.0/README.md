# MC 全版本下载器 v2.0.0

发布日期：2026-11-20

## 更新内容

- **新增 Minecraft Wiki 阅读器**：工具栏「Wiki」打开中文 Minecraft Wiki（`zh.minecraft.wiki`），支持关键词搜索、内嵌阅读正文、一键在浏览器中打开、随机条目；正文优先取渲染页面并剔除信息框与导航框，表格与段落保留，读取过的条目本地缓存 7 天。
- **新增模组下载（Modrinth）**：工具栏「下载模组」，可按游戏版本与加载器（Fabric / Quilt / Forge / NeoForge）筛选搜索，选中项目后列出可用版本；下载校验 SHA-1 并写入目标目录的 `mods` 文件夹，已存在文件自动跳过。
- **新增外观设置**：标题栏「外观」可选择明亮 / 暗黑主题、5 种强调色（森林绿 / 天空蓝 / 紫罗兰 / 熔岩橙 / 红石红）、调节卡片透明度（0–50%）、开关毛玻璃效果。默认白色明亮主题，改动即时生效并随程序保存。
- 主题热切换：调色板原地更新后重建界面，保留搜索词、筛选与选中状态，无需重启。
- 修复侧栏「提示」卡片在小窗口下被裁切的问题。

## 文件

- `MC全版本下载器.exe` — 主程序（单文件，无需安装）
- `MCVersionDownloader-v2.0.0-source.zip` — 源码包
- `MCVersionDownloader-v2.0.0-all.zip` — 源码 + 程序 + 文档

## 说明

- 仅支持 Windows；exe 未签名，SmartScreen 提示时选择「仍要运行」。
- 本程序与 Mojang / Microsoft 无关联，Minecraft 是 Mojang Studios 的商标。
- 开源协议：MIT License，Copyright (c) 2026 Xyq_yy
