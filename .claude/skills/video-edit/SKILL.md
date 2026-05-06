# 视频自动剪辑器

根据 video-analyze skill 产生的分镜分析模板，将新素材自动剪辑为风格相似的视频。核心特性：音频卡点（镜头切换对齐节拍）、智能转场选择、多维素材匹配。

## 输入处理

用户输入为 ``。

1. 解析参数：
   - 第一参数：分析 markdown 文件路径（由 video-analyze 产生）
   - 第二参数：素材目录路径（包含视频和/或图片文件）
   - 可选 `--output <path>`：输出路径，默认 `./edited_output.mp4`
   - 可选 `--audio <mode>`：`template`（用模板音频，默认）/ `keep`（保留素材音频）/ `silent`（静音）
   - 可选 `--bgm <path>`：指定背景音乐文件
2. 如果参数不完整，使用 AskUserQuestion 请求缺失信息
3. 确认文件存在

## 前置检查

```bash
which ffmpeg && which ffprobe && python3 -c "import librosa" 2>/dev/null && echo "READY" || echo "需要安装依赖"
```

如果 ffmpeg 或 librosa 未安装，提示用户安装。

---

## Step 1: 解析模板分析

读取分析 markdown 文件，提取以下结构化数据：

### 1.1 视频基本信息

从 markdown 的信息表中提取：
- 分辨率 → 输出视频的目标分辨率
- 帧率 → 输出视频的目标帧率
- 总时长 → 参考时长
- 检测镜头数

### 1.2 分镜总表

