# OpenNovel Desktop — GUI 视觉设计规范

> 版本 1.0 · 基于 PySide6 (Qt for Python) · 亮/暗双主题 · "文学驾驶舱"设计语言

---

## 1. 设计原则

| 原则 | 含义 |
|:---|:---|
| **文学沉浸** | 编辑器区以衬线正文 + 暖白纸色模拟"纸上写作"心流，与右侧工程面板形成有温度的对比 |
| **工程克制** | UI 外壳保持清晰的信息层级和严谨的间距系统，不以装饰牺牲可用性 |
| **安静自信** | 不炫耀视觉技巧。扁平为主，微阴影/细边框仅用于区分层级。状态反馈清晰但不喧宾夺主 |
| **鲁棒优先** | 所有交互状态（hover/active/disabled/error）都有明确的视觉反馈。不依赖动效传达关键信息 |

---

## 2. 色彩系统

### 2.1 亮色主题

```
背景底色       #FAF9F6  (暖白/米白，模拟纸张)
表面卡片色     #FFFFFF  (纯白，用于面板/卡片)
表面悬浮色     #F5F4F0  (极浅灰，用于 hover/选中态)
边框色         #E4E2DD  (浅灰褐，1px 细边框)
三级文本色     #A8A49E  (浅灰，用于辅助信息/占位符)
二级文本色     #6B6863  (中灰，用于标签/说明文字)
一级文本色     #2D2A26  (深灰黑，主正文色，不用纯黑)

强调色(默认)   #B85C4A  (暗朱红/赤陶色，按钮/高亮/选中标记)
强调色(hover)  #A04D3C  (加深，按钮悬停)
强调色(active)  #8B3F30  (再加深，按钮按下)
强调色(淡)     #F3E4E0  (极淡朱红，用于选中行背景/标签底色)

成功           #4A7C5B  (暗绿，不刺眼)
警告           #C4913A  (暖金，与强调色协调)
错误           #B85C4A  (复用强调色，减少颜色数量)
信息           #5A7C9A  (暗蓝，用于链接/信息提示)
```

### 2.2 暗色主题

```
背景底色       #1E1E20  (深石墨灰，不用死黑)
表面卡片色     #28282A  (略浅，用于面板/卡片)
表面悬浮色     #323234  (hover/选中态底色)
边框色         #3A3A3D  (深灰边框)
三级文本色     #6E6E72  (辅助信息)
二级文本色     #9A9A9E  (标签/说明)
一级文本色     #E4E4E7  (主正文色)

强调色(默认)   #C96A58  (暗朱红提亮一档，暗色下保持可读性)
强调色(hover)  #D97A68  (悬停变亮)
强调色(active)  #B85A48  (按下变暗)
强调色(淡)     #3A2828  (选中行背景/标签底色)

成功           #5A8C6B
警告           #C4A14A
错误           #C96A58
信息           #6A8CAC
```

### 2.3 语义色对照表 (亮/暗)

| 用途 | 亮色 | 暗色 |
|:---|:---|:---|
| Pipeline 运行中 | #5A7C9A (暗蓝) | #6A8CAC |
| Pipeline 通过 | #4A7C5B (暗绿) | #5A8C6B |
| Pipeline 有问题 | #C4913A (暖金) | #C4A14A |
| Pipeline 失败 | #B85C4A (朱红) | #C96A58 |
| Pipeline 未开始 | #C8C6C2 | #4A4A4D |
| Critic 高亮锚定 | #B85C4A 淡 20% 背景 | #C96A58 淡 20% 背景 |
| 内联 Diff 新增 | 绿底 #E8F5E9 | 绿底 #2A3A2E |
| 内联 Diff 删除 | 红底 #FFEBEE 删除线 | 红底 #3A2828 删除线 |
| 搜索匹配高亮 | 暖黄底 #FFF3D6 | 暖黄底 #3A3420 |

---

## 3. 字体系统

### 3.1 三层字体映射

| 层级 | 西文 | 中文 | 回退 |
|:---|:---|:---|:---|
| **UI** (菜单/按钮/状态栏/面板) | `Inter` | `思源黑体` / `Noto Sans CJK SC` | `"Microsoft YaHei", "PingFang SC", sans-serif` |
| **正文编辑** (NovelEditor) | `Inter` | `思源宋体` / `Noto Serif CJK SC` | `"Source Han Serif SC", "SimSun", serif` |
| **等宽** (Diff/日志/Token数据) | `JetBrains Mono` | `JetBrains Mono` 仅西文，中文回退 UI 字体 | `"Cascadia Code", "Consolas", monospace` |

