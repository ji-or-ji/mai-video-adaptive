"""时间轴落盘与检索。

设计口径（见 README「便宜的常驻，贵的按需」）：

- **摘要**进上下文，固定上限，每轮都付
- **完整时间轴**落盘，只在需要细节时查

签名可能很长（一组帧指纹），所以用 sha1 前 16 位做短标识，
注入上下文里就是 `视频#a3f2b1c9d4e5f6a7` 这样的引用。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

__all__ = ["sig_key", "TimelineStore"]


def sig_key(sig) -> str:
    """视频签名 → 短标识（16 位十六进制）。"""
    blob = json.dumps(sig, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


class TimelineStore:
    """时间轴存储。

    data_dir/
      timeline/<key>.json        # 完整时间轴（分段明细）
      timeline_index.json        # 索引：key → 摘要 + 群 + 消息 id（常驻那份）
    """

    def __init__(self, data_dir, *, ttl_days: float = 7.0,
                 max_entries: int = 200):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "timeline"
        self.index_path = self.data_dir / "timeline_index.json"
        self.ttl = max(0.0, float(ttl_days)) * 86400
        self.max_entries = max(1, int(max_entries))

    # ---- 写 ----

    def save(self, key: str, *, segments, summary: str, duration: float = 0.0,
             group_id: str = "", message_id: str = "", video_kept: bool = False,
             file_name: str = "", now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self.root.mkdir(parents=True, exist_ok=True)
        record = {"key": key, "duration": float(duration or 0.0),
                  "created_at": now, "segments": segments or [],
                  "group_id": str(group_id or ""),
                  "message_id": str(message_id or ""),
                  "file_name": str(file_name or ""),
                  "video_kept": bool(video_kept)}
        (self.root / f"{key}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")

        idx = self._load_index()
        idx[key] = {"group_id": record["group_id"],
                    "message_id": record["message_id"],
                    "file_name": record["file_name"],
                    "created_at": now,
                    "duration": record["duration"],
                    "summary": summary or "",
                    "video_kept": record["video_kept"]}
        self._save_index(idx)
        self.purge(now=now)
        return idx[key]

    # ---- 读 ----

    def load(self, key: str) -> dict | None:
        """取完整时间轴（含 segments）。"""
        p = self.root / f"{key}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def summary(self, key: str) -> str:
        """取常驻那份摘要。"""
        return str((self._load_index().get(key) or {}).get("summary") or "")

    def by_message(self, message_id: str) -> str | None:
        """按被引用消息 id 反查视频标识。

        用户引用某条消息提问时，reply 段给的是消息 id，走这里定位。
        """
        mid = str(message_id or "")
        if not mid:
            return None
        for key, meta in self._load_index().items():
            if str(meta.get("message_id") or "") == mid:
                return key
        return None

    def by_filename(self, file_name: str) -> str | None:
        """按视频文件名反查标识。

        用途：cleanup_after 会把原片删掉，同一条视频被转发/引用再来时
        取回目录已无文件；此时按文件名找回已有时间轴直接复用。
        """
        name = str(file_name or "").strip()
        if not name:
            return None
        for key, meta in self._load_index().items():
            if str(meta.get("file_name") or "") == name:
                return key
        return None

    def recent(self, group_id: str | None = None, limit: int = 1) -> list:
        """最近若干条，可按群过滤。"""
        idx = self._load_index()
        items = [(k, v) for k, v in idx.items()
                 if group_id is None
                 or str(v.get("group_id") or "") == str(group_id)]
        items.sort(key=lambda kv: kv[1].get("created_at", 0), reverse=True)
        return [{"key": k, **v} for k, v in items[:max(1, int(limit))]]

    def find_segments(self, key: str, start: float, end: float) -> list:
        """取某时间区间内的分段（按需查细节走这里）。"""
        rec = self.load(key)
        if not rec:
            return []
        lo, hi = float(start), float(end)
        return [s for s in (rec.get("segments") or [])
                if float(s.get("end", 0)) >= lo and float(s.get("start", 0)) <= hi]

    # ---- 淘汰 ----

    def purge(self, now: float | None = None) -> int:
        """按 TTL 与条数上限清理，返回清理条数。"""
        now = time.time() if now is None else now
        idx = self._load_index()
        removed = 0

        if self.ttl > 0:
            for key, meta in list(idx.items()):
                if now - float(meta.get("created_at") or 0) > self.ttl:
                    idx.pop(key, None)
                    self._drop_file(key)
                    removed += 1

        if len(idx) > self.max_entries:
            order = sorted(idx.items(), key=lambda kv: kv[1].get("created_at", 0))
            for key, _ in order[:len(idx) - self.max_entries]:
                idx.pop(key, None)
                self._drop_file(key)
                removed += 1

        if removed:
            self._save_index(idx)
        return removed

    # ---- 内部 ----

    def _drop_file(self, key: str):
        try:
            (self.root / f"{key}.json").unlink(missing_ok=True)
        except Exception:
            pass

    def _load_index(self) -> dict:
        if self.index_path.exists():
            try:
                return json.loads(self.index_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_index(self, idx: dict):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(idx, ensure_ascii=False),
                                   encoding="utf-8")