解析 markdown 表格，每行提取：
- 镜头编号 (#)
- 时间范围（精确到毫秒）
- 时长
- 构图/景别
- 角度/运镜
- 光影/氛围
- 焦段/景深
- **变速**（正常 / 慢放Nx / 快进Nx / Speed Ramp(曲线) / 定格 / 倒放 / 延时）
- **画面文字**（"文字内容" 位置+颜色 / 无）— 用于判断素材是否需要补文字
- 画面内容概述
- 画面语言描述

### 1.3 逐镜头详细信息

从 `### 镜头 #N` 部分补充提取：
- **与上一镜头的关系**（转场类型）：硬切/溶解/渐隐到黑/从黑渐显/叠化+渐隐/闪黑/闪白/甩镜转场/跳切/遮挡转场
- **情绪/氛围**
- **镜头功能**：制造冲突/展现人物/叙述故事/交代环境/渲染情绪
- **画面文字元素详情**：文字内容、位置、大小、颜色、样式、背景、出现方式、出现时机（这是总表"画面文字"列的完整展开）

### 1.4 镜头分类

将每个镜头标记类型：
- **内容镜头**：有明确主体和内容的镜头（大多数镜头）
- **内容+文字叠加镜头**：画面有实际内容，同时叠加了文字/品牌名（常见于第一个镜头）
- **过渡镜头**：甩镜、运动模糊（构图/运镜字段包含"甩镜"、"运动模糊"、"Whip Pan"）
- **标题画面**：LOGO、文字画面（内容包含"LOGO"、"标题"、"字幕"、"文字"）

### 1.5 文字元素表（从分析 markdown 提取）

遍历分析 markdown 中**总表的"画面文字"列** + **逐镜头的"画面文字元素"字段**，汇总建立文字元素表：

```
镜头 #1: "品牌名 XXX" — 正中偏上，白色无衬线粗体，约12%高度，透明叠加，淡入0.5s → 需要叠加
镜头 #2: 无 → 不需要处理
镜头 #3: 无 → 不需要处理
镜头 #6: "LOGO" — 正中央，白色手写体，约15%高度，纯黑背景，叠化浮现 → 需要叠加或生成
```

**数据来源优先级**：
1. 逐镜头详细分析的"画面文字元素"字段（最详细，有位置/大小/颜色/样式/出现方式）
2. 总表的"画面文字"列（摘要，用于快速判断哪些镜头有文字）
3. 如果分析 markdown 中没有文字相关字段（旧版 skill 产生的分析），则跳过文字叠加

**后续使用**：
- Step 3 素材分析时，通过 Read 查看素材关键帧，记录素材中已有的文字
- Step 4 匹配时，对比"模板要求的文字" vs "素材已有的文字"，标记哪些需要补充
- Step 6 渲染时，对标记了"需要叠加"的镜头执行文字叠加

**所有镜头一视同仁**——无论首尾还是中间，只要模板有文字而素材没有，就叠加。
**LOGO/结尾画面也优先用素材**——如果素材中有合适的品牌素材，优先使用。Pillow 生成或原视频截取都是 fallback。

---

## Step 2: 音频节拍检测与卡点时间表

这是**最关键的步骤**。镜头切换必须精准对齐音乐节拍。

### 2.1 确定音频来源

按优先级：
1. `--bgm` 指定的背景音乐
2. `--audio template` 模式下，查找模板对应的原视频提取音频
3. 如果都没有（`--audio silent`），跳过卡点，使用模板原始时间

### 2.2 节拍检测

运行节拍检测脚本（脚本位于与分析 markdown 同级目录或 `services/` 目录下）：

```bash
python3 services/beat_detector.py "<audio_path>"
```

> **重要**：`beat_detector.py` 的路径相对于项目根目录。如果在其他目录执行，需要用绝对路径。先用 `find` 或 `ls` 确认脚本位置。

解析输出 JSON，获取：
- `bpm`: 节拍速度
- `beats`: 所有节拍时间点
- `strong_peaks`: 能量峰值（最佳切点）
- `downbeats`: 强拍（每4拍的第1拍）

### 2.3 生成卡点时间表

将模板镜头边界对齐到节拍网格：

**算法**：
```
对模板中的每个镜头边界时间 T：
  1. 在 beats + strong_peaks 中找最近的时间点 B
  2. 如果 |T - B| ≤ 0.15s：将边界对齐到 B
  3. 如果 |T - B| > 0.15s：保留原始时间 T
  4. 确保对齐后相邻镜头不重叠
```

**优先级**：strong_peaks > downbeats > 普通 beats

输出卡点时间表（内部数据结构）：
```
镜头1: 0.000s - 0.488s (对齐到 beat #1)
镜头2: 0.488s - 2.508s (对齐到 downbeat)
镜头3: 2.508s - 3.413s (对齐到 peak)
...
```

### 2.4 节奏分析

根据 BPM 和 beat intervals 判断段落节奏：
- **快节奏段落**：beat interval < 0.4s 或连续 strong_peaks → 适合硬切或极短转场
- **中速段落**：beat interval 0.4s-0.8s → 适合标准转场
- **慢节奏段落**：beat interval > 0.8s → 适合长转场（溶解/fade）
- **高能段落**：strong_peaks 密集 → 适合闪白/zoomin/hblur

---

## Step 3: 素材分析

### 3.1 收集素材文件

```bash
ls "<materials_dir>"/*.{mp4,mov,avi,mkv,webm,jpg,jpeg,png,bmp,webp} 2>/dev/null
```

### 3.2 素材元数据

对每个素材：
```bash
ffprobe -v quiet -print_format json -show_format -show_streams "<material_path>"
```

记录：文件名、类型（视频/图片）、时长、分辨率、是否有音频。

### 3.3 素材关键帧提取

对每个**视频**素材，在 25%、50%、75% 处各抽一帧：
```bash
DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "<material>")
T25=$(echo "$DURATION * 0.25" | bc -l)
T50=$(echo "$DURATION * 0.50" | bc -l)
T75=$(echo "$DURATION * 0.75" | bc -l)
mkdir -p /tmp/video_edit_<ts>/mat_frames
ffmpeg -v quiet -y -ss $T25 -i "<material>" -frames:v 1 -q:v 2 /tmp/video_edit_<ts>/mat_frames/mat_<idx>_a.jpg
ffmpeg -v quiet -y -ss $T50 -i "<material>" -frames:v 1 -q:v 2 /tmp/video_edit_<ts>/mat_frames/mat_<idx>_b.jpg
ffmpeg -v quiet -y -ss $T75 -i "<material>" -frames:v 1 -q:v 2 /tmp/video_edit_<ts>/mat_frames/mat_<idx>_c.jpg
```

对**图片**素材，直接使用原文件。

### 3.4 视觉分析

用 Read 工具查看每个素材的关键帧（图片直接查看），重点分析**剪辑风格属性**：

- **景别**（最重要）：大全景 / 全景 / 中景 / 近景 / 特写 / 大特写
- **运镜方式**：静态 / 慢推 / 慢拉 / 横摇 / 手持跟随 / 手持微晃
- **色调/氛围**：冷色调 / 暖色调 / 高调（明亮）/ 低调（暗沉）/ 中性
- **节奏感**：安静/缓慢 / 中等 / 动感/快速
- **素材类型标签**：
  - `全景·静态` / `全景·横摇` / `中景·手持` / `特写·慢推` 等（景别+运镜组合）
  - `品牌LOGO` — 品牌标志图片（黑底/白底+LOGO图形+品牌名，适合做结尾定版）
  - `文字素材` — 纯文字图片（用于叠加到视频上，不能单独作为镜头画面使用）
  - `产品图` — 产品展示图片（可作为独立镜头）
- **画面文字**：素材中已有的文字（精确记录）
- **宽高比**（图片素材必须记录！）：
  - 记录实际像素尺寸（如 1280×1706 竖版、6910×726 超宽横版）
  - 标注是否适合当前画布（如竖屏 9:16 画布，超宽横图不适合做全屏镜头）
  - **超宽横图**（宽高比 > 3:1）通常是文字条/banner，只能用于文字叠加，不能做独立镜头
- **适用场景**：
  - 竖版产品图 → 可用于任何镜头
  - 竖版/方形 LOGO 图 → 适合做结尾定版
  - 横版文字条 → 只能用于文字叠加素材，不能做独立镜头画面
- **内容简述**：画面中是什么（简要描述）
- **稳定性评估**（关键！）：
  - 对比 3 个关键帧（25%/50%/75%），判断素材整体稳定性
  - 标记稳定性等级：稳定 / 轻微抖动 / 明显抖动
  - 识别**最稳定的片段区间**：如"0-3s 稳定，3-7s 有摇晃，7-10s 稳定"
  - 这个信息在 Step 6.3 防抖策略中使用——优先从稳定区间截取
- **最佳片段**：综合内容和稳定性，推荐最适合使用的片段区间

---

## Step 4: 智能匹配

### 4.1 LOGO/品牌镜头匹配（优先用素材）

对模板中标记为"标题画面"或包含 LOGO 的镜头（通常是最后一个镜头），按以下优先级匹配：

**优先级 1：素材中有品牌 LOGO 图片/视频**
- 在 Step 3.4 中标记为 `品牌LOGO` / `品牌素材` / `文字卡片` 的素材
- 直接使用该素材：
  - 图片 → Ken Burns 效果（或静态）转为视频片段
  - 视频 → 截取对应时长
- 如果素材 LOGO 和模板 LOGO 匹配（文字内容一致），不需要额外叠加

**优先级 2：从原视频截取**
- 如果素材中没有 LOGO 素材，从原视频截取该镜头
- ```bash
  ORIG_VIDEO=$(ls uploads/<template_job_id>_*.mp4 2>/dev/null | head -1)
  ffmpeg -y -ss <shot_start> -t <shot_duration> -i "$ORIG_VIDEO" ...
  ```

**优先级 3：Pillow 生成**
- 如果原视频也找不到，用 Pillow 按分析中的文字信息生成（见 Step 6.6）

### 4.1.2 文字叠加镜头（内容画面上有品牌文字）

对模板中有"画面文字"但同时有实际画面内容的镜头（如第一个镜头），处理方式：
1. 正常匹配内容素材（走 4.3 匹配流程）
2. 从分析中读取文字信息（内容、位置、颜色、大小）
3. 用 Pillow 生成文字透明 PNG，overlay 到素材画面上（见 Step 6.6）

### 4.2 素材去重与分组

素材中经常有同一场景的多次拍摄（不同 take），需要先去重分组再择优。

**4.2.1 分组**：根据 Step 3.4 的素材类型标签和内容，分为两大类：

**A. 品牌素材组**（标签为 `品牌LOGO` / `品牌素材` / `文字卡片`）：
- 单独归组，**不参与**常规内容匹配
- 专门供 LOGO/文字镜头使用（Step 4.1）
- 例：logo.png → "品牌LOGO组"，slogan.jpg → "文字卡片组"

**B. 内容素材组**（按景别和运镜风格分组，不是按内容题材）：
- 按**景别**分组：全景/远景素材组、中景素材组、近景/特写素材组
- 同景别内按**运镜**细分：静态、慢推、横摇、手持
- 例：3 个全景静态素材 → "全景·静态组"，2 个特写慢推素材 → "特写·慢推组"
- 同一场景的多次 take 归入同组

**4.2.2 组内择优**：每组中选出最佳素材，选择标准按优先级：
1. **稳定性最好**的（稳定 > 轻微抖动 > 明显抖动）
2. 稳定性相同时，选**构图最好**的（对焦清晰、曝光正确、构图完整）
3. 构图也相同时，选**时长最长**的（更多可用片段）

**4.2.3 备选保留**：每组的非最佳素材标记为"备选"

输出：
```
素材分组：
  [品牌] LOGO组(1个): ★logo.png (图片, 品牌LOGO, "RESTONE 枕石")
  [内容] 瀑布组(3个): ★1529(稳定,11s,视频) | 1530(轻微抖动,8s) | 1531(明显抖动,6s)
  [内容] 手链特写组(2个): ★1522(稳定,9s,视频) | 1523(轻微抖动,7s)
  [内容] 树干组(2个): ★1533(稳定,12s,视频) | 1534(稳定,10s)
  ...
```

### 4.3 风格匹配策略（核心）

**⚠️ 匹配的目标是复现"剪辑风格"，不是复现"画面内容"。**

素材和参考视频可能是完全不同的题材。重要的是让新素材按照参考视频的剪辑手法排列——相同的节奏、景别变化、运镜方式、情绪曲线。

对每个模板镜头，从**各组的最佳素材**中匹配，按以下**风格维度**评分：

1. **景别匹配**（权重 30%）：模板是全景→素材也选全景，模板是特写→素材也选特写
   - 全景/远景 → 选有环境纵深感的素材
   - 中景 → 选人物/产品中等距离的素材
   - 近景/特写 → 选细节、质感类素材
   - 大特写/极特写 → 选最能展示细节的素材

2. **运镜匹配**（权重 25%）：模板是横摇→素材也选有横向运动的，模板是静态→素材选最稳定的
   - 静态 → 选三脚架或最稳定的片段
   - 慢推/慢拉 → 选有缓慢推进感的素材
   - 横摇 → 选有横向运动或扫视的素材
   - 手持跟随 → 选手持但相对稳定的素材

3. **情绪/氛围匹配**（权重 20%）：模板是宁静冷调→素材选类似氛围的，模板是高能快节奏→素材选有动感的
   - 冷色调/宁静 → 选色温偏冷、画面安静的素材
   - 暖色调/温馨 → 选色温偏暖的素材
   - 高能/紧张 → 选有动作或视觉冲击的素材

4. **稳定性**（权重 15%）：优先选稳定素材
5. **时长兼容性**（权重 10%）：素材时长足够覆盖该镜头需要的时长

**⚠️ 内容相似度不作为匹配维度**——参考视频拍的是山水手链，你的素材可能是城市咖啡，完全没关系。

**中间出现的文字镜头**：走通用文字叠加流程（Step 4.3 + Step 6.6）

**过渡镜头**（甩镜/运动模糊）处理：
- 不需要匹配内容，从任意素材生成运动模糊效果
- 使用 ffmpeg boxblur + tblend：
  ```bash
  ffmpeg -y -ss <start> -t <duration> -i "<material>" \
    -vf "setpts=0.25*PTS,tblend=all_mode=average,boxblur=luma_radius=30:luma_power=2" \
    -c:v libx264 -preset fast -crf 18 -an /tmp/video_edit_<ts>/transition_<n>.mp4
  ```

### 4.2 文字叠加判断（通用逻辑）

对 Step 1.5 中提取的文字元素表中的**每个有文字的镜头**：

1. 检查匹配到的素材中**是否已经包含相同或相似的文字**
   - 在 Step 3 素材分析时，通过 Read 查看关键帧，记录素材中可见的文字
   - 如果素材已有相同品牌名/LOGO → 不叠加，直接使用素材
   - 如果素材有部分文字但缺少某些 → 只叠加缺失的部分
2. 如果素材**没有**模板要求的文字 → 标记为"需要叠加文字"，在 Step 6.6 中处理
3. 文字叠加是**后处理**步骤，在素材片段渲染完成后叠加（overlay）

### 4.4 匹配约束

- **避免视觉重复**：不同镜头尽量使用不同组的素材。如果两个模板镜头内容不同（如"全景"和"特写"），不应该用同一组素材
- **同组同素材复用**：同一素材可用于多个镜头，但必须使用**不同时间段**（不能两个镜头截取同一片段）
- **同组不同素材**：如果最佳素材的可用片段已用完，启用该组的备选素材
- 素材太短（< 镜头时长的 50%）时：
  - 图片：使用 Ken Burns 效果（zoompan 慢推/慢拉）
  - 视频：减速播放（setpts，最慢 0.5x）
- 素材时长充足时：选择内容最匹配的片段起点
- **变速镜头的素材需求**：
  - 慢放镜头需要更多素材时长（如 0.5x 慢放需要 2x 素材时长）
  - Speed Ramp 镜头：素材时长 ≥ 正常速度段时长 + 减速段源时长
  - 快进/延时镜头：素材时长可以更短
  - 倒放镜头：优先选择有明确运动方向的素材

### 4.3 输出匹配表

对每个镜头记录：
- 镜头编号
- 匹配的素材文件 + 片段起点
- 置信度（高/中/低）
- 速度处理（正常 / 慢放Nx / 快进Nx / Speed Ramp / 倒放 / 定格 / 延时）
- 需要的转场效果
- 匹配理由

---

## Step 5: 转场选择

### 5.1 从模板分析推断转场

读取每个镜头的"与上一镜头的关系"字段，映射到 ffmpeg xfade 转场：

| 模板描述关键词 | ffmpeg 实现 | 时长 |
|--------------|-----------|------|
| 硬切、快切、直切 | 不加转场 | 0 |
| 甩镜、Whip Pan | `xfade=transition=hblur`（或该镜头本身就是运动模糊） | 0.2-0.3s |
| 溶解、叠化、交叉溶解 | `xfade=transition=dissolve` | 0.5-0.8s |
| 淡出淡入、渐隐渐现 | `xfade=transition=fadeblack` | 0.3-0.5s |
| 闪黑 | `xfade=transition=fadeblack` | 0.2-0.3s |
| 闪白 | `xfade=transition=fadewhite` | 0.2-0.3s |
| 跳切 | 不加转场 | 0 |
| **渐隐到黑**（片尾常见） | 不用 xfade，在最后一个片段上加 `fade=t=out:st=<end-d>:d=<d>` 滤镜 | 0.5-1.5s |
| **从黑渐显**（片头常见） | 在第一个片段上加 `fade=t=in:st=0:d=<d>` 滤镜 | 0.5-1.5s |
| **叠化+渐隐**（Logo从前一画面叠化浮现后整体渐隐到黑） | 复合效果，见下方说明 | 1.0-3.0s |

**复合转场：叠化+渐隐（常见于品牌结尾画面）**

这种效果是：前一镜头画面上 Logo/文字以叠化方式浮现，然后整体渐隐到纯色背景。实现方式：

1. 用 Pillow 生成 Logo/文字的 PNG 图片（透明背景）
2. 将 Logo 图片叠加到前一镜头的尾部（overlay + fade in）
3. 最后加 fade out 到黑/纯色背景

```bash
# Step 1: 生成带透明背景的 Logo PNG（用 Pillow，RGBA 模式）
python3 -c "
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGBA', (<W>, <H>), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
font = ImageFont.truetype('/System/Library/Fonts/PingFang.ttc', <size>)
bbox = draw.textbbox((0,0), '<logo_text>', font=font)
tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
draw.text(((<W>-tw)//2, (<H>-th)//2), '<logo_text>', fill=(255,255,255,255), font=font)
img.save('$WORKDIR/logo_overlay.png')
"

# Step 2: 前一镜头尾部叠加 Logo（渐显）+ 整体渐隐到黑
# 先延长前一镜头或用其最后一帧 loop，然后 overlay logo with fade
ffmpeg -y -i "$WORKDIR/segments/seg_prev.mp4" \
  -loop 1 -i "$WORKDIR/logo_overlay.png" \
  -filter_complex "
    [0:v]split[main][bg];
    [1:v]format=rgba,fade=t=in:st=0:d=1:alpha=1[logo];
    [main][logo]overlay=0:0:shortest=1,
    fade=t=out:st=<总时长-1.0>:d=1.0[outv]
  " -map "[outv]" -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

如果 overlay 复杂度过高，**简化方案**：
- 用 xfade=transition=dissolve 过渡到 Pillow 生成的带文字静态画面
- 在静态画面末尾加 fade=t=out

### 5.2 根据节奏调整转场

结合 Step 2 的节奏分析：

- **快节奏段落**（BPM > 140 或 beat interval < 0.4s）：
  - 仅使用硬切或极短转场（≤ 0.1s）
  - 推荐：无转场、`fadefast`(0.1s)

- **中速段落**（BPM 90-140）：
  - 标准转场效果
  - 推荐：`fade`(0.3s)、`dissolve`(0.3s)、`slideleft`(0.2s)、`wipeleft`(0.2s)

- **慢节奏段落**（BPM < 90 或 beat interval > 0.8s）：
  - 可以使用较长转场
  - 推荐：`fade`(0.5-1.0s)、`dissolve`(0.5s)、`fadeslow`(0.8s)、`circleopen`(0.5s)

- **高能瞬间**（在 strong_peak 处切换）：
  - 推荐：`fadewhite`(0.15s)、`zoomin`(0.2s)、`hblur`(0.15s)

### 5.3 转场多样性

避免连续使用相同转场。如果连续 3 个以上镜头使用同类转场，从同类别中选择变体：
- 例：连续 fade → 改用 dissolve → 改用 fadefast

### 5.4 常用场景-转场组合

| 场景变化 | 推荐转场 |
|---------|---------|
| 同一场景内切换（不同角度） | 硬切 |
| 跨场景切换 | `fade` / `dissolve` / `fadeblack` |
| 时间跳跃 | `fadeblack` / `fadegrays` |
| 高能→高能（快切蒙太奇） | 硬切 / `fadewhite`(极短) |
| 抒情段落 | `fadeslow` / `dissolve` |
| 开场 | `fadeblack`(从黑) / `circleopen` |
| 结尾 | `fadeblack`(到黑) / `circleclose` |
| 运动方向一致 | `slideleft`/`slideright`（跟随运动方向） |

---

## Step 5.5: 选择渲染模式

检测剪映 MCP API 是否可用：

```bash
curl -s -m 2 http://localhost:9001/create_draft -X POST -H "Content-Type: application/json" \
  -d '{"name":"test","width":720,"height":1280}' 2>/dev/null | grep -q '"success":true'
```

- **如果 API 可用** → 走 **Step 6A（剪映模式）**，生成剪映工程，效果最好
- **如果 API 不可用** → 走 **Step 6B（ffmpeg 模式）**，直接生成 MP4

⚠️ 优先使用剪映模式。剪映的转场、文字、变速效果远优于 ffmpeg。

---

## Step 6A: 剪映模式（推荐）

通过剪映 MCP (http://localhost:9001) 生成剪映工程。

### 6A.1 创建工程

```bash
DRAFT_RESP=$(curl -s http://localhost:9001/create_draft -X POST \
  -H "Content-Type: application/json" \
  -d '{"name":"auto_edit_<job_id>","width":<TARGET_W>,"height":<TARGET_H>}')
DRAFT_ID=$(echo "$DRAFT_RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['output']['draft_id'])")
echo "Draft ID: $DRAFT_ID"
```

### 6A.2 逐镜头添加素材

对每个匹配到的镜头，按卡点时间表的时间添加到时间线：

```bash
# 添加视频素材
curl -s http://localhost:9001/add_video -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "draft_id": "'$DRAFT_ID'",
    "video_url": "<素材绝对路径>",
    "start": <trim_start_ms>,
    "end": <trim_end_ms>,
    "width": <TARGET_W>,
    "height": <TARGET_H>
  }'

