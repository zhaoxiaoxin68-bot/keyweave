# 键序 · KeyWeave

键序是一款 Windows 按键连发工具。可以按分组管理键位，设置组合键、发送间隔和循环顺序，也可以限定只在某个程序位于前台时响应。

界面使用 PySide6，配置保存在本机 SQLite 数据库中。当前版本为 0.3.0。

## 安装与启动

运行环境：Windows x64、Python 3.11 或更高版本。首次安装需要联网下载 PySide6。

在项目目录打开 PowerShell：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe run.py
```

安装后，也可以通过模块启动：

```powershell
.venv\Scripts\python.exe -m keyweave
```

程序启动时不会发送按键。默认只有一个分组和按键 `1`，`F8` 启停连发，`F9` 选择下一个按键。

## 使用方法

### 分组与按键

在“按键控制”中创建分组，再点击“添加键”录入按键或组合键。支持键盘按键、鼠标中键和两枚侧键。

- 单击按键卡片选择当前键位。
- 双击修改键位，按 `Esc` 取消录入。
- 右键可以修改、启用、禁用或删除按键。
- 分组可以重命名、删除，至少保留一个分组。

组合键在主键按下时确认。单独设置 Ctrl、Shift 等修饰键时，在松开后确认。录入超过 12 秒会取消。

删除或修改期间，如果目标分组或按键已经变化，程序会取消这次操作并提示重试。

### 连发参数

连发间隔可设为 5–2000ms，按住时长可设为 1–500ms。普通连发的按住时长不能超过发送间隔。

高级设置提供以下运行方式。鼠标停留在选项上可以查看说明。

| 选项 | 用途 |
|---|---|
| 按已配置的键选中对应按键 | 按下已配置的键时选中它，可以跨分组选择 |
| 依次连发 | 当前组内启用的按键轮流发送，每键间隔 50ms |
| 持续按住选中键 | 保持按下当前键，直到停止 |
| 启动后，按任意键开始 | 启动后先等待，收到非快捷键的实际按键后开始发送 |
| 按住连发，松开停止 | 启动后，只在按住已配置按键时连发 |

“持续按住选中键”不能与“依次连发”或“按住连发”同时开启。依次连发时，按住时长不能超过 50ms。

### 循环顺序

点击“循环顺序”，输入组号和按键序号，例如：

```text
1.1 → 1.1 → 2.3
```

这表示先选择第 1 组第 1 个按键两次，再选择第 2 组第 3 个按键。也可以使用空格或逗号分隔，最多保存 60 步。

点击“开始记录”后，按下已配置的按键即可记录顺序；重复步骤会保留。录制过程中不进行连发。停止记录后可以继续编辑，再点击“保存顺序”。

设置循环后，“选择下一个按键”会按循环前进一步。没有设置循环时，它会选择当前组内下一个启用的按键。“循环归位”回到第一个可用步骤，不会清除配置。

### 额外按键连发

在高级设置中指定“额外按键连发”的快捷键。触发后，程序重复发送这个快捷键本身，每次按住 20ms，每 100ms 调度一次，最长运行 10 秒。再次触发会提前停止。

它可以独立于主连发运行。“停止全部”会同时停止主连发、额外连发和循环录制。

### 响应窗口

“响应窗口”可选择当前运行的程序，也可以输入完整进程名，例如 `Example.exe`。输入后按回车，或离开输入框时应用。

进程名不区分大小写，但必须完整匹配。切出指定程序后暂停发送并释放按键，切回后恢复。快捷键也受这个范围限制。选择“所有程序”时全局生效。

### 小窗

点击右上角“小窗”进入置顶窗口。小窗可以切换分组、选择按键、启停连发，并在 100%、85%、70%、55% 四档透明度之间切换。

小窗最多显示三行按键，超过的部分可以滚动查看。点击“展开”或关闭小窗，会返回主窗口。

## 输入后端与驱动

| 发送方式 | 行为 |
|---|---|
| 自动 | 优先使用 Interception，不可用时使用 SendInput 并显示原因 |
| SendInput | 使用 Windows 系统接口，不需要安装额外驱动 |
| Interception | 使用已安装的 Interception 驱动；不可用时报告错误 |

选择发送方式后会应用。键盘设备号 `0` 表示自动选择，`1–10` 表示指定 Interception 键盘设备。设备号右侧的“应用”按钮也可以重新检测当前后端，即使选项没有改变。

发送或释放按键失败时，程序会停止连发、显示后端异常，并阻止再次启动。重新检测成功后，需要手动启动连发。

### 安装驱动

在高级设置中点击“安装 / 修复驱动”。程序会下载官方 Interception v1.0.1 安装包，核对 SHA-256，然后请求 Windows 管理员授权。

安装完成后需要重启电脑。程序只显示提示，不会自动重启。安装期间不能退出程序；授权取消或安装失败后可以重试。

配套 DLL 保存到：

```text
%LOCALAPPDATA%\KeyWeave\drivers\interception.dll
```

只有 DLL 文件还不够，系统驱动也必须安装并生效。程序确认发送接口成功，不代表目标软件一定接受输入；接收行为也受目标软件和权限影响。

### 启动参数

```powershell
python run.py --backend sendinput
python run.py --backend interception --driver-dll "C:\Drivers\interception.dll"
python run.py --config "D:\KeyWeave\config.sqlite3"
```

| 参数 | 说明 |
|---|---|
| `--backend` | `auto`、`sendinput` 或 `interception` |
| `--driver-dll` | 指定现有 x64 DLL 的路径，优先于自动查找 |
| `--config` | 指定本地 SQLite 配置文件 |

未指定 DLL 路径时，优先使用已下载的副本，再尝试源码目录中的 `assets/interception.dll`。

## 配置文件

默认配置位置：

```text
%APPDATA%\KeyWeave\config.sqlite3
```

日志文件 `app.log` 保存在配置文件旁边。修改配置后会写入数据库，不需要另存。提示音量和键盘设备号使用各自的“应用”按钮。

高级设置支持导入、导出 JSON 配置。导入前会停止发送，文件中的 `schema_version` 必须为 `1`。导入内容包括分组、键位、快捷键、循环顺序和其他设置。

“恢复默认设置”会清除自定义内容，恢复一个默认分组及按键 `1`，并停止发送和录入。配置读取失败时，原文件会保留，后续修改写入同目录的 `recovered-config.sqlite3`。

## 代码结构

| 文件 | 职责 |
|---|---|
| `run.py` | 源码启动入口 |
| `src/keyweave/__main__.py` | 启动参数、日志和程序生命周期 |
| `src/keyweave/ui.py` | 主窗口、设置页、小窗和循环编辑 |
| `src/keyweave/ui_widgets.py` | 共用控件与界面样式 |
| `src/keyweave/runtime.py` | 命令队列、输入事件、状态快照和资源清理 |
| `src/keyweave/controller.py` | 连发、选键、分组和循环状态 |
| `src/keyweave/engine.py` | 发送调度及按键释放 |
| `src/keyweave/backends.py` | SendInput 和 Interception 调用 |
| `src/keyweave/hooks.py` | 系统键鼠监听 |
| `src/keyweave/windows.py` | Win32 接口、进程识别和单实例限制 |
| `src/keyweave/models.py` | 配置数据结构与参数校验 |
| `src/keyweave/config.py` | JSON 读写与 SQLite 存储 |
| `src/keyweave/driver_setup.py` | 驱动下载和安装 |
| `src/keyweave/sound.py` | 提示音 |

界面通过队列向运行线程提交命令，再读取状态快照更新控件。主页面和设置页面只创建一次，切换时保留输入内容和滚动位置。

退出时依次停止监听、释放按键、关闭后端并释放单实例句柄。

## 生成 Windows exe

在 Windows x64 上安装 Python 3.11 或更新版本，双击项目根目录的 `build_exe.bat`。脚本会创建虚拟环境、安装依赖并打包。

也可以在项目目录运行：

```powershell
python -m pip install ".[build]"
python tools/build_exe.py
```

生成文件位于 `artifacts/KeyWeave.exe`，可以直接复制到其他 Windows x64 电脑运行，无需安装 Python 或 PySide6。旁边的 `KeyWeave.exe.sha256` 用于核对文件。

这是直接运行的单文件程序，不带安装向导。启动时会将运行库解压到临时目录，因此首次启动需要一点时间。配置仍保存在 `%APPDATA%\KeyWeave`，不写入 exe 所在目录。

Interception 驱动不打入 exe，需要时从程序内下载安装；SendInput 模式无需驱动。手动提供 DLL 时，可以放在 exe 旁边的 `assets/interception.dll`，也可以通过 `--driver-dll` 指定。

exe 必须在 Windows 上构建，Linux 上不能直接用 PyInstaller 生成 Windows 程序。

## 构建 Python 安装包

```powershell
python tools/build_release.py
```

输出位于 `artifacts`：

- `keyweave-source.zip`：源码压缩包。
- `keyweave-0.3.0-py3-none-any.whl`：Python 安装包。
- `checksums.json`：文件大小和 SHA-256。

wheel 不包含 Qt 运行库或第三方驱动。安装时会按包内依赖声明安装 PySide6。

## 许可证

项目使用 MIT 许可证，见 [LICENSE](LICENSE)。Interception 驱动由其原项目单独发布。
