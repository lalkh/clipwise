#!/bin/bash
# 视频分镜提取脚本
# 用法: extract_frames.sh <video_path> <output_dir> [threshold]
#
# 功能:
# 1. 获取视频基本信息
# 2. 基于场景切换检测提取关键帧
# 3. 输出每一帧的时间戳

set -e

VIDEO_PATH="$1"
OUTPUT_DIR="$2"
THRESHOLD="${3:-0.3}"  # 场景切换阈值，越小越敏感，默认 0.3

if [ -z "$VIDEO_PATH" ] || [ -z "$OUTPUT_DIR" ]; then
    echo "用法: extract_frames.sh <video_path> <output_dir> [threshold]"
    exit 1
fi

if [ ! -f "$VIDEO_PATH" ]; then
    echo "错误: 视频文件不存在: $VIDEO_PATH"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# ========== 第一步：获取视频基本信息 ==========
echo "===== VIDEO_INFO_START ====="
ffprobe -v quiet -print_format json -show_format -show_streams "$VIDEO_PATH"
echo "===== VIDEO_INFO_END ====="

# ========== 第二步：检测场景切换时间点 ==========
echo "===== SCENE_DETECT_START ====="
# 使用 select filter 检测场景变化，输出每个切换点的时间戳
ffprobe -v quiet -f lavfi \
    -i "movie='$(echo "$VIDEO_PATH" | sed "s/'/'\\\\''/g")',select='gt(scene,$THRESHOLD)'" \
    -show_entries frame=pts_time -of csv=p=0 2>/dev/null | head -100
echo "===== SCENE_DETECT_END ====="

# ========== 第三步：提取首帧 ==========
ffmpeg -v quiet -y -i "$VIDEO_PATH" -vf "select=eq(n\,0)" -frames:v 1 \
    "$OUTPUT_DIR/frame_000_0.00s.jpg" 2>/dev/null
echo "0.00" > "$OUTPUT_DIR/timestamps.txt"

# ========== 第四步：在每个场景切换点提取关键帧 ==========
FRAME_IDX=1
ffprobe -v quiet -f lavfi \
    -i "movie='$(echo "$VIDEO_PATH" | sed "s/'/'\\\\''/g")',select='gt(scene,$THRESHOLD)'" \
    -show_entries frame=pts_time -of csv=p=0 2>/dev/null | head -100 | while read -r TIMESTAMP; do
    if [ -n "$TIMESTAMP" ]; then
        PADDED_IDX=$(printf "%03d" $FRAME_IDX)
        FORMATTED_TS=$(printf "%.2f" "$TIMESTAMP")
        OUTPUT_FILE="$OUTPUT_DIR/frame_${PADDED_IDX}_${FORMATTED_TS}s.jpg"
        ffmpeg -v quiet -y -ss "$TIMESTAMP" -i "$VIDEO_PATH" -frames:v 1 -q:v 2 "$OUTPUT_FILE" 2>/dev/null
        echo "$FORMATTED_TS" >> "$OUTPUT_DIR/timestamps.txt"
        FRAME_IDX=$((FRAME_IDX + 1))
    fi
done

# ========== 第五步：统计结果 ==========
TOTAL_FRAMES=$(ls "$OUTPUT_DIR"/frame_*.jpg 2>/dev/null | wc -l | tr -d ' ')
echo "===== EXTRACTION_RESULT ====="
echo "总共提取关键帧: $TOTAL_FRAMES"
echo "输出目录: $OUTPUT_DIR"
echo "时间戳文件: $OUTPUT_DIR/timestamps.txt"
ls -1 "$OUTPUT_DIR"/frame_*.jpg 2>/dev/null
echo "===== EXTRACTION_DONE ====="
