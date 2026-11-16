# Interception DLL

已有的 x64 `interception.dll` 可以放在本目录，也可以在启动时通过 `--driver-dll` 指定路径。

通常可直接使用高级设置中的“安装 / 修复驱动”。下载的 DLL 会保存到 `%LOCALAPPDATA%\KeyWeave\drivers`，下次启动时优先读取；显式指定路径时使用指定文件。

DLL 和系统驱动是两部分。仅复制 DLL 不能代替驱动安装，安装完成后还需要重启电脑。

源码包和 wheel 不附带第三方驱动二进制文件。驱动由 [Interception 项目](https://github.com/oblitum/Interception) 发布。