### 3.2 字号与行高

| 用途 | 字号 | 行高 | 字重 |
|:---|:---|:---|:---|
| 编辑器正文 | 16px (亮) / 15px (暗) | 1.8 | Regular 400 |
| 编辑器标题 H1 | 24px | 1.6 | Bold 700 |
| 编辑器标题 H2 | 20px | 1.6 | Bold 700 |
| UI 导航文本 | 13px | 1.4 | Medium 500 |
| 面板标题 | 14px | 1.4 | SemiBold 600 |
| 面板正文/列表 | 13px | 1.5 | Regular 400 |
| 状态栏 | 12px | 1.3 | Regular 400 |
| 标签/徽标 (Badge) | 11px | 1.2 | Medium 500 |
| 等宽数据 (Diff) | 13px | 1.5 | Regular 400 (JetBrains Mono) |
| 按钮文字 | 13px | — | Medium 500 |
| Tooltip | 12px | 1.4 | Regular 400 |

> 暗色主题下调小 1px 是因为浅色字在深色背景上视觉偏大，保持阅读舒适度。

### 3.3 编辑器行距详解

QPlainTextEdit 需通过 QSS 或 `setStyleSheet()` 设置行高：
```css
/* 编辑器正文区域 */
QPlainTextEdit {
    font-family: "Noto Serif CJK SC", "Source Han Serif SC", "SimSun", serif;
    font-size: 16px;
    line-height: 1.8;        /* → 实际 ~29px，给汉字呼吸空间 */
    letter-spacing: 0.02em;  /* 极微字间距，提升长篇可读性 */
}
```

---

## 4. 间距与布局

### 4.1 基础单位

- **基础间距单位**: 4px
- **间距阶梯**: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 px

### 4.2 面板间距约定

```
MainWindow 内容边距     16px (内边距，非 QSS padding 而是布局 margins)
左侧导航面板宽度        240px (固定)
右侧面板宽度            320px (默认，用户可通过分割器调整 280-480)
面板间分割器宽度        4px (分割条，hover 时变色)
主工具栏高度            48px (含上下 8px padding)
状态栏高度              28px
标签栏高度              36px (右侧 QTabBar)
```

### 4.3 内边距约定

```
QListWidget/QTreeView 项 padding     8px 12px
QPushButton 内容 padding             8px 20px (水平)
QTabWidget 标签页内容 padding        16px
QGroupBox 标题偏移                   上 16px，内边距 16px
卡片/分组面板 padding                16px
输入框 (QLineEdit) padding           8px 12px (含 1px 边框)
```

---

## 5. 圆角与边框

| 组件 | 圆角 | 边框 |
|:---|:---|:---|
| 主编辑器 | 0px（直角） | 无边框，靠背景底色区分 |
| 面板/卡片 | 4px | 1px solid $border-color |
| 按钮 | 4px | 无关色，hover 加深 |
| 输入框 | 4px | 1px solid $border-color，focus 变强调色 |
| 标签/Badge | 2px | 无 |
| 工具提示 Tooltip | 4px | 无 |
| 对话框 (QDialog) | 0px（系统窗口） | 无 |
| 分割器 (QSplitter) | 0px | 2px wide，透明背景，hover 强调色 |

---

## 6. QSS 组件指南

### 6.1 MainWindow 与全局

```css
/* 亮色 */
QMainWindow { background: #FAF9F6; }
QWidget { font-family: "Inter", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; }

/* 暗色 */
QMainWindow { background: #1E1E20; }
```

### 6.2 QPlainTextEdit (NovelEditor)

```css
/* 亮色 — "纸上写作"调性 */
QPlainTextEdit {
    background: #FAF9F6;
    color: #2D2A26;
    font-family: "Noto Serif CJK SC", "Source Han Serif SC", "SimSun", serif;
    font-size: 16px;
    line-height: 1.8;
    padding: 24px 32px;       /* 宽敞的左右内边距，模拟书页留白 */
    selection-background-color: #F3E4E0;  /* 赤陶淡色选中 */
    border: none;
}

/* 暗色 */
QPlainTextEdit {
    background: #1E1E20;
    color: #E4E4E7;
    selection-background-color: #3A2828;
}
```

