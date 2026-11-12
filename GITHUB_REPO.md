# MC 全版本下载器

一个面向 Windows 的 Minecraft Java 版版本下载与整理工具：下载官方版本、安装模组加载器、准备 Java 运行时。

当前版本：**v1.1.0**  
作者：**Xyq_yy**  
GitHub：https://github.com/Xyq667/Minecraft-Downloader-for-All-Versions  
Bilibili：https://space.bilibili.com/1364232155

## 功能

- 读取 Mojang 官方版本清单，支持正式版、快照、旧版 Alpha/Beta。
- 按类型筛选、搜索、多选版本。
- 完整安装：版本 JSON、客户端、库、原生库、日志配置、资源索引和资源对象。
- 仅版本文件：只下载版本 JSON 和 `client.jar`，适合已有启动器。
- 支持加载器：**Fabric / Quilt / Forge / NeoForge**。
- 加载器在下载版本时选择：下载原版后自动安装，生成独立的加载器版本目录。
- Forge / NeoForge 自动准备 Java 8 / 17 / 21 / 25，并运行官方无界面安装器。
- 取消下载后可删除本次新增文件，不影响已有版本。
- 下载时间统计：实时计时、累计耗时、流量、平均速度和最近下载记录。
- 32 路并发、HTTP keep-alive 复用、512 KB 分块读取。
- 首次启动必须阅读并同意用户使用协议。
- 关于页面显示作者、GitHub、Bilibili，并注明“本程序由 DeepSeek 辅助完成”。
- 默认下载目录 `D:\Minecraft`，可切换；自动检测常见启动器目录。
- 应用数据保存在 `D:\MCVersionDownloader\<版本号>`。

## 使用

1. 从 Release 下载 `MC全版本下载器.exe` 并运行。
2. 选择下载位置，勾选一个或多个 Minecraft 版本。
3. 选择“仅版本文件”或“完整安装”。
4. 需要加载器时，在版本列表上方选择 Fabric / Quilt / Forge / NeoForge，再点“开始下载”。
5. 下载完成后，在启动器中选择对应版本。

> Forge / NeoForge 首次安装需要下载 Java 和安装依赖，耗时可能较长。

## 构建

```powershell
python app.py
python -m unittest discover -s tests -v
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

需要 Python 3.10+；构建依赖见 `requirements-build.txt`。推送 `v*` 标签会触发 GitHub Actions 构建并发布 exe、源码包和完整包。

## 鸣谢与接口来源

本项目使用或调用了以下公开接口与项目，感谢维护者：

- **Mojang**：版本清单、客户端、资源与 Java 运行时
  - `https://piston-meta.mojang.com/mc/game/version_manifest_v2.json`
  - `https://piston-data.mojang.com/`
  - `https://libraries.minecraft.net/`
  - `https://resources.download.minecraft.net/`
  - `https://launchermeta.mojang.com/v1/products/java-runtime/`
- **FabricMC**：`https://meta.fabricmc.net/v2/versions/loader/{mc}` 及 `/profile/json`
- **QuiltMC**：`https://meta.quiltmc.org/v3/versions/loader/{mc}` 及 `/profile/json`
- **MinecraftForge**：`https://files.minecraftforge.net/`、`https://maven.minecraftforge.net/`
- **NeoForged**：`https://maven.neoforged.net/releases/net/neoforged/neoforge/`
- **BMCLAPI / OpenBMCLAPI**：Forge/NeoForge 元数据与依赖镜像
  - `https://bmclapi2.bangbang93.com/maven/`
  - `https://mirrors.qlu.edu.cn/bmclapi/`
  - `https://mirror4.nyist.edu.cn/bmclapi/`
- **Maven Central**：`https://repo1.maven.org/maven2/`，作为部分依赖的备用源
- **Python / Tkinter / PyInstaller**：界面与打包工具

## 说明

- 仅支持 Minecraft Java 版，不包含基岩版。
- 本程序开源免费，作者未授权任何付费渠道作为官方发行或售后渠道。若你是付费获得，请优先联系卖家售后、退款，或通过平台投诉及法律途径维护自身权益。
- 与 Mojang / Microsoft 无关联；Minecraft 是 Mojang Studios 的商标。
- 开源协议：**MIT License**，Copyright (c) 2026 Xyq_yy，详见 `LICENSE`；程序“关于”页内可查看完整协议。
- 详细使用说明见 `README.md`。
