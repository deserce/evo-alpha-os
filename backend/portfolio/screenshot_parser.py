"""Bounded, on-demand local Vision OCR; results are editable drafts, never ledger writes."""
from __future__ import annotations

import hashlib
import fcntl
import io
import json
import re
import subprocess
import tempfile
import threading
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_IMAGES = 8
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_ROWS = 200
_OCR_LOCK = threading.Lock()
_SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "portfolio_ocr.swift"


def _number(text: str, scale: int) -> int | None:
    cleaned = text.strip().replace(",", "").replace("，", "").replace("−", "-")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", cleaned):
        return None
    try:
        value = Decimal(cleaned) * scale
        return int(value) if value == value.to_integral_value() else None
    except (InvalidOperation, ValueError):
        return None


def parse_lines(lines: list[dict], image_index: int = 0,
                reference_year: int | None = None) -> dict:
    """Parse spatial OCR lines, preserving ambiguous and clipped rows for review."""
    lines = sorted(lines, key=lambda item: (float(item["y"]), float(item["x"])))
    texts = " ".join(item["text"] for item in lines)
    if "持仓股" in texts and "对账单" not in texts:
        return {"rows": [], "warnings": ["持仓截图只能用于对账，未生成成交。"]}
    transfer = "银证转账" in texts
    anchors = []
    for item in lines:
        label = re.sub(r"\s+", "", item["text"])
        match = re.match(r"^(买入|卖出)[—–－-]?(.*)", label)
        if match and item["x"] < .4:
            anchors.append((item, "BUY" if match[1] == "买入" else "SELL", match[2]))
        elif transfer and label in {"收入", "银证转入", "银证转出", "支出"}:
            anchors.append((item, "DEPOSIT" if label in {"收入", "银证转入"} else "WITHDRAWAL", None))
    rows = []
    for index, (anchor, kind, name) in enumerate(anchors):
        y = anchor["y"]
        end = min(y + .064, anchors[index + 1][0]["y"] - .004) if index + 1 < len(anchors) else y + .064
        group = [item for item in lines if y - .012 <= item["y"] < end]
        # A year is trusted only when explicitly present in a preceding month heading.
        year = reference_year
        for item in lines:
            heading = re.search(r"\b(20\d{2})\s*[-年]\s*(\d{1,2})(?:月|\b)", item["text"])
            if heading and item["y"] <= y and item["x"] < .4:
                year = int(heading[1])
        joined = " ".join(item["text"] for item in group)
        timestamp = re.search(r"(\d{2})\s*[-/]\s*(\d{2})\s+(\d{1,2})[:：](\d{2})", joined)
        trade_date = None
        trade_time = None
        warnings = []
        if timestamp:
            try:
                month, day, hour, minute = map(int, timestamp.groups())
                if hour > 23 or minute > 59:
                    raise ValueError("invalid_time")
                trade_time = f"{hour:02}:{minute:02}:00"
                if year is not None:
                    trade_date = date(year, month, day).isoformat()
            except ValueError:
                warnings.append("日期或时间无效，请核对原图。")
        if trade_date is None:
            warnings.append("缺少完整成交日期（含年份），请补填。")
        if trade_time is None:
            warnings.append("缺少成交时间，请补填。")
        middle = [item for item in group if .45 <= item["x"] < .73]
        right = [item for item in group if item["x"] >= .73]
        middle.sort(key=lambda item: item["y"])
        right.sort(key=lambda item: item["y"])
        price = quantity = fee = amount = net_cash = None
        if kind in {"BUY", "SELL"}:
            if middle:
                price = _number(middle[0]["text"], 1000000)
            if len(middle) > 1:
                quantity = _number(middle[1]["text"], 1)
            if right:
                net_cash = _number(right[0]["text"], 100)
            if len(right) > 1:
                fee = _number(right[1]["text"], 100)
            if not name or price is None or quantity is None or fee is None or net_cash is None:
                warnings.append("成交字段缺失或截断，请逐项补齐。")
            if price is not None and quantity is not None and fee is not None and net_cash is not None:
                gross = Decimal(price) * quantity / 10000
                expected = -gross - fee if kind == "BUY" else gross - fee
                if abs(expected - net_cash) > Decimal("0.5"):
                    warnings.append("价格×数量与净金额/税费不一致，请核对。")
        else:
            if right:
                net_cash = _number(right[0]["text"], 100)
                amount = abs(net_cash) if net_cash is not None else None
            if amount is None or amount <= 0:
                warnings.append("资金金额缺失或无效，请补填。")
        rows.append({"row_id": f"image-{image_index}-row-{index}", "business_type": kind,
                     "trade_date": trade_date, "trade_time": trade_time, "symbol": None,
                     "name": name or None, "price_micros": price, "quantity": quantity,
                     "fee_cents": fee, "amount_cents": amount, "net_cash_cents": net_cash,
                     "image_index": image_index, "warnings": warnings})
    warnings = [] if rows else ["未识别到完整行标题，请使用成交/交割单或银证转账截图。"]
    return {"rows": rows, "warnings": warnings}