### 6.3 按钮 (QPushButton)

```css
/* 亮色 — 主按钮 (强调色) */
QPushButton {
    background: #B85C4A;
    color: #FFFFFF;
    border: none;
    border-radius: 4px;
    padding: 8px 20px;
    font-size: 13px;
    font-weight: 500;
    font-family: "Inter", "Noto Sans CJK SC", sans-serif;
}
QPushButton:hover {
    background: #A04D3C;
}
QPushButton:pressed {
    background: #8B3F30;
}
QPushButton:disabled {
    background: #E4E2DD;
    color: #A8A49E;
}

/* 亮色 — 次级按钮 (透明，带边框) */
QPushButton#SecondaryButton {
    background: transparent;
    color: #2D2A26;
    border: 1px solid #E4E2DD;
}
QPushButton#SecondaryButton:hover {
    background: #F5F4F0;
    border-color: #C8C6C2;
}

/* 暗色主按钮 */
QPushButton {
    background: #C96A58;
}
QPushButton:hover { background: #D97A68; }
QPushButton:pressed { background: #B85A48; }
QPushButton:disabled { background: #3A3A3D; color: #6E6E72; }
```

### 6.4 工具栏按钮 (QToolButton)

```css
/* 纯图标 32x32，无边框无背景，hover 显示悬浮底色 */
QToolButton {
    border: none;
    border-radius: 4px;
    padding: 4px;
    icon-size: 24px;
    min-width: 32px;
    min-height: 32px;
}
QToolButton:hover { background: #F5F4F0; }
QToolButton:pressed { background: #E4E2DD; }
QToolButton:disabled { opacity: 0.4; }

/* 暗色 */
QToolButton:hover { background: #323234; }
QToolButton:pressed { background: #3A3A3D; }
```

### 6.5 标签页 (QTabWidget / QTabBar)

```css
/* 亮色 — 右侧面板标签栏 */
QTabBar::tab {
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
    color: #6B6863;
    border: none;
    border-bottom: 2px solid transparent;
    min-height: 36px;
}
QTabBar::tab:selected {
    color: #2D2A26;
    border-bottom: 2px solid #B85C4A;
}
QTabBar::tab:hover:!selected {
    color: #2D2A26;
    background: #F5F4F0;
}

/* 暗色 */
QTabBar::tab { color: #9A9A9E; }
QTabBar::tab:selected { color: #E4E4E7; border-bottom-color: #C96A58; }
QTabBar::tab:hover:!selected { color: #E4E4E7; background: #323234; }
```

### 6.6 列表/树 (QListWidget, QTreeWidget)

```css
/* 亮色 */
QListWidget, QTreeWidget {
    background: transparent;   /* 继承父容器背景 */
    border: none;
    outline: none;
    font-size: 13px;
    color: #2D2A26;
}

QListWidget::item, QTreeWidget::item {
    padding: 8px 12px;
    border-radius: 4px;
}
QListWidget::item:selected, QTreeWidget::item:selected {
    background: #F3E4E0;
    color: #2D2A26;
}
QListWidget::item:hover:!selected, QTreeWidget::item:hover:!selected {
    background: #F5F4F0;
}

/* 暗色 */
QListWidget::item:selected, QTreeWidget::item:selected {
    background: #3A2828;
}
QListWidget::item:hover:!selected, QTreeWidget::item:hover:!selected {
    background: #323234;
}
```

### 6.7 滚动条 (QScrollBar)

```css
/* 亮色 — 极窄滚动条，仅 hover 显示 */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #D0CEC8;
    border-radius: 4px;
    min-height: 40px;
}
QScrollBar::handle:vertical:hover {
    background: #B8B4AE;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;    /* 隐藏箭头按钮 */
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

/* 暗色 */
QScrollBar::handle:vertical { background: #4A4A4D; }
QScrollBar::handle:vertical:hover { background: #5E5E62; }
```

### 6.8 分割器 (QSplitter)

```css
/* 亮色 — 几乎不可见，hover 时显示强调色条 */
QSplitter::handle {
    background: transparent;
    width: 4px;
}
QSplitter::handle:hover {
    background: #B85C4A;
}

/* 暗色 */
QSplitter::handle:hover { background: #C96A58; }
```