# 添加图片素材（LOGO 等）
curl -s http://localhost:9001/add_image -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "draft_id": "'$DRAFT_ID'",
    "image_url": "<图片绝对路径>",
    "duration": <duration_ms>,
    "width": <TARGET_W>,
    "height": <TARGET_H>
  }'
```

### 6A.3 添加文字/图片叠加层

对分析中有文字的镜头，有两种方式添加文字：

**方式 A：用设计好的文字图片叠加**（优先，效果更好）
如果素材中有现成的文字图片（如品牌文字 PNG/JPG），直接用图片叠加，保留原始字体、颜色、设计：

```bash
curl -s http://localhost:9001/add_image_overlay -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "draft_id": "'$DRAFT_ID'",
    "image_path": "<文字图片绝对路径>",
    "start": <start_s>,
    "duration": <duration_s>,
    "alpha": 0.8,
    "scale": 0.75,
    "position_x": 0,
    "position_y": 0
  }'
```

参数说明：
- `alpha`：透明度（0-1，1=不透明，0.8=略透明）
- `scale`：缩放比例（0.75=缩小到75%）
- `position_x/y`：位置偏移（0=居中）

**什么时候用图片叠加**：
- 素材中有设计好的文字图片（如 WechatIMG1736.jpg 的青绿色品牌文字）
- 需要保留原始字体、颜色、排版设计
- 文字图片通常是横版的，需要调整 scale 让它在画面中大小合适

**方式 B：用 add_text 生成文字**（fallback，素材中没有文字图片时）

对分析中有文字的镜头，生成文字：

```bash
curl -s http://localhost:9001/add_text -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "draft_id": "'$DRAFT_ID'",
    "text": "<品牌名/标题>",
    "start": <start_ms>,
    "duration": <duration_ms>,
    "font_size": <大小>,
    "font_color": "<颜色hex>",
    "transform_y": <y偏移>,
    "text_intro": "fade_in",
    "intro_duration": 500
  }'
