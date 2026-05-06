---
name: video-analyze
description: This skill should be used when the user asks to "analyze video", "extract shots from video", "分析视频分镜", "视频镜头分析", "提取视频镜头", "reverse storyboard", "视频拉片", or mentions identifying cinematography techniques from a video file.
argument-hint: <video-file-path> [--threshold 0.3] [--max-frames 30]
allowed-tools: [Read, Glob, Grep, Bash, Write]
---

# 视频分镜分析器

分两步执行：**Step A 精确检测镜头边界** → **Step B 逐镜头详细分析**。

## 输入处理

用户输入为 `$ARGUMENTS`。

1. 解析参数：提取视频文件路径、可选的阈值 `--threshold`（默认 0.3）
2. 如果没有输入视频路径，使用 AskUserQuestion 请求
3. 确认视频文件存在

## 前置检查

```bash
which ffmpeg && which ffprobe && echo "READY" || echo "需要安装 ffmpeg: brew install ffmpeg"
```

---

# ═══════════════════════════════════════
# Step A：精确检测镜头边界（最重要）
# ═══════════════════════════════════════

**这一步的唯一目标：找到所有镜头切换的精确时间点。不做任何其他分析。**

## A1：获取视频基本信息

```bash
ffprobe -v quiet -print_format json -show_format -show_streams "<video_path>"
```

记录：分辨率、帧率、总时长、编码。

## A2：Scene Detection — 两轮扫描

**第一轮（threshold=0.03）**：
```bash
ffmpeg -i "<video_path>" -vf "select='gt(scene,0.03)',showinfo" -vsync vfr -f null - 2>&1 | grep "pts_time" | sed 's/.*pts_time:\([0-9.]*\).*/\1/'
```

**第二轮（threshold=0.01）**：
```bash
ffmpeg -i "<video_path>" -vf "select='gt(scene,0.01)',showinfo" -vsync vfr -f null - 2>&1 | grep "pts_time" | sed 's/.*pts_time:\([0-9.]*\).*/\1/'
```

将时间戳按 0.5s 窗口聚类，连续密集的时间戳标记为甩镜区间。

## A3：4fps 高精度抽帧

```bash
mkdir -p "/tmp/video_analyze_<timestamp>"
ffmpeg -v quiet -y -i "<video_path>" -vf "fps=4" -q:v 2 "/tmp/video_analyze_<timestamp>/f_%04d.jpg"
```

帧编号与时间换算：**帧 f_NNNN 对应时间 = (NNNN - 1) × 0.25 秒**。

## A4：视觉验证 — 逐切点确认

**这是最关键的步骤。** 对每个候选切点，用 Read 工具查看前后帧，判断是否为真切点。

### 核心判断标准："什么是一个镜头"

**一个镜头 = 一次连续的摄影机拍摄（one continuous take）。**

| 维度 | 同一镜头内允许的变化 | 表示不同镜头的变化 |
|------|---------------------|-------------------|
| 机位 | 不变（除非手持微晃） | 机位明显跳变 |
| 拍摄角度 | 连续渐变 | 角度突然改变 |
| 构图框架 | 因推拉摇移而连续变化 | 构图不连续地跳变 |
| 对焦平面 | 可以跟焦或拉焦 | 对焦点突然跳到完全不同的主体 |
| 光影明暗 | **可以渐变** | — |
| 前景遮挡 | **可以有物体穿过前景** | — |

### 必须避免的误判

**误判1：光影变化 ≠ 切换**
构图、角度、主体完全一致，仅亮度/色调渐变 → 同一镜头。

**误判2：前景遮挡 ≠ 两个镜头**
物体划过暂时遮挡画面，遮挡后如果机位/角度变了，遮挡是新镜头入场方式。

**误判3：题材相似 ≠ 同一镜头**
两段画面拍同类题材但构图、视角、背景不同 → 不同镜头。

**误判4：甩镜是独立镜头**
连续 ≥0.5s 运动模糊且前后画面不同构图 → 甩镜是独立镜头。

