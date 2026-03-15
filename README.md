# DeskPilot

一个可以操控你的鼠标和键盘的智能体。

这是一个本地运行的 Windows 桌面智能体项目。它会在你的电脑上直接执行鼠标、键盘、窗口切换、浏览器、OCR、Windows UI Automation 等操作，模型侧通过 OpenAI 兼容接口接入，所以可以对接官方 API，也可以对接你自己的中转站或代理服务。

当前版本已经包含：

- 屏幕截图驱动的桌面操作
- 鼠标移动、点击、拖拽、滚动
- 键盘输入、快捷键
- 窗口枚举与聚焦
- Playwright 浏览器 DOM 自动化
- Tesseract OCR
- Windows UI Automation
- Tkinter 图形界面控制台
- `0 = unlimited` 的无限步数模式
- 浏览器策略切换：
  - `Reuse current browser`
  - `Allow new controlled browser`

## 1. 适用场景

适合这类本地自动化任务：

- 打开桌面程序并执行固定流程
- 在多个 Windows 应用之间切换复制信息
- 结合截图、OCR、UIA 去操作普通软件界面
- 需要模型读屏幕、规划下一步并持续执行
- 使用 OpenAI 兼容 `base_url` 的本地 GUI 智能体

不适合的场景：

- 需要浏览器扩展级、内核级权限的自动化
- 需要绕过 UAC、安全桌面、杀软注入等系统限制
- 不可信第三方中转站上的高敏感账号操作

## 2. 项目结构

- `run_dashboard.py`
  - GUI 入口
- `start_dashboard.bat`
  - Windows 双击启动脚本
- `start_dashboard_admin.ps1`
  - 提权启动 GUI
- `run_agent.py`
  - CLI 入口
- `desktop_operator/dashboard.py`
  - GUI 控制台
- `desktop_operator/runner.py`
  - 智能体主循环
- `desktop_operator/runtime.py`
  - 桌面 / 浏览器 / OCR / UIA 统一工具层
- `desktop_operator/controller.py`
  - 鼠标、键盘、窗口、截图
- `desktop_operator/browser.py`
  - Playwright 浏览器控制
- `desktop_operator/ocr.py`
  - Tesseract OCR
- `desktop_operator/ui_automation.py`
  - Windows UI Automation
- `.env.example`
  - 环境变量模板

## 3. 环境要求

- 操作系统：Windows
- Python：3.10+
- 推荐 Conda 环境名：`openai`
- 推荐直接在本机桌面环境下运行，不要在无桌面的远程会话里使用

## 4. 安装步骤

### 4.1 进入环境

```powershell
conda activate openai
cd D:\openai-desktop-agent
```

### 4.2 安装依赖

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

### 4.3 准备环境变量

```powershell
Copy-Item .env.example .env
```

然后编辑 `D:\openai-desktop-agent\.env`。

## 5. 如何接入 API Key 和 Base URL

这个项目使用 OpenAI 兼容接口，所以至少需要这 3 个配置：

```dotenv
OPENAI_API_KEY=你的_api_key
OPENAI_BASE_URL=https://你的接口地址/v1
DESKTOP_AGENT_MODEL=你要使用的模型名
```

示例：

```dotenv
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
OPENAI_BASE_URL=https://example.com/v1
DESKTOP_AGENT_MODEL=gpt-5.4
```

说明：

- `OPENAI_API_KEY`
  - 你的接口密钥
- `OPENAI_BASE_URL`
  - OpenAI 兼容接口地址，通常建议以 `/v1` 结尾
- `DESKTOP_AGENT_MODEL`
  - 你希望调用的模型名，例如 `gpt-5.4`

如果你接的是官方 OpenAI，而不是中转站：

- `OPENAI_BASE_URL` 可以留空
- 只保留 `OPENAI_API_KEY`
- `DESKTOP_AGENT_MODEL` 填你实际可用的模型名

## 6. 推荐的 `.env` 配置

下面是一组比较适合当前项目的常用配置：

```dotenv
OPENAI_API_KEY=你的_api_key
OPENAI_BASE_URL=https://你的接口地址/v1
DESKTOP_AGENT_MODEL=你的模型名
DESKTOP_AGENT_OPENAI_TRUST_ENV=false

DESKTOP_AGENT_MAX_STEPS=0
DESKTOP_AGENT_ACTION_PAUSE_SECONDS=0.10
DESKTOP_AGENT_DRY_RUN=false
DESKTOP_AGENT_ALLOW_SHELL=false
DESKTOP_AGENT_RUNS_DIR=./runs

DESKTOP_AGENT_BROWSER_HEADLESS=false
DESKTOP_AGENT_BROWSER_ENGINE=chromium
DESKTOP_AGENT_BROWSER_USER_DATA_DIR=./.browser-state
DESKTOP_AGENT_BROWSER_TIMEOUT_MS=15000
DESKTOP_AGENT_MAX_BROWSER_ELEMENTS=20
DESKTOP_AGENT_PREFER_EXISTING_BROWSER_WINDOW=true
DESKTOP_AGENT_PROMPT_IMAGE_MAX_SIDE=1440
DESKTOP_AGENT_PROMPT_IMAGE_QUALITY=70

DESKTOP_AGENT_OCR_LANG=chi_sim+eng
DESKTOP_AGENT_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSDATA_PREFIX=D:\openai-desktop-agent\tessdata

DESKTOP_AGENT_INCLUDE_UIA_IN_PROMPT=true
DESKTOP_AGENT_MAX_HISTORY_MESSAGES=60
DESKTOP_AGENT_MAX_SAVED_SCREENSHOTS=200
DESKTOP_AGENT_ALLOWED_COMMAND_PREFIXES=
DESKTOP_AGENT_AUTO_RUN_DOCTOR=false
```