```

### 6A.4 添加转场效果

在镜头之间添加剪映内置转场：

```bash
curl -s http://localhost:9001/add_effect -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "draft_id": "'$DRAFT_ID'",
    "effect_type": "transition",
    "effect_name": "<剪映转场名称>",
    "position": <镜头边界时间ms>,
    "duration": <转场时长ms>
  }'
```

**剪映转场名称映射**（从分析结果的转场描述直接取括号内的剪映名）：

分析结果格式：`叠化(叠化, 约0.8s)` → 直接使用括号内的 **"叠化"** 作为剪映转场名。

常用映射表（兜底，如果分析没给出剪映名）：

| 分析中的转场大类 | 默认剪映转场名 |
|---------------|-------------|
| 硬切 | 不添加 |
| 叠化/溶解 | 叠化 |
| 闪黑/渐隐到黑 | 闪黑 |
| 闪白/光闪 | 闪白 |
| 模糊转场 | 模糊 |
| 横向模糊 | 横向模糊 |
| 竖向模糊 | 竖向模糊 |
| 旋转模糊 | 旋转模糊 |
| 滑动/左推 | 左移 |
| 滑动/右推 | 右移 |
| 缩放 | 快速缩放 |
| 旋转 | 中心旋转 |
| 故障 | 故障 |
| 水墨 | 水墨 |
| 频闪 | 频闪 |

**⚠️ 优先使用分析结果中括号内的剪映转场名**，上表仅作兜底。剪映共有 453 种转场可用。

### 6A.5 后处理：防抖 / 人声分离 / 变速 / AI对口型

添加完所有素材后，逐个片段判断是否需要启用以下功能。

#### 6A.5.1 防抖

**何时启用**：
- 素材是手持拍摄（在 Step 3 分析中标记为"轻微抖动"或"明显抖动"的素材）→ **必须启用**
- 素材是三脚架/稳定器拍摄（标记为"稳定"）→ 不启用
- 图片素材 → 不启用
- **如果无法判断**，默认启用推荐防抖（宁可多防不要漏防）

```bash
# stable_level: 0=关闭, 1=剪裁最少, 2=推荐(默认), 3=最稳定
curl -s http://localhost:9001/set_stabilization -X POST \
  -H "Content-Type: application/json" \
  -d '{"draft_id": "'$DRAFT_ID'", "stable_level": 2}'
# segment_index 不传 = 应用到所有视频片段
# 传 segment_index = 只应用到指定片段
```

| 素材稳定性评估 | 防抖设置 |
|-------------|---------|
| 明显抖动 | `stable_level: 3`（最稳定） |
| 轻微抖动 | `stable_level: 2`（推荐） |
| 稳定 | `stable_level: 0`（关闭） |
| 无法判断 | `stable_level: 2`（推荐） |

#### 6A.5.2 人声分离

**何时启用**：
- 素材有音频（`has_audio=true`）且我们不需要素材原声（`volume=0`）→ 不需要分离，已经静音了
- 素材有音频且需要保留背景音乐但去除人声 → **启用，choice=1（保留背景音）**
- 需要只保留人声（如采访素材、旁白）→ **启用，choice=2（保留人声）**
- 模板分析中标注有背景音乐/环境音的镜头，素材也有类似音频 → 考虑保留背景音

```bash
# choice: 0=关闭, 1=保留背景音, 2=保留人声
curl -s http://localhost:9001/set_vocal_separation -X POST \
  -H "Content-Type: application/json" \
  -d '{"draft_id": "'$DRAFT_ID'", "segment_index": <N>, "choice": 1}'
```

| 场景 | 人声分离设置 |
|------|-----------|
| 素材已静音（volume=0）| 不需要 |
| 素材有环境音想保留、但有人说话 | `choice: 1`（保留背景音） |
| 素材有旁白/对话想保留 | `choice: 2`（保留人声） |
| 不确定 | 不启用（素材已静音时无需处理） |

#### 6A.5.3 变速

**何时启用**：
- 模板分析中"变速"字段不是"正常"→ **必须启用**，按模板指定的倍率
- 为了防抖而慢放（素材严重抖动，防抖不够用）→ 启用 0.9x-0.95x
- 素材时长不够覆盖镜头需要的时长 → 启用慢放拉伸

⚠️ 变速需要在 `add_video` 时通过 `speed` 参数设置（会自动调整 source/target 时间关系）。
如果是后期调整，用 `set_speed`：

```bash
curl -s http://localhost:9001/set_speed -X POST \
  -H "Content-Type: application/json" \
  -d '{"draft_id": "'$DRAFT_ID'", "segment_index": <N>, "speed": 0.5}'
```

| 模板变速字段 | 设置 |
|------------|-----|
| 正常 | speed=1.0（不设置） |
| 慢放0.5x | speed=0.5 |
| 快进2x | speed=2.0 |
| Speed Ramp | 需要曲线变速（目前用 mode=1） |
| 防抖辅助慢放 | speed=0.9~0.95 |

#### 6A.5.4 色彩校正

**何时启用**：
- 不同素材的色调/色温差异明显（如有的偏暖有的偏冷）→ 统一调整让视觉一致
- 模板视频有特定的色彩风格（如冷调、暖调、高对比）→ 素材需要调整到类似风格
- 素材曝光不足/过曝 → 调整亮度和对比度

```bash
curl -s http://localhost:9001/set_color_adjust -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "draft_id": "'$DRAFT_ID'",
    "segment_index": null,
    "brightness": 5,
    "contrast": 10,
    "saturation": -5,
    "temperature": 15,
    "tint": 0
  }'
```

参数说明（值范围 -50 到 50，0=不调整）：
- `brightness`：亮度（正=更亮，负=更暗）
- `contrast`：对比度（正=更高对比）
- `saturation`：饱和度（正=更鲜艳，负=更灰）
- `temperature`：色温（正=暖黄，负=冷蓝）
- `tint`：色调（正=偏绿，负=偏紫）
- `segment_index`：null=所有片段统一调，数字=只调指定片段

**色彩统一策略**：
- 分析模板视频的整体色调（冷/暖/中性），然后给所有素材统一加一个色温调整
- 如果个别素材色差明显，单独对那个片段加额外校正
- 在 EDIT_CONFIG 的 matches 里加 `color_adjust` 字段指定每个镜头的校正参数

#### 6A.5.5 AI对口型

**何时启用**：
- 视频中有人物说话，且需要替换音频（如配音/翻译）→ 启用
- 模板分析中标注有"人物对话"、"采访"、"主持人讲解"等内容 → 可能需要
- 素材是风景/产品/无人物的 → **不启用**

```bash
curl -s http://localhost:9001/set_ai_lip_sync -X POST \
  -H "Content-Type: application/json" \
  -d '{"draft_id": "'$DRAFT_ID'", "segment_index": <N>}'
```

⚠️ AI对口型需要剪映在线处理，设置后用户在剪映中打开会触发处理。

### 6A.6 添加音频

```bash
curl -s http://localhost:9001/add_audio -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "draft_id": "'$DRAFT_ID'",
    "audio_url": "<音频文件绝对路径>",
    "start": 0,
    "duration": <总时长ms>
  }'
```

### 6A.7 保存工程

```bash
curl -s http://localhost:9001/save_draft -X POST \
  -H "Content-Type: application/json" \
  -d '{"draft_id": "'$DRAFT_ID'"}'
```

保存后输出剪映工程路径，用户可以在剪映中打开。

**剪映模式完成后，跳到 Step 8（输出报告）**。不需要 Step 7 的 ffmpeg 拼接。

---

## Step 6B: ffmpeg 模式（兜底）

当剪映 MCP API 不可用时，走以下 ffmpeg 流程。

为每个镜头生成独立的视频片段。

### 6.1 创建工作目录

```bash
WORKDIR="/tmp/video_edit_$(date +%s)"
mkdir -p "$WORKDIR/segments"
```

### 6.2 目标参数

从 Step 1 的视频信息中获取：
```bash
TARGET_W=<模板宽度>    # 如 1280
TARGET_H=<模板高度>    # 如 720
TARGET_FPS=<模板帧率>  # 如 30
```

### 6.3 视频素材片段 — 稳定优先渲染

**核心原则**：画面稳定是专业感的基础。防抖策略按优先级：**选稳定片段 > 慢放稳定片段 > deshake 滤镜**。

#### 6.3.1 素材稳定性评估（在 Step 3 已完成）

在 Step 3 素材分析时，对每个视频素材的 3 个关键帧通过 Read 查看，标记稳定性：
- **稳定**：三脚架/稳定器拍摄，画面无抖动
- **轻微抖动**：手持但运动平缓，可用 deshake 修复
- **明显抖动**：手持且运动剧烈，deshake 效果有限

同时记录素材中**最稳定的片段区间**（如"0-3s 稳定，3-7s 有摇晃，7-10s 稳定"）。

#### 6.3.2 防抖策略（按优先级选择）

**优先级 1：选取素材中稳定的片段**

如果素材总时长充足，**优先从稳定区间截取**，完全避免抖动：
- 在 Step 3 分析时识别的稳定区间中截取
- 即使稳定区间的内容不是最佳匹配，稳定性优先于内容精确度
- 如果稳定区间太短不够用，可以用稳定片段做慢放来凑够时长

**优先级 2：稳定片段 + 慢放**

如果素材中有一段稳定但时长不够的片段，**用慢放来拉伸**：
```bash
# 找到 2s 稳定片段，需要 3.6s → 0.56x 慢放（setpts=1.786*PTS）
ffmpeg -y -ss <stable_start> -t <stable_duration> -i "<material>" \
  -vf "setpts=<stretch_factor>*PTS,minterpolate=fps=<TARGET_FPS>:mi_mode=mci:mc_mode=obmc,scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,setsar=1" \
  -t <beat_aligned_duration> \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