### 6.9 状态栏 (QStatusBar)

```css
/* 亮色 */
QStatusBar {
    background: #F5F4F0;
    border-top: 1px solid #E4E2DD;
    font-size: 12px;
    color: #6B6863;
    min-height: 28px;
    padding: 0 12px;
}
QStatusBar QLabel {
    padding: 0 8px;
    border: none;
}

/* 暗色 */
QStatusBar {
    background: #28282A;
    border-top: 1px solid #3A3A3D;
    color: #9A9A9E;
}
```

### 6.10 工具提示 (QToolTip)

```css
/* 亮色 */
QToolTip {
    background: #2D2A26;
    color: #FFFFFF;
    border: none;
    border-radius: 4px;
    padding: 8px 12px;
    font-size: 12px;
    font-family: "Inter", "Noto Sans CJK SC", sans-serif;
}

/* 暗色 */
QToolTip {
    background: #E4E4E7;
    color: #1E1E20;
}
```

---

## 7. 阴影系统

极克制，仅用于区分浮动层与背景：

| 层级 | 亮色 | 暗色 |
|:---|:---|:---|
| 卡片/面板 | `0 1px 2px rgba(0,0,0,0.04)` | `0 1px 2px rgba(0,0,0,0.2)` |
| 下拉菜单/弹出 | `0 4px 12px rgba(0,0,0,0.08)` | `0 4px 12px rgba(0,0,0,0.3)` |
| 模态对话框 | `0 8px 24px rgba(0,0,0,0.12)` | `0 8px 24px rgba(0,0,0,0.4)` |
| Toast 通知 | `0 2px 8px rgba(0,0,0,0.1)` | `0 2px 8px rgba(0,0,0,0.25)` |

> Qt QSS 不支持 `box-shadow`，阴影效果通过 `QGraphicsDropShadowEffect` 实现：
> ```python
> shadow = QGraphicsDropShadowEffect()
> shadow.setBlurRadius(12)
> shadow.setOffset(0, 4)
> shadow.setColor(QColor(0, 0, 0, 20))  # 亮色
> widget.setGraphicsEffect(shadow)
> ```

---

## 8. 图标规范

| 属性 | 规格 |
|:---|:---|
| 格式 | `.svg`，纯矢量，不内嵌 PNG |
| 默认尺寸 | 24×24px（工具栏 32×32px 画板内 24px 内容区域） |
| 颜色 | 单色，使用 `currentColor` 继承父级文本色 |
| 主题适配 | 无需双套 SVG。单色图标 + QSS `color` 自动适配亮/暗 |
| 禁用态 | 不需要独立的禁用 SVG。QSS 控制 opacity |
| 命名约定 | `icon_action-description.svg`（如 `icon_write-chapter.svg`） |
| 存放位置 | `opennovel_desktop/resources/icons/`，按功能分组子目录 |

> **不需要为每个图标单独设计双套 SVG。** 单色 SVG + `currentColor` + QSS 主题色 = 一套资源适配双主题。这是商业级效率。

---

## 9. 编辑器语法高亮配色

NovelEditor 的 QSyntaxHighlighter 颜色值，与主题联动：

| Token 类型 | 亮色 | 暗色 |
|:---|:---|:---|
| 标题 `#` H1/H2 | `#B85C4A` (朱红) | `#C96A58` |
| 标题 `###` H3+ | `#6B6863` (中灰) | `#9A9A9E` |
| 加粗 `**text**` | `#2D2A26` + Bold (同正文) | `#E4E4E7` + Bold |
| 斜体 `*text*` | `#6B6863` + Italic | `#9A9A9E` + Italic |
| 对话引用 `> ` | `#5A7C9A` (暗蓝) | `#6A8CAC` |
| 分割线 `---` | `#D0CEC8` | `#4A4A4D` |
| YAML Frontmatter `---...---` | `#A8A49E` (浅灰) | `#6E6E72` |
| 行内代码 `` `code` `` | `#5A7C9A` + JetBrains Mono | `#6A8CAC` + JetBrains Mono |
| 链接 `[text](url)` | `#5A7C9A` + underline | `#6A8CAC` + underline |

---

## 10. 组件状态视觉矩阵