几个关键项：

- `DESKTOP_AGENT_MAX_STEPS=0`
  - `0` 表示不限步数
- `DESKTOP_AGENT_ALLOW_SHELL=false`
  - 默认关闭本地程序启动；只有你确实需要时再打开，并建议同时配置 `DESKTOP_AGENT_ALLOWED_COMMAND_PREFIXES`
- `DESKTOP_AGENT_OPENAI_TRUST_ENV=false`
  - 默认不继承系统代理，适合很多中转站环境
- `DESKTOP_AGENT_PREFER_EXISTING_BROWSER_WINDOW=true`
  - 默认优先复用你已经打开的浏览器，而不是自己新开一个
- `DESKTOP_AGENT_PROMPT_IMAGE_QUALITY=70`
  - 当前桌面截图会保持原始分辨率，避免坐标漂移；这里只调压缩质量，不再缩放截图
- `TESSDATA_PREFIX`
  - 可以直接指向你的 `tessdata` 目录；现在会从 `.env` 正常加载
- 内置执行 critic
  - 运行器会拦截同一张截图上的多次状态变更动作，避免连点、连输入、连跳页面导致上下文过时

## 7. OCR 安装

如果你希望模型识别屏幕文字，需要安装 Tesseract。

常见安装路径：

```text
C:\Program Files\Tesseract-OCR\tesseract.exe
```

安装好后，在 `.env` 中设置：

```dotenv
DESKTOP_AGENT_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSDATA_PREFIX=D:\openai-desktop-agent\tessdata
DESKTOP_AGENT_OCR_LANG=chi_sim+eng
```

如果 `doctor` 报 OCR unavailable，优先检查：

- `tesseract.exe` 是否真的存在
- `DESKTOP_AGENT_TESSERACT_CMD` 是否填写正确
- `tessdata` 目录是否存在所需语言包

## 8. 启动前检查

先跑一次能力检查：

```powershell
python run_agent.py doctor --env-file .env
```

它会检查：

- 屏幕截图是否可用
- 当前窗口枚举是否正常
- Playwright 浏览器是否可用
- OCR 是否可用
- Windows UI Automation 是否可用

## 9. GUI 使用方法

### 9.1 启动 GUI

方式 1：

```powershell
python run_dashboard.py --env-file .env
```

方式 2：

```powershell
D:\openai-desktop-agent\start_dashboard.bat
```

如果需要管理员权限：

```powershell
powershell -ExecutionPolicy Bypass -File D:\openai-desktop-agent\start_dashboard_admin.ps1
```

### 9.2 GUI 里主要看什么

当前 GUI 里比较重要的区域有：

- `Task Studio`
  - 输入任务目标
- `Live Reply`
  - 模型的中间回复会实时显示在这里
- `Live Screen`
  - 当前步骤截图
- `Overview`
  - 最终答复
- `Log`
  - 工具调用和事件日志
- `Windows`
  - 当前可见窗口列表
- `Runs`
  - 最近运行记录

### 9.3 GUI 里几个关键开关

- `Dry run`
  - 只记录动作，不真正点击鼠标和键盘
- `Allow shell launch`
  - 允许模型启动程序或命令
- `Browser mode`
  - `Reuse current browser`
    - 优先复用你当前已打开的浏览器窗口
  - `Allow new controlled browser`
    - 允许模型自己新开一个 Playwright 受控浏览器
- `Max steps (0 = unlimited)`
  - `0` 表示不限步数

## 10. CLI 使用方法

### 10.1 先 dry-run

```powershell
python run_agent.py run "打开记事本并输入 hello from desktop agent" --dry-run --env-file .env
```

### 10.2 真正执行

```powershell
python run_agent.py run "打开记事本并输入 hello from desktop agent" --env-file .env
```

### 10.3 指定步数

```powershell
python run_agent.py run "执行一个长流程任务" --max-steps 0 --env-file .env
```

说明：

- `--max-steps 0`
  - 不限步数
- `--dry-run`
  - 不真正操作鼠标键盘

## 11. 浏览器模式说明