这种"为了防抖而慢放"是合理的——宁可稍慢也不要抖。慢放后的画面看起来更从容，完全可以接受。

**优先级 3：deshake 滤镜（兜底）**

只有在无法找到稳定片段时才用 deshake：
```bash
ffmpeg -y -ss <trim_start> -t <duration> -i "<material>" \
  -vf "deshake=rx=32:ry=32:edge=mirror:blocksize=8:contrast=125,scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,fps=<TARGET_FPS>,setsar=1" \
  -t <beat_aligned_duration> \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

deshake 参数：
| 抖动程度 | 参数 |
|---------|------|
| 轻微 | `rx=16:ry=16` |
| 中等 | `rx=24:ry=24` |
| 严重 | `rx=32:ry=32` + 配合慢放 0.9x |

#### 6.3.3 速度策略（严格跟随模板）

| 模板变速字段 | 处理方式 | setpts 系数 |
|------------|---------|------------|
| 正常 | 1.0x（除非为防抖做慢放） | `1.0*PTS` |
| 慢放Nx | 按模板倍率 | `<1/N>*PTS` |
| 快进Nx | 按模板倍率 | `<1/N>*PTS` |
| Speed Ramp | 分段处理（见 6.4） | 变速曲线 |
| 定格/倒放/延时 | 对应处理（见 6.4） | — |

**防抖慢放**（选了稳定但短的片段需要拉伸时）：
- 在报告中标注"防抖慢放 0.Nx，使用稳定片段 [起点-终点]"
- 慢放幅度取决于稳定片段长度 vs 需要的时长

#### 6.3.4 完整渲染示例

**示例 A：素材有稳定片段，直接截取（最优）**
```bash
# 素材 0-5s 稳定，需要 3.6s，直接从稳定区间截取
ffmpeg -y -ss 0.5 -t 3.6 -i "material.mp4" \
  -vf "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,fps=30,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_001.mp4"
```

**示例 B：稳定片段不够长，慢放拉伸**
```bash
# 素材 2-4s 稳定(2s)，需要 3.6s → 慢放到 0.56x
ffmpeg -y -ss 2.0 -t 2.0 -i "material.mp4" \
  -vf "setpts=1.8*PTS,minterpolate=fps=30:mi_mode=mci:mc_mode=obmc,scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,setsar=1" \
  -t 3.6 \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_001.mp4"
```

**示例 C：无稳定片段，deshake 兜底**
```bash
ffmpeg -y -ss 0.0 -t 3.6 -i "material.mp4" \
  -vf "deshake=rx=32:ry=32:edge=mirror:blocksize=8:contrast=125,scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,fps=30,setsar=1" \
  -t 3.6 \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_001.mp4"
```

### 6.4 变速效果（从模板分析的变速字段复现）

如果模板镜头的"变速"字段不是"正常"，需要在素材上复现相应的速度效果。

**均匀慢放**（模板标注 `慢放0.5x` 等）：
```bash
# 0.5x 慢放：setpts=2.0*PTS（PTS 系数 = 1/速度倍率）
# 配合 minterpolate 补帧让慢放更丝滑
ffmpeg -y -ss <trim_start> -t <素材需要的实际时长> -i "<material>" \
  -vf "setpts=<1/speed>*PTS,minterpolate=fps=<TARGET_FPS>:mi_mode=mci:mc_mode=obmc:me_mode=bilat,scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

> **注意**：minterpolate 运算量大。如果素材帧率已经很高（≥60fps），可以跳过 minterpolate 直接用 setpts。

**均匀快进**（模板标注 `快进2x` 等）：
```bash
# 2x 快进：setpts=0.5*PTS
ffmpeg -y -ss <trim_start> -t <素材需要的实际时长> -i "<material>" \
  -vf "setpts=<1/speed>*PTS,fps=<TARGET_FPS>,scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

**Speed Ramp（变速曲线）**（模板标注 `Speed Ramp(1.0→0.3→1.0)` 等）：

Speed Ramp 是最能体现剪辑高级感的效果。使用 setpts 表达式实现变速曲线：

```bash
# Speed Ramp 示例：前1/3正常 → 中间1/3减到0.3x → 后1/3恢复正常
# 镜头总时长 D 秒，分三段
# T1 = D/3, T2 = 2*D/3
#
# setpts 表达式（基于帧序号 N 和帧率 FR）：
# 正常段：PTS
# 减速段：在时间 T1 处开始拉伸，系数从1.0渐变到3.33(=1/0.3)
# 恢复段：在时间 T2 处开始恢复

ffmpeg -y -ss <trim_start> -i "<material>" \
  -vf "setpts='if(lt(T,<T1>),PTS,if(lt(T,<T2>),PTS+(<ramp_factor>-1)*(T-<T1>)/<TARGET_FPS>,PTS+<offset>))',fps=<TARGET_FPS>,trim=duration=<beat_aligned_duration>,scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

**简化版 Speed Ramp**（更可靠的实现方式）：分成三个子片段分别处理，再 concat：

```bash
# 子片段1：正常速度段
ffmpeg -y -ss <t0> -t <d1> -i "<material>" \
  -vf "scale=<W>:<H>:...,fps=<FPS>,setsar=1" -c:v libx264 -preset fast -crf 18 -an seg_N_a.mp4

# 子片段2：慢放段（带补帧）
ffmpeg -y -ss <t1> -t <d2_source> -i "<material>" \
  -vf "setpts=<factor>*PTS,minterpolate=fps=<FPS>:mi_mode=mci,scale=<W>:<H>:...,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an seg_N_b.mp4

# 子片段3：恢复正常速度段
ffmpeg -y -ss <t2> -t <d3> -i "<material>" \
  -vf "scale=<W>:<H>:...,fps=<FPS>,setsar=1" -c:v libx264 -preset fast -crf 18 -an seg_N_c.mp4

# concat 三段
printf "file 'seg_N_a.mp4'\nfile 'seg_N_b.mp4'\nfile 'seg_N_c.mp4'\n" > seg_N.txt
ffmpeg -y -f concat -safe 0 -i seg_N.txt -c copy "$WORKDIR/segments/seg_<NNN>.mp4"
```

**倒放**（模板标注 `倒放`）：
```bash
ffmpeg -y -ss <trim_start> -t <duration> -i "<material>" \
  -vf "reverse,scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,fps=<TARGET_FPS>,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

> **注意**：`reverse` 需要将整个片段加载到内存。对于长片段（>10s），先用 trim 截取再 reverse。

**定格**（模板标注 `定格0.5s`）：
```bash
# 先抽一帧，再 loop 为视频
ffmpeg -y -ss <freeze_time> -i "<material>" -frames:v 1 -q:v 2 /tmp/freeze_frame.jpg
ffmpeg -y -loop 1 -i /tmp/freeze_frame.jpg -t <freeze_duration> \
  -vf "scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,fps=<TARGET_FPS>,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>_freeze.mp4"
```

如果镜头中间有定格（如 Speed Ramp 到定格再恢复），用分段 concat 方式：
正常段 + 定格段 + 恢复段

**延时摄影效果**（模板标注 `延时`）：
```bash
# 从素材中快速抽帧模拟延时效果（如 10x 加速）
ffmpeg -y -ss <trim_start> -t <素材实际时长> -i "<material>" \
  -vf "setpts=0.1*PTS,fps=<TARGET_FPS>,scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

### 6.5 变速 + 音频卡点配合

**关键原则**：变速效果的减速点/加速点应该对齐到 strong_peak 或 beat。

典型卡点变速组合：
- **beat 处 Speed Ramp 减速**：在能量峰值瞬间减速，突出动作细节
- **downbeat 处恢复正常速度**：在强拍恢复，维持整体节奏感
- **连续 beats 快切 + 最后一个 beat 慢放**：蒙太奇高潮手法

如果模板分析中有 Speed Ramp，其减速点/加速点应该与最近的 beat 对齐（±0.1s）。

