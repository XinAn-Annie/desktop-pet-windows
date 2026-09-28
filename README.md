# Windows 桌宠工作台 MVP

这是 Windows 桌面宠物工作台原型，核心操作是：

1. 使用系统截图工具截图。
2. 按 `Ctrl+C` 复制截图。
3. 点击桌面宠物打开工作台。
4. 在“任务与 DDL”输入框按 `Ctrl+V`。
5. 程序从剪贴板读取图片，在本地 OCR，然后把文字填入输入框。
6. 选择事件类型后点击“解析并添加”。

首次运行会询问：

- 是否开机自启
- 启动快捷键，默认 `win+alt+o`

桌宠右键菜单支持打开工作台、隐藏/显示桌宠、上传图片桌宠形象和退出。

## 安装

需要 Windows、Python 3.10+ 和 Tesseract OCR。安装脚本会优先寻找可用的 `python.exe`，不会依赖可能失效的 `py.exe`。PowerShell 中运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\.venv\Scripts\python.exe app.py
```

不要把桌宠作为 PowerShell 前台进程长期运行，否则关闭 PowerShell 会结束桌宠。安装依赖后，建议双击 `start_desktop_pet.vbs` 后台启动；它不会打开控制台窗口。

首次运行会让你在“开机自启”与“快捷键唤醒”之间二选一。快捷键模式下，使用 `start_desktop_pet.vbs` 启动一次程序后，桌宠默认隐藏，按快捷键显示/隐藏；开机自启模式则登录 Windows 后自动显示桌宠。之后可右键桌宠 → “桌宠设置”修改。

长按桌宠并拖动即可改变位置，松开鼠标后保留本次运行位置。

Tesseract 的中文识别需要安装 `chi_sim` 语言包。若 Tesseract 不在 PATH，可设置：

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## 当前完成

- Windows 透明桌宠窗口
- 点击打开工作台
- 月历视图，点击日期显示当天全部安排
- DDL、普通事件、规律事件、会议四种颜色分类
- 支持 `9月10-29每天上午2点上课` 形式的规律事件
- 文本粘贴
- 图片剪贴板粘贴
- 本地 OCR
- 课表图片 OCR 后人工确认并添加
- 当前运行程序、PID、内存占用
- 网址提醒界面占位

## 下一步

- 使用 SQLite 持久化任务
- 接入 Windows OCR，减少 Tesseract 安装负担
- 支持 RSS/Atom 和网页变化监控
- 任务优先级、提醒和 Windows 通知
- 图片生成宠物形象和动画状态