**误判5：scene detection 高分 ≠ 一定是切点**
同一镜头内的剧烈运动（水花、快速甩头）会触发高分但不是切换。必须视觉确认。

### 镜头分界判断流程

对每对相邻帧：
1. 主体是否改变？→ 新镜头
2. 机位/角度是否连续？→ 不连续则新镜头
3. 构图是否连续？→ 不连续则新镜头
4. 背景参照物是否一致？→ 不一致则新镜头
5. 以上都没变，仅光影变化？→ 同一镜头

### 转场类型识别

确认切点后，判断转场类型：

| 转场 | 视觉特征 | 剪映转场名 |
|------|---------|-----------|
| 硬切 | 瞬间跳变 | 无转场 |
| 叠化 | 两画面半透明重叠 | 叠化 |
| 渐隐到黑 | 逐帧变暗到纯黑 | 闪黑 |
| 从黑渐显 | 从纯黑逐帧变亮 | 闪黑(反) |
| 闪白 | 中间帧纯白 | 闪白 |
| 模糊 | 切点附近整体失焦 | 模糊 |
| 甩镜 | 连续运动模糊 | 横移模糊 |

输出格式：`转场大类(剪映转场名, 约Ns)`

## A5：输出镜头边界表

**Step A 的唯一输出**——一个精确的镜头边界列表：

```
镜头边界确认：
#1: 0.00s - 4.03s (4.0s) 转场:直接开始
#2: 4.03s - 7.77s (3.7s) 转场:硬切
#3: 7.77s - 11.47s (3.7s) 转场:硬切
...
总计 N 个镜头
```

**到这里停下来确认**：镜头数量和边界是否合理，再进入 Step B。

---

# ═══════════════════════════════════════
# Step B：逐镜头详细分析
# ═══════════════════════════════════════

**在 Step A 确认的镜头边界基础上**，对每个镜头选取中间时刻的关键帧，进行详细分析。

## B1：逐镜头视觉分析

对每个镜头用 Read 查看中间帧，分析以下维度：

**构图/景别**：特写/近景/中景/全景/远景/大全景
**角度/运镜**：平视/仰视/俯视 + 静态/手持/横摇/慢推
**光影/氛围**：自然光/电影布光 + 冷调/暖调
**焦段/景深**：广角/标准/长焦 + 浅景深/深景深
**变速**：正常/慢放Nx/快进Nx/Speed Ramp（只写播放速度，不写转场）
**素材类型**：视频（>1s有运动）/ 图片/视频均可（<0.8s静态）/ LOGO
**画面文字**：有则记录内容+位置+颜色+大小，无则写"无"
**音频**：BGM/环境音/人声对话/静音
**人物**：无/有·未说话/有·说话·嘴部清晰
**情绪/氛围**：核心情绪关键词
**画面内容**：主要元素描述

## B2：输出分镜总表

```
| # | 时间范围 | 时长 | 素材类型 | 构图/景别 | 角度/运镜 | 光影 | 变速 | 画面文字 | 音频 | 内容概述 |
```

## B3：输出逐镜头详细分析

每个镜头一段详细描述，包含上述所有维度。

## B4：文字信息汇总（TEXT_CONFIG JSON）

在分析末尾输出标准化 JSON：

```
<!-- TEXT_CONFIG_START -->
{
  "brand_texts": [...],
  "logo": {...}
}
<!-- TEXT_CONFIG_END -->
```

字段：text, shot_numbers, font_size_percent, color_hex, position_y, animation, animation_duration

## B5：整体风格总结

- 构图偏好、景别节奏、色调基调、剪辑节奏
- 场景结构
- 值得学习的技巧
- **配乐风格建议**：基于视频的节奏和氛围，给出适合的音乐风格描述：
  - BPM 范围（如 90-110）
  - 音乐风格（如 electronic, ambient, cinematic, lo-fi）
  - 情绪关键词（如 upbeat, calm, energetic, melancholic）
  - 英文搜索关键词（用于在 Freesound 等素材网站搜索，如 "upbeat electronic corporate"）