### 6.6 图片素材片段

根据图片类型选择不同的渲染方式：

**A. LOGO/品牌图片 → 静态 + fade 效果（不做 Ken Burns）**

LOGO 图片应该保持静态居中，不需要推拉效果：
```bash
ffmpeg -y -loop 1 -i "<logo_image>" -t <beat_aligned_duration> \
  -vf "scale=<TARGET_W>:<TARGET_H>:force_original_aspect_ratio=decrease,pad=<TARGET_W>:<TARGET_H>:(ow-iw)/2:(oh-ih)/2:black,fps=<TARGET_FPS>,fade=t=in:st=0:d=0.5,fade=t=out:st=<duration-0.5>:d=0.5,setsar=1" \
  -pix_fmt yuv420p -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

**B. 风景/内容图片 → Ken Burns 效果（慢推慢拉）**

```bash
FRAMES=$(echo "<duration> * <TARGET_FPS>" | bc | cut -d. -f1)
ffmpeg -y -loop 1 -i "<image>" -t <beat_aligned_duration> \
  -vf "zoompan=z='min(zoom+0.0015,1.3)':d=$FRAMES:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=<TARGET_W>x<TARGET_H>:fps=<TARGET_FPS>,setsar=1" \
  -pix_fmt yuv420p -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

zoompan 参数含义：
- `zoom+0.0015`：每帧放大 0.15%，总时长内约放大 1.3x
- `d=$FRAMES`：总帧数
- `x`/`y`：居中缩放

可变化的 zoompan 效果：
- 推入（zoom in）：`z='min(zoom+0.0015,1.3)'` + 居中
- 拉出（zoom out）：`z='if(eq(on,1),1.3,max(zoom-0.0015,1))'`
- 左移（pan left）：`x='if(eq(on,1),0,min(x+1,iw))'` + `z=1.1`
- 右移（pan right）：`x='if(eq(on,1),iw/zoom,max(x-1,0))'` + `z=1.1`

### 6.5 过渡镜头（运动模糊）

```bash
ffmpeg -y -ss <任意起点> -t <beat_aligned_duration> -i "<任意素材>" \
  -vf "setpts=0.25*PTS,tblend=all_mode=average,boxblur=luma_radius=30:luma_power=2,scale=<TARGET_W>:<TARGET_H>:force_original_aspect_ratio=decrease,pad=<TARGET_W>:<TARGET_H>:(ow-iw)/2:(oh-ih)/2:black,fps=<TARGET_FPS>,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

### 6.6 文字叠加（通用流程，适用于任何镜头）

在所有素材片段渲染完成后（6.3-6.5），遍历 Step 1.5 的文字元素表。对每个标记了"需要叠加文字"的镜头，执行以下流程：

#### 6.6.1 生成高品质文字 PNG

⚠️ **文字品质决定了整个视频的专业感**。必须严格按照分析中的文字属性生成，包括：字体选择、大小、颜色、字间距、位置。

**字体选择策略**（根据分析中的"样式"字段）：

| 分析描述 | macOS 字体优先级 | Linux 备选 |
|---------|----------------|-----------|
| 无衬线粗体 | PingFang SC Bold → Helvetica Bold | Noto Sans CJK Bold |
| 无衬线细体/常规 | PingFang SC Regular → Helvetica | Noto Sans CJK Regular |
| 衬线体 | Songti SC → Times New Roman | Noto Serif CJK |
| 手写体/书法体 | STKaiti → Apple LiSung | — |
| 英文无衬线 | SF Pro Display → Helvetica Neue | — |

```bash
python3 << 'PYEOF'
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