| 组件 | 默认 | Hover | Active/Pressed | Selected | Disabled | Focus |
|:---|:---|:---|:---|:---|:---|:---|
| 主按钮 | 朱红底白字 | 深朱红 | 最深朱红 | — | 灰底浅灰字 | 外发光环 |
| 次按钮 | 透明+灰边框 | 浅灰底 | 灰底 | — | 灰字+灰边 | 强调色边框 |
| 列表项 | 透明 | 极浅灰 | — | 朱红淡底 | 灰字 | 虚线框 |
| 标签页 | 灰字+透明底 | 灰字+浅灰底 | — | 深字+朱红底边 | 浅灰字 | — |
| 输入框 | 白底+灰边框 | 深灰边框 | — | — | 灰底+浅灰字 | 强调色边框 |
| 复选框 | 灰框 | 深灰框 | — | 朱红勾 | 浅灰框 | 强调色框 |
| 分割器 | 透明 | 朱红条 | — | — | — | — |
| API 灯 | ●灰 | — | — | ●绿/●红/●灰 | — | — |

---

## 11. 布局红线 (开发约束)

| 规则 | 说明 |
|:---|:---|
| 工具栏不可拖拽 | `QToolBar.movable = False` |
| 分割器不影响编辑器最小宽度 | `QSplitter.setStretchFactor(1, 1)` 确保编辑器伸缩优先级最高 |
| 左侧导航宽度最小 200px | `QSplitter.setMinimumSize(200, ...)` |
| 右侧面板宽度 280-480px | `QSplitter.setMinimumWidth(280)` + `setMaximumWidth(480)` |
| 专注模式 F11 | 隐藏所有外围面板 + 菜单栏 + 工具栏 + 状态栏，仅留编辑器 |
| 编辑器亮/暗分别使用 16/15px | 暗色主题视觉膨胀导致的字号微调 |
| SVG 图标全部 QRC 编译 | 使用 `pyrcc5` 或 `rcc` 编译为二进制资源，避免文件路径依赖 |
| 不要为 macOS/Linux 调整 | 当前仅 Windows 目标，跨平台属于后续考虑 |

---

## 12. QRC 资源结构

```
opennovel_desktop/
└── resources/
    ├── resources.qrc          # Qt Resource 索引文件
    ├── icons/
    │   ├── write-chapter.svg
    │   ├── auto.svg
    │   ├── stop.svg
    │   ├── commit.svg
    │   ├── stash.svg
    │   ├── save.svg
    │   ├── search.svg
    │   ├── settings.svg
    │   └── ... (约 20-30 SVG)
    └── themes/
        ├── base.qss           # 布局样式（与主题无关）
        ├── light.qss          # 亮色变量
        └── dark.qss           # 暗色变量
```

> `resources.qrc` 使用 Qt 标准格式，通过 `rcc` 编译为 `resources_rc.py`，在 `__init__.py` 中 `import resources_rc` 即可全局访问。QSS 文件中用 `url() icon` 引用资源。

---

## 13. 实现优先级 (建筑学顺序)

```
Phase 1 — 骨架（先让界面可见）
  □ 包结构搭建 + QRC 资源系统
  □ MainWindow 三栏布局 (QSplitter)
  □ AppState 单例 + Signal 骨架
  □ 亮/暗主题 QSS 基础变量加载
  □ 主工具栏 (6 SVG 图标, 占位)

Phase 2 — 编辑器（核心创作区）
  □ NovelEditor (QPlainTextEdit + 语法高亮)
  □ 标签页体系 (QTabWidget 管理多文件)
  □ 光标位置 → 状态栏联动
  □ 右键菜单 + "发送给 Agent" 骨架

Phase 3 — 面板（功能填充）
  □ 左侧导航三面板 (文件树/角色/大纲)
  □ 右侧标签栏 (Critic/流水线/Diff)
  □ AgentWorker + Pipeline View 实时更新
  □ 状态栏三区填充

Phase 4 — 工作流（让功能跑通）
  □ 写章节流水线 (QThread + AgentWorker)
  □ Commit 流程 (Diff 面板内勾选)
  □ Auto 全自动创作
  □ 内联 Diff + 接受/拒绝

Phase 5 — 打磨（商业级体验）
  □ 崩溃恢复 (60s 自动备份)
  □ 会话持久化 (QSettings + session.json)
  □ 初始设置向导 (QWizard)
  □ 搜索面板 (Ctrl+Shift+F)
  □ 专注模式 F11
  □ 错误 Toast + Pipeline 红色标记
```
