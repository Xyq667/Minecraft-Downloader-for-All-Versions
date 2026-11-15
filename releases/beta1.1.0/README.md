# MC 全版本下载器 Beta 1.1.0

发布日期：2026-10-29

## 更新内容

- 新增应用数据迁移：数据目录改为 `D:\MCVersionDownloader\<版本号>`，旧设置、缓存、日志自动复制
- 新增 Fabric / Quilt 模组加载器下载与合并，生成独立加载器版本目录
- 新增版本类型筛选：全部 / 正式版 / 快照 / 旧版 / 已安装，并显示数量
- 新增启动器目录自动检测，支持官方启动器、HMCL、PCL2、Prism、MultiMC 等
- 优化下载：多路并发、HTTP keep-alive 连接复用，减少小文件重复握手
- 其他：SHA-1 校验、失败重试、已存在文件跳过、下载进度与速度显示

## 文件

- `MC全版本下载器.exe` — 主程序
- `source/` — 完整源码

## 说明

- 本版本不包含 Java 运行时下载、下载时间统计和取消后删除文件
- 与 Mojang / Microsoft 无关联，Minecraft 是 Mojang Studios 的商标
- 开源协议：MIT