def _validate_image(content: bytes) -> None:
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("每张图片须为 1 字节至 8MB。")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("仅支持 PNG、JPEG、WEBP 图片。")
            width, height = image.size
            if width <= 0 or height <= 0 or max(width, height) > 16000 or width * height > 24000000:
                raise ValueError("图片像素过大，请裁剪后重试。")
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片损坏或无法解码。") from exc


def _recognize(content: bytes, directory: Path, executable: Path, index: int) -> list[dict]:
    path = directory / f"image-{index}.png"
    # Re-encode validated formats at a bounded resolution for the native decoder.
    with Image.open(io.BytesIO(content)) as image:
        image.thumbnail((2200, 2200))
        image.convert("RGB").save(path, "PNG")
    try:
        completed = subprocess.run([str(executable), str(path)], capture_output=True,
                                   timeout=30, check=False)
        if completed.returncode != 0:
            raise ValueError("本地文字识别失败，请重试或使用手工录入。")
        return json.loads(completed.stdout)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("本地文字识别超时，请裁剪图片后重试。") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("本地 OCR 不可用，请使用手工录入。") from exc
    finally:
        path.unlink(missing_ok=True)


def recognize_images(images: list[bytes], reference_year: int | None = None) -> dict:
    """Recognize up to eight screenshots synchronously with no persistent process."""
    if not 1 <= len(images) <= MAX_IMAGES or sum(map(len, images)) > MAX_TOTAL_BYTES:
        raise ValueError("每批须为 1–8 张图片，总大小不超过 32MB。")
    if reference_year is not None and (isinstance(reference_year, bool) or not 2000 <= reference_year <= 2100):
        raise ValueError("年份须在 2000–2100 之间。")
    for content in images:
        _validate_image(content)
    if not _OCR_LOCK.acquire(blocking=False):
        raise ValueError("已有截图正在识别，请稍后重试。")
    cache_lock = None
    try:
        cache = Path.home() / "Library" / "Caches" / "EvoAlpha" / "portfolio-ocr"
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_lock = (cache / "recognizer.lock").open("a")
        try:
            fcntl.flock(cache_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("已有截图正在识别，请稍后重试。") from exc
        digest = hashlib.sha256(_SOURCE.read_bytes()).hexdigest()
        executable = cache / "recognizer"
        stamp = cache / "source.sha256"
        if not executable.exists() or not stamp.exists() or stamp.read_text() != digest:
            candidate = cache / "recognizer.pending"
            try:
                compiled = subprocess.run(["/usr/bin/swiftc", "-O", str(_SOURCE), "-o", str(candidate)],
                                          capture_output=True, timeout=60, check=False)
                if compiled.returncode != 0:
                    raise ValueError("本地 OCR 编译失败；请确认已安装 macOS 命令行开发工具。")
                candidate.replace(executable)
                stamp.write_text(digest)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError("本地 OCR 编译不可用或超时，请使用手工录入。") from exc
            finally:
                candidate.unlink(missing_ok=True)
        rows, warnings, hashes = [], [], []
        with tempfile.TemporaryDirectory(prefix="evoalpha-ocr-") as temporary:
            for index, content in enumerate(images):
                digest = hashlib.sha256(content).hexdigest()
                hashes.append(digest)
                # Exact image duplicates are safe to skip; repeated text rows remain reviewable.
                if digest in hashes[:-1]:
                    warnings.append(f"第 {index + 1} 张图片与前图完全相同，已跳过识别。")
                    continue
                result = parse_lines(_recognize(content, Path(temporary), executable, index), index, reference_year)
                rows.extend(result["rows"])
                warnings.extend(f"第 {index + 1} 张：{warning}" for warning in result["warnings"])
                if len(rows) > MAX_ROWS:
                    raise ValueError("单批超过 200 条记录，请拆分上传。")
        return {"rows": rows, "warnings": warnings, "source_hashes": hashes}
    finally:
        if cache_lock is not None:
            cache_lock.close()
        _OCR_LOCK.release()
