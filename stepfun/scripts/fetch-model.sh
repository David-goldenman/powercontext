#!/bin/bash
# 下载 all-MiniLM-L6-v2 各精度 ONNX 权重，用于量化前后体积对比
set -u
export HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890
OUT="D:/ob-race/stepfun/models/Xenova/all-MiniLM-L6-v2"
BASE="https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main"
mkdir -p "$OUT/onnx"

for f in config.json tokenizer.json tokenizer_config.json vocab.txt special_tokens_map.json; do
  [ -s "$OUT/$f" ] || curl -sL --max-time 180 "$BASE/$f" -o "$OUT/$f"
done

for f in model.onnx model_fp16.onnx model_quantized.onnx model_int8.onnx model_q4.onnx; do
  if [ ! -s "$OUT/onnx/$f" ]; then
    echo "downloading $f ..."
    curl -sL --max-time 900 "$BASE/onnx/$f" -o "$OUT/onnx/$f"
  fi
  printf "%-22s %10s bytes\n" "$f" "$(stat -c%s "$OUT/onnx/$f" 2>/dev/null || echo 0)"
done

echo "--- 其它文件 ---"
ls -l "$OUT" | awk '{print $5, $9}'
