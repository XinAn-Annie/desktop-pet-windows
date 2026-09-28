# Windows 桌宠工作台 MVP

这是 Windows 桌面宠物工作台原型，核心操作是：

1. 使用系统截图工具截图。
2. 按 `Ctrl+C` 复制截图。
3. 点击桌面宠物打开工作台。
4. 在“任务与 DDL”输入框按 `Ctrl+V`。
5. 程序从剪贴板读取图片，在本地 OCR，然后把文字填入输入框。
6. 选择事件类型后点击“解析并添加”。

首次运行会询问登录后的显示方式和全局快捷键，默认 `win+alt+o`。桌宠右键菜单支持打开工作台、隐藏/显示桌宠、上传图片桌宠形象和设置。

## 安装

需要 Windows、Python 3.10+。安装脚本会优先寻找可用的 `python.exe`，不会依赖可能失效的 `py.exe`；如果缺少 Tesseract，会询问是否通过 winget 安装。PowerShell 中运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\.venv\Scripts\python.exe app.py
```

不要把桌宠作为 PowerShell 前台进程长期运行，否则关闭 PowerShell 会结束桌宠。安装依赖后，建议双击 `start_desktop_pet.vbs` 后台启动；它不会打开控制台窗口。

首次运行选择一种登录后的行为：自动显示桌宠，或后台驻留并通过快捷键显示/隐藏。全局快捷键需要后台进程运行，因此快捷键模式会在 Windows 登录后启动到系统托盘，但不显示桌宠本体。托盘菜单提供显示/隐藏、打开工作台、设置和退出。之后可右键桌宠或托盘图标 → “桌宠设置”修改。

按住桌宠并拖动即可改变位置，位置会保存在本机设置中。单击打开工作台；长按但不移动不会误开工作台。

Tesseract 的中文识别需要安装 `chi_sim` 语言包。若 Tesseract 不在 PATH，可设置：

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

也可以在 PowerShell 手动安装 Tesseract：

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

安装器中请勾选 English 和 Chinese Simplified (`chi_sim`) 语言数据。程序会自动检查引擎和语言包；缺少引擎时会在 OCR 输入区说明修复命令，缺少中文包时会降级到英文识别并提示。

## 当前完成

- Windows 透明桌宠窗口
- 点击打开工作台
- 月历视图，点击日期显示当天全部安排
- DDL、普通事件、规律事件、会议四种颜色分类
- 支持 `9月10-29每天上午2点上课` 形式的规律事件
- 文本粘贴
- 图片剪贴板粘贴
- 本地 OCR
- 当前可见应用窗口、进程 PID 和内存占用
- DDL 提前一小时和到期提醒，普通日程到点提醒
- RSS/Atom 定时监控和公开网页文字变化检查
- 更新内容在本机生成简短摘要，并通过托盘提醒
- 用户上传图片作为静态宠物形象

## 下一步

- AI 图片生成宠物形象和动画状态（目前需要配置图像模型服务）
- 课表与教务网集成（后续单独评估）