W, H = <TARGET_W>, <TARGET_H>
# 用 2x 分辨率渲染再缩小，获得更清晰的抗锯齿效果
SCALE = 2
img = Image.new('RGBA', (W * SCALE, H * SCALE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# 根据分析中的字体样式选择字体文件
font_candidates = {
    'bold': [
        '/System/Library/Fonts/PingFang.ttc',       # index 通常 0=Regular, 需要找 Bold
        '/Library/Fonts/Arial Bold.ttf',
    ],
    'regular': [
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/Helvetica.ttc',
    ],
    'serif': [
        '/Library/Fonts/Songti.ttc',
        '/System/Library/Fonts/Times.ttc',
    ],
    'handwriting': [
        '/Library/Fonts/STKaiti.ttf',
        '/System/Library/Fonts/STHeiti Medium.ttc',
    ],
}

style = '<style_from_analysis>'  # 'bold', 'regular', 'serif', 'handwriting'
font_size = <font_size> * SCALE
font = None
for fp in font_candidates.get(style, font_candidates['regular']):
    if os.path.exists(fp):
        try:
            font = ImageFont.truetype(fp, size=font_size)
            break
        except:
            pass
if not font:
    for fp in ['/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/Helvetica.ttc']:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, size=font_size)
                break
            except:
                pass
if not font:
    font = ImageFont.load_default()

# 绘制文字（支持多行，每行独立定位）
texts = [
    # (文字内容, x偏移, y偏移, 颜色RGBA, 字号倍率)
    ('<text_line_1>', <x1>, <y1>, <color1_rgba>, 1.0),
    # ('<text_line_2>', <x2>, <y2>, <color2_rgba>, 0.7),  # 副标题较小
]

for text, xoff, yoff, color, size_mult in texts:
    if size_mult != 1.0:
        f = ImageFont.truetype(font.path, size=int(font_size * size_mult))
    else:
        f = font
    bbox = draw.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (W * SCALE - tw) // 2 + xoff * SCALE
    y = (H * SCALE - th) // 2 + yoff * SCALE
    draw.text((x, y), text, fill=color, font=f)

# 缩小到目标分辨率（高质量抗锯齿）
img = img.resize((W, H), Image.LANCZOS)
img.save('$WORKDIR/text_overlay_<NNN>.png')
PYEOF
```

> **关键技巧**：用 2x 分辨率渲染再 LANCZOS 缩小，文字边缘会更平滑精致，不会有锯齿感。

#### 6.6.2 首尾镜头特殊处理

**最后一个镜头 → 直接从原视频截取（已在 Step 4.1 说明）**

```bash
ORIG_VIDEO=$(ls uploads/<template_job_id>_*.mp4 uploads/<template_job_id>_*.mov 2>/dev/null | head -1)
if [ -n "$ORIG_VIDEO" ]; then
  ffmpeg -y -ss <last_shot_start> -t <last_shot_duration> -i "$ORIG_VIDEO" \
    -vf "scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,fps=<FPS>,setsar=1" \
    -c:v libx264 -preset fast -crf 18 -an \
    "$WORKDIR/segments/seg_<LAST>.mp4"
fi
```

**第一个镜头 → 素材画面 + 原视频文字叠加**

不用 Pillow 生成文字，而是**直接从原视频第一个镜头提取文字层**叠加到新素材上。

方法：截取原视频第一个镜头，将其作为半透明层 overlay 到素材上。由于文字通常是亮色（白色/青绿色）叠加在画面上，可以用混合模式提取：

```bash
# Step 1: 截取原视频第一个镜头
ffmpeg -y -ss <first_shot_start> -t <first_shot_duration> -i "$ORIG_VIDEO" \
  -vf "scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,fps=<FPS>,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/orig_first_shot.mp4"

# Step 2: 用 blend=lighten 模式将原视频文字叠加到素材画面
# lighten 模式：取两个画面中较亮的像素，文字（亮色）会保留，背景（暗于素材）会被素材覆盖
ffmpeg -y -i "$WORKDIR/segments/seg_001.mp4" -i "$WORKDIR/orig_first_shot.mp4" \
  -filter_complex "[0:v][1:v]blend=all_mode=lighten:shortest=1[outv]" \
  -map "[outv]" -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_001_txt.mp4"
mv "$WORKDIR/segments/seg_001_txt.mp4" "$WORKDIR/segments/seg_001.mp4"
```

> **blend=lighten 原理**：对每个像素取两个输入中较亮的那个。原视频中文字是亮色（白色/青绿色），背景是实际画面（较暗）。素材画面的内容会显示在背景区域，而原视频的亮色文字会"浮"在素材上面。

**如果文字是深色的**（少见），改用 `blend=all_mode=darken`。

**如果 blend 效果不理想**（文字和素材亮度冲突），退回到 Pillow 生成（6.6.1 + 下面的场景 A）。

#### 6.6.3 通用文字叠加（中间镜头）

**场景 A：素材画面 + Pillow 文字叠加**（中间镜头需要加文字时）

```bash
ffmpeg -y -i "$WORKDIR/segments/seg_<NNN>.mp4" \
  -loop 1 -i "$WORKDIR/text_overlay_<NNN>.png" \
  -filter_complex "
    [1:v]format=rgba,fade=t=in:st=<fade_start>:d=<fade_duration>:alpha=1[txt];
    [0:v][txt]overlay=0:0:shortest=1[outv]
  " -map "[outv]" -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>_txt.mp4"
mv "$WORKDIR/segments/seg_<NNN>_txt.mp4" "$WORKDIR/segments/seg_<NNN>.mp4"
```

**场景 B：纯文字画面**（模板镜头是纯文字/LOGO，素材中没有合适内容）

先生成带背景色的文字图片（非透明），再转视频：

```bash
# 修改 Pillow 脚本中的背景色
img = Image.new('RGB', (W, H), '<bg_color>')  # 如 (0,0,0) 黑底

# 转视频（带 fade 效果）
ffmpeg -y -loop 1 -i "$WORKDIR/text_overlay_<NNN>.png" -t <duration> \
  -vf "fade=t=in:st=0:d=0.5,fade=t=out:st=<duration-0.5>:d=0.5,fps=<TARGET_FPS>,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

**场景 C：从原视频截取（最后的 fallback）**

仅当素材完全不可用且 Pillow 生成效果不佳时（如 LOGO 有复杂动效），尝试从原视频截取：

```bash
ORIG_VIDEO=$(ls uploads/<template_job_id>_*.mp4 uploads/<template_job_id>_*.mov 2>/dev/null | head -1)
if [ -n "$ORIG_VIDEO" ]; then
  ffmpeg -y -ss <shot_start> -t <shot_duration> -i "$ORIG_VIDEO" \
    -vf "scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,fps=<FPS>,setsar=1" \
    -c:v libx264 -preset fast -crf 18 -an \
    "$WORKDIR/segments/seg_<NNN>.mp4"
fi
```

#### 6.6.3 文字出现动画

根据分析中的"出现方式"字段选择 ffmpeg 参数：

| 分析描述 | fade 参数 |
|---------|----------|
| 直接出现 | 不加 fade（`st=0, d=0`） |
| 淡入 | `fade=t=in:st=0:d=0.5:alpha=1` |
| 叠化出现（慢速淡入） | `fade=t=in:st=0:d=1.0:alpha=1` |
| 延迟出现 | `fade=t=in:st=<delay>:d=0.5:alpha=1` |

#### 6.6.4 中间纯标题画面（无实际画面内容的镜头）

从模板分析的"文字/品牌元素"字段提取文字信息，用 Pillow 生成文字图片，再转为视频片段。

**Step A：生成文字图片**

```bash
python3 -c "
from PIL import Image, ImageDraw, ImageFont
import sys

W, H = <TARGET_W>, <TARGET_H>
bg_color = '<bg_color>'  # 从分析中读取，如 'black', '#1a1a1a'
img = Image.new('RGB', (W, H), bg_color)
draw = ImageDraw.Draw(img)

# 尝试加载系统字体（macOS 优先使用苹方/Helvetica）
font_paths = [
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/Helvetica.ttc',
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
]
font = None
for fp in font_paths:
    try:
        font = ImageFont.truetype(fp, size=<font_size>)
        break
    except:
        pass
if not font:
    font = ImageFont.load_default()

# 绘制文字（居中）
text = '<text_content>'
bbox = draw.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
x = (W - tw) // 2
y = (H - th) // 2 + <y_offset>  # y_offset 根据分析中的位置调整
draw.text((x, y), text, fill='<text_color>', font=font)

# 如果有第二行文字，继续绘制
# ...

img.save('$WORKDIR/title_<NNN>.png')
"
```

**Step B：图片转视频（带 fade 效果）**

```bash
ffmpeg -y -loop 1 -i "$WORKDIR/title_<NNN>.png" -t <duration> \
  -vf "fade=t=in:st=0:d=0.5,fade=t=out:st=<duration-0.5>:d=0.5,fps=<TARGET_FPS>,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

**文字出现动画变体**（根据分析中的"出现方式"选择）：

| 分析描述 | ffmpeg 实现 |
|---------|-----------|
| 淡入 | `fade=t=in:st=0:d=0.5` |
| 从下滑入 | 先生成带透明通道的 PNG，用 overlay + 位移动画 |
| 缩放进入 | `zoompan=z='if(eq(on,1),1.5,max(zoom-0.01,1))':d=<frames>:s=<W>x<H>` |
| 直接出现 | 不加 fade |

**如果分析中没有文字信息**，退回到纯色 + fade：
```bash
ffmpeg -y -f lavfi -i "color=c=black:s=<TARGET_W>x<TARGET_H>:d=<duration>:r=<TARGET_FPS>" \
  -vf "fade=t=in:st=0:d=0.5,fade=t=out:st=<duration-0.5>:d=0.5,setsar=1" \
  -c:v libx264 -preset fast -crf 18 \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

### 6.7 视频防抖处理

**所有视频素材片段**都应该经过防抖处理，消除手持拍摄的抖动，提升专业感。

使用 ffmpeg 的 `deshake` 滤镜：

```bash
# 在 scale/pad 之前加入 deshake
ffmpeg -y -ss <trim_start> -t <duration> -i "<material>" \
  -vf "deshake=rx=16:ry=16:edge=mirror:blocksize=8:contrast=125,scale=<W>:<H>:force_original_aspect_ratio=decrease,pad=<W>:<H>:(ow-iw)/2:(oh-ih)/2:black,fps=<TARGET_FPS>,setsar=1" \
  -c:v libx264 -preset fast -crf 18 -an \
  "$WORKDIR/segments/seg_<NNN>.mp4"
```

**deshake 参数说明**：
- `rx=16:ry=16`：搜索范围（像素），越大防抖越强但越慢
- `edge=mirror`：边缘镜像填充（避免出现黑边）
- `blocksize=8`：运动检测块大小
- `contrast=125`：对比度阈值（过滤低对比度区域的噪声）

**防抖策略**（根据模板分析的运镜类型决定）：

| 模板运镜 | 防抖处理 |
|---------|---------|
| 静态镜头 | 强防抖 `rx=32:ry=32` — 完全消除微抖 |
| 手持跟随 | 中等防抖 `rx=16:ry=16` — 保留跟随感，消除抖动 |
| 手持微晃 | 轻微防抖 `rx=8:ry=8` — 保留一定手持质感 |
| 平移/横摇 | 不防抖 — deshake 可能干扰平移运动 |
| 甩镜/快速运镜 | 不防抖 — 运动模糊是刻意效果 |
| 稳定器/三脚架 | 不防抖 — 原素材已经稳定 |

**重要**：deshake 必须在 scale 之前应用（在原始分辨率上防抖效果更好）。

> **注意**：如果素材本身来自稳定器或三脚架拍摄（画面已经很稳），跳过防抖以避免引入不必要的画面裁切。通过 Read 查看关键帧判断素材稳定性。

### 6.8 验证片段

对每个生成的片段验证：
```bash
ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$WORKDIR/segments/seg_<NNN>.mp4"
```

确保时长与卡点时间表一致（误差 ≤ 0.05s）。

---

## Step 7: 卡点拼接 + 转场

采用**两阶段**拼接策略，避免单个 filter_complex 过于复杂。

### 7.1 Phase A：分组

将连续的硬切镜头分为一组，在转场位置断开：

```
组1: seg_001 + seg_002 + seg_003（硬切连接）
-- xfade: dissolve 0.5s --
组2: seg_004 + seg_005（硬切连接）
-- xfade: fadeblack 0.3s --
组3: seg_006（单独）
...
```

### 7.2 Phase B：组内拼接（concat demuxer）

对每个硬切组，使用 concat demuxer（无需重编码）：

```bash
# 生成 concat 列表
echo "file 'seg_001.mp4'" > "$WORKDIR/group_1.txt"
echo "file 'seg_002.mp4'" >> "$WORKDIR/group_1.txt"
echo "file 'seg_003.mp4'" >> "$WORKDIR/group_1.txt"

ffmpeg -y -f concat -safe 0 -i "$WORKDIR/group_1.txt" -c copy "$WORKDIR/group_1.mp4"
```

### 7.3 Phase C：组间转场（xfade）

逐对应用 xfade。由于 ffmpeg xfade 一次只能处理两个输入，需要迭代 chain：

```bash
# 第一对
ffmpeg -y -i "$WORKDIR/group_1.mp4" -i "$WORKDIR/group_2.mp4" \
  -filter_complex "[0:v][1:v]xfade=transition=dissolve:duration=0.5:offset=<G1时长-0.5>[outv]" \
  -map "[outv]" -c:v libx264 -preset fast -crf 18 "$WORKDIR/chain_1.mp4"

# 第二对（chain_1 + group_3）
ffmpeg -y -i "$WORKDIR/chain_1.mp4" -i "$WORKDIR/group_3.mp4" \
  -filter_complex "[0:v][1:v]xfade=transition=fadeblack:duration=0.3:offset=<chain1时长-0.3>[outv]" \
  -map "[outv]" -c:v libx264 -preset fast -crf 18 "$WORKDIR/chain_2.mp4"
```

**xfade offset 计算**：
- offset = 前一个视频的时长 - 转场时长
- 每次 xfade 后总时长 = 前视频时长 + 后视频时长 - 转场时长

**注意**：如果只有一个组（全部硬切），直接进入 Phase D。

### 7.4 Phase D：添加音频

根据 `--audio` 模式：

**template 模式**（默认，使用模板原始音频）：
```bash
# 先提取模板视频的音频（需要找到模板原视频）
# 模板视频路径可能在 markdown 文件名中，或在上传目录中
ffmpeg -y -i "<template_video>" -vn -c:a aac -b:a 192k "$WORKDIR/template_audio.aac"

# mux 视频 + 音频
ffmpeg -y -i "$WORKDIR/chain_final.mp4" -i "$WORKDIR/template_audio.aac" \
  -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart "<output_path>"
```

**keep 模式**（保留素材音频）：
在 Step 6 中不加 `-an`，保留每个片段的音频，concat 时一并拼接。

**silent 模式**：
```bash
ffmpeg -y -i "$WORKDIR/chain_final.mp4" \
  -f lavfi -i anullsrc=r=44100:cl=stereo \
  -c:v copy -c:a aac -shortest -movflags +faststart "<output_path>"
```

**bgm 模式**（使用指定 BGM）：
```bash
ffmpeg -y -i "$WORKDIR/chain_final.mp4" -i "<bgm_path>" \
  -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart "<output_path>"
```

---

## Step 8: 输出结果

⚠️ **必须在报告末尾输出以下标准化 JSON**。自动剪辑系统直接解析此 JSON 驱动剪映 MCP，不解析 markdown 表格。**如果不输出此 JSON，剪辑将失败。**

格式（用特殊标记包裹，不能改）：

```
<!-- EDIT_CONFIG_START -->
{
  "matches": [
    {
      "shot_number": 1,
      "material": "1529_1775539936.mp4",
      "trim_start": 2.0,
      "duration": 7.0,
      "speed": 1.0,
      "transition": "硬切",
      "transition_duration": 0,
      "intro_animation": "渐显",
      "intro_duration": 0.5,
      "outro_animation": null,
      "outro_duration": 0,
      "color_adjust": {"temperature": -5, "saturation": 3},
      "reason": "全景水景，素材偏暖需降色温匹配模板冷调"
    },
    {
      "shot_number": 2,
      "material": "1532_1775540189.mp4",
      "trim_start": 1.5,
      "duration": 4.25,
      "speed": 1.0,
      "transition": "硬切",
      "transition_duration": 0,
      "intro_animation": null,
      "intro_duration": 0,
      "outro_animation": null,
      "outro_duration": 0,
      "color_adjust": null,
      "reason": "色调已匹配，无需校正"
    }
  ]
}
<!-- EDIT_CONFIG_END -->
```

**字段说明**：
- `shot_number`：模板镜头编号（必须覆盖所有镜头，一个都不能少）
- `material_type`：建议的素材类型（来自模板分析的"素材类型"列）：`视频` / `图片/视频均可` / `LOGO`
  - 模板标注"视频"的镜头**优先用视频素材**，尽量不用图片替代（视频有运动感，图片是静态的）
  - 模板标注"图片/视频均可"的镜头，图片和视频都行（可以从视频中截取一帧当图片用）
- `material`：匹配的素材文件名（必须是素材目录中实际存在的文件名）
- `trim_start`：从素材的第几秒开始截取
- `duration`：该镜头需要的时长（秒）
- `speed`：播放速度（1.0=正常，0.5=慢放2倍）
- `transition`：与前一镜头的转场（"硬切" / 剪映转场名如"叠化"/"闪黑"等）。只填转场效果，不要填入场/出场动画
- `transition_duration`：转场时长（秒），硬切为 0
- `intro_animation`：入场动画（null=无, "渐显"=从黑渐显, "缩放"=缩放进入 等）。第一个镜头通常需要"渐显"
- `intro_duration`：入场动画时长（秒）
- `outro_animation`：出场动画（null=无, "渐隐"=渐隐到黑）。最后一个镜头通常需要"渐隐"
- `outro_duration`：出场动画时长（秒）
- `color_adjust`：色彩校正参数（null=不调整），值范围 -50 到 50：
  - `brightness`：亮度 | `contrast`：对比度 | `saturation`：饱和度
  - `temperature`：色温（负=冷蓝 正=暖黄）| `tint`：色调
  - 只写需要调整的参数，不需要的不写
  - **判断方法**：在 Read 素材关键帧时对比素材色调和模板色调，偏暖的降温，偏冷的升温，让所有镜头色调统一
- `reason`：匹配理由（简短）

**必须包含每一个镜头的匹配**——数组长度必须等于模板镜头数。

同时在 JSON 中加入 `text_overlays` 数组，**由 AI 判断哪些文字需要叠加**：

```json
"text_overlays": [
  {
    "type": "image_overlay",
    "image_file": "WechatIMG1736.jpg",
    "shot_numbers": [1, 2],
    "needs_overlay": true,
    "reason": "素材视频中无品牌文字，使用文字图片叠加（保留原始青绿色字体设计）",
    "alpha": 0.8,
    "scale": 0.75,
    "position_x": 0,
    "position_y": 0,
    "start_offset": 0.3,
    "overlay_duration": null
  },
  {
    "type": "text",
    "text": "RESTONE 枕石™",
    "shot_numbers": [1, 2],
    "needs_overlay": true,
    "reason": "素材中无此文字且无文字图片可用，用 add_text 生成",
    "font_size_percent": 8,
    "color_hex": "#5EEDC7",
    "position_y": -0.06,
    "animation": "fade_in",
    "animation_duration": 0.75
  },
  {
    "type": "text",
    "text": "RESTONE 枕石",
    "shot_numbers": [6, 7, 8, 9],
    "needs_overlay": false,
    "reason": "素材图片/视频已自带此文字"
  }
]
```

**判断 `needs_overlay` 的规则**：

⚠️ **这个判断非常关键，必须逐个镜头检查匹配到的素材：**

1. 查看该镜头匹配到的具体素材文件
2. 在 Step 3 素材分析时已看过这个素材的关键帧，记录了素材中的"画面文字"
3. **逐条对比**：
   - 模板要求的文字内容 vs 素材中已有的文字内容
   - 如果素材中**已有相同或相似的文字**（品牌名、LOGO、Slogan） → `needs_overlay: false`
   - 如果素材中**没有这个文字** → `needs_overlay: true`
4. **特别注意**：
   - 图片素材（.jpg/.png）通常已经包含了设计好的文字/LOGO，大概率不需要叠加
   - 视频素材如果是原始拍摄素材，通常没有文字叠加，需要 `true`
   - 如果不确定，宁可设为 `false`（不叠加），用户可以在剪映里手动添加

**image_overlay 时间控制字段**：
- `start_offset`：overlay 从镜头开始后延迟多少秒出现（秒）。例如 `0.3` 表示镜头开始 0.3s 后 overlay 才出现（模拟淡入）。默认 0。
- `overlay_duration`：overlay 持续多少秒。`null` 表示持续到覆盖镜头结束。如果需要 overlay 只在镜头后半段出现（如 LOGO 在长镜头末尾叠化），设置合适的 start_offset 和 overlay_duration。
  - 例：5.5s 的镜头，LOGO 在最后 2s 叠化出现 → `"start_offset": 3.5, "overlay_duration": 2.0`

**⚠️ 不要对已经用了相同图片作为镜头画面的 shot 再叠加同一张图片** — 这会导致重叠。如果 shot 14 的素材就是 WechatIMG1735.jpg，不要再对 shot 14 做 image_overlay WechatIMG1735.jpg。

**素材选择的注意事项**：

⚠️ **选择图片/LOGO素材时必须考虑画布比例：**
- 如果输出是竖屏（9:16），LOGO 图片也应该是竖版或方形的
- **不要选择超宽横图**（如 6910×726）作为竖屏视频的 LOGO 结尾——放大后会严重失真
- 优先选择宽高比接近画布的素材

在 JSON 之前可以自由输出 markdown 格式的分析过程和报告（给人看），但 JSON 是给程序用的，格式不能变。

## 警告
- [列出低置信度匹配、变速过大、缺失素材等问题]
```

---

## Step 9: 清理

```bash
rm -rf "$WORKDIR"
```

---

## 注意事项

- **卡点精度优先**：所有镜头切换时间必须来自节拍对齐后的时间表，不是模板原始时间
- **转场时长计入总时长**：xfade 会缩短总时长，需要在 offset 计算中扣除
- **分辨率一致性**：所有片段必须使用相同的目标分辨率和 SAR=1
- **编码兼容性**：中间片段使用 libx264 fast crf 18，最终输出使用 medium crf 20
- **错误处理**：每个 ffmpeg 命令后检查返回码，失败时输出 stderr 信息
- 如果素材数量极少（< 3个），在报告中明确提示"素材不足，建议提供更多素材"