### 11.1 复用当前浏览器

这是当前默认模式。

适合：

- 你已经手动打开 Chrome / Edge / 目标网页
- 你希望模型直接在你当前浏览器窗口上继续操作
- 你不希望它自己新开一个浏览器

此时模型会优先尝试：

- `focus_window`
- 鼠标键盘操作
- OCR
- UI Automation

### 11.2 新开受控浏览器

如果切换到 `Allow new controlled browser`，模型可以调用 Playwright 新开一个独立浏览器窗口。

适合：

- 你要稳定 DOM 自动化
- 你接受它自己管理一个浏览器会话
- 你不要求必须用你当前已经打开的浏览器

### 11.3 连接到已打开浏览器的 DOM

如果你既想复用已有浏览器，又想拿 DOM，而不是只靠截图和点击，那么需要让浏览器支持 CDP。

例如手动启动 Chrome：

```powershell
chrome.exe --remote-debugging-port=9222
```

然后让智能体调用：

```text
browser_connect_cdp(endpoint_url="http://127.0.0.1:9222")
```

这样它才能直接读取已打开浏览器的标签页和 DOM。

## 12. 常见工作流写法

### 12.1 桌面应用

```text
打开微信和记事本，把微信当前窗口可见的待办整理成中文清单，写到记事本。每次发送消息前先暂停并等待我确认。
```

### 12.2 浏览器 + 桌面混合

```text
复用我当前已经打开的浏览器窗口，检查当前页面内容，提取关键信息，再切换到 Excel 录入。除非我明确同意，不要打开新的浏览器窗口。
```

### 12.3 长流程任务

```text
持续执行这个批量流程，直到全部完成或者遇到明确阻塞。不要因为做了一部分就提前结束。每完成一项都在最终总结里报告累计数量。
```

## 13. 运行结束的判断规则

当前智能体不会因为“随便说一句完成了”就结束。

它的正常结束条件是：

- `TASK_COMPLETE:`
  - 任务真正完成
- `TASK_BLOCKED:`
  - 有明确阻塞，无法继续
- 你手动点击 `Stop`
- 如果设置了正整数步数，则达到步数上限

如果 `Max steps = 0`，就不会因为步数限制自动停掉。

## 14. 安全与权限说明

- 鼠标键盘操作是在本机真实执行的
- `PyAutoGUI` failsafe 默认开启
- 把鼠标快速移到左上角可以触发 failsafe
- 如果目标程序是管理员权限，建议 GUI 也以管理员身份启动
- UAC、安全桌面、某些杀软、某些游戏保护、某些浏览器沙箱仍然可能阻止自动化
- 不要把高敏感密码交给不可信的第三方中转站

## 15. 常见问题

### 15.1 为什么它会自己新开浏览器

如果你选择了 `Allow new controlled browser`，或者任务明确要求用 Playwright DOM 自动化，它可能会新开浏览器。

如果你不想这样：

- 在 GUI 里选择 `Reuse current browser`
- `.env` 里保持 `DESKTOP_AGENT_PREFER_EXISTING_BROWSER_WINDOW=true`

### 15.2 为什么它看得到页面，但还是点得不准

常见原因：

- 页面在滚动
- 元素是 canvas 或自绘控件
- DPI 缩放较高
- 当前窗口没真正聚焦
- OCR 识别到的文字位置不稳定

这时更适合：

- 先 `Run Doctor`
- 明确要求它先聚焦窗口
- 在任务描述里写明不要盲点，优先 OCR / UIA / DOM

### 15.3 为什么 GUI 里看不到模型回复

现在中间回复在 `Live Reply`，最终结果在 `Overview -> Final Answer`。

### 15.4 为什么它做一半就停

优先检查：

- `Max steps` 是否过小
- 是否误填了正整数限制
- 模型是否输出了 `TASK_BLOCKED:`
- 运行日志里是否出现明确报错

当前推荐长流程直接使用：

```dotenv
DESKTOP_AGENT_MAX_STEPS=0
```

## 16. 快速开始

只想最快跑起来，可以直接按下面做：

```powershell
conda activate openai
cd D:\openai-desktop-agent
Copy-Item .env.example .env
notepad .env
```

在 `.env` 填入：

```dotenv
OPENAI_API_KEY=你的_api_key
OPENAI_BASE_URL=https://你的接口地址/v1
DESKTOP_AGENT_MODEL=你的模型名
DESKTOP_AGENT_MAX_STEPS=0
DESKTOP_AGENT_ALLOW_SHELL=false
DESKTOP_AGENT_PREFER_EXISTING_BROWSER_WINDOW=true
```

然后运行：

```powershell
python run_agent.py doctor --env-file .env
D:\openai-desktop-agent\start_dashboard.bat
```

## 17. 免责声明

这是一个高权限本地自动化工具。请只在你信任的模型接口、你理解的任务范围、以及你能承担后果的本地环境中使用。
