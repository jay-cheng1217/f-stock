"""Wrapper: 訓練模型並將輸出寫到 log 檔"""
import sys
import io
import warnings

# Force UTF-8 and line buffering
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

warnings.filterwarnings("ignore")

LOG = open("F:/stock/train_output.log", "w", encoding="utf-8")

def log(msg):
    print(msg, flush=True)
    LOG.write(msg + "\n")
    LOG.flush()

log("=== Starting training ===")

from ml.train import run_training
result = run_training()

if result:
    model, meta = result
    log(f"\nTraining complete!")
    log(f"Features: {meta['n_features']}")
    log(f"Stocks: {meta['n_stocks']}")
    log(f"Samples: {meta['n_samples']}")
    log(f"Avg F1: {meta['avg_f1_weighted']:.4f}")
    log(f"Avg UP precision: {meta['avg_precision_up']:.4f}")
    log(f"Model: {meta['model_file']}")
else:
    log("Training returned no result")

LOG.close()
