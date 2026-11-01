#!/bin/bash
# 下载中文 embedding 模型 bge-small-zh-v1.5（走 hf-mirror，直连可用）
set -u
OUT="D:/ob-race/stepfun/models/Xenova/bge-small-zh-v1.5"
BASE="https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main"
mkdir -p "$OUT/onnx"

for f in config.json tokenizer.json tokenizer_config.json vocab.txt special_tokens_map.json; do
  [ -s "$OUT/$f" ] || curl -sL --noproxy '*' --max-time 240 "$BASE/$f" -o "$OUT/$f"
done

for f in model.onnx model_fp16.onnx model_quantized.onnx; do
  if [ ! -s "$OUT/onnx/$f" ]; then
    echo "downloading $f ..."
    curl -sL --noproxy '*' --max-time 900 "$BASE/onnx/$f" -o "$OUT/onnx/$f"
  fi
done

echo "--- 体积 ---"
for f in "$OUT"/onnx/*.onnx; do printf "%-26s %12d\n" "$(basename "$f")" "$(stat -c%s "$f")"; done
echo "--- 配置 ---"
ls -l "$OUT" | awk '$9!="" && $9!="onnx"{printf "%-26s %8d\n", $9, $5}'
