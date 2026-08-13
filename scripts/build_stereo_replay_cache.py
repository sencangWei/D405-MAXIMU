#!/usr/bin/env python3
"""Build a derived DB3 containing only stereo IR images and metadata.

The source recording remains untouched.  The cache removes RGB/Depth pages
from the VINS replay path while preserving original CDR blobs, bag timestamps,
device metadata, topic IDs, and serialization descriptions.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from replay_db3_to_ros2 import select_db3


STEREO_TOPICS = (
    "/device_0/sensor_0/Infrared_1/image/data",
    "/device_0/sensor_0/Infrared_1/image/metadata",
    "/device_0/sensor_0/Infrared_2/image/data",
    "/device_0/sensor_0/Infrared_2/image/metadata",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成只含双IR的轻量回放DB3")
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--duration-s",
        type=float,
        default=0.0,
        help="只复制起始N秒；0表示完整会话",
    )
    return parser.parse_args()


def sql_placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def validate_cache(path: Path) -> dict[str, object]:
    database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    topics = {
        name: topic_id
        for topic_id, name in database.execute("SELECT id, name FROM topics")
    }
    missing = sorted(set(STEREO_TOPICS) - topics.keys())
    counts = {
        name: int(
            database.execute(
                "SELECT count(*) FROM messages WHERE topic_id = ?",
                (topics[name],),
            ).fetchone()[0]
        )
        for name in STEREO_TOPICS
        if name in topics
    }
    timestamp_bounds = database.execute(
        "SELECT min(timestamp), max(timestamp) FROM messages"
    ).fetchone()
    database.close()
    if missing or not counts or min(counts.values()) <= 0:
        raise RuntimeError(
            "invalid stereo cache: missing=" + ",".join(missing)
        )
    return {
        "topics": counts,
        "first_bag_timestamp_ns": timestamp_bounds[0],
        "last_bag_timestamp_ns": timestamp_bounds[1],
    }


def build_cache(
    source: Path, output: Path, duration_s: float = 0.0
) -> dict[str, object]:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    database = sqlite3.connect(str(output))
    try:
        database.execute("PRAGMA journal_mode=OFF")
        database.execute("PRAGMA synchronous=OFF")
        database.execute(
            "CREATE TABLE topics("
            "id INTEGER PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL,"
            "serialization_format TEXT NOT NULL,offered_qos_profiles TEXT NOT NULL)"
        )
        database.execute(
            "CREATE TABLE messages("
            "id INTEGER PRIMARY KEY,topic_id INTEGER NOT NULL,"
            "timestamp INTEGER NOT NULL,data BLOB NOT NULL)"
        )
        source_uri = source.resolve().as_uri() + "?mode=ro"
        database.execute("ATTACH DATABASE ? AS source", (source_uri,))
        placeholders = sql_placeholders(len(STEREO_TOPICS))
        topic_ids = [
            row[0]
            for row in database.execute(
                f"SELECT id FROM source.topics WHERE name IN ({placeholders})",
                STEREO_TOPICS,
            )
        ]
        if len(topic_ids) != len(STEREO_TOPICS):
            raise RuntimeError("source DB3 does not contain all stereo topics")
        id_placeholders = sql_placeholders(len(topic_ids))
        first_timestamp = int(
            database.execute(
                f"SELECT min(timestamp) FROM source.messages "
                f"WHERE topic_id IN ({id_placeholders})",
                topic_ids,
            ).fetchone()[0]
        )
        last_timestamp = (
            first_timestamp + int(duration_s * 1e9)
            if duration_s > 0.0
            else None
        )
        database.execute(
            f"INSERT INTO topics SELECT * FROM source.topics "
            f"WHERE id IN ({id_placeholders})",
            topic_ids,
        )
        if last_timestamp is None:
            database.execute(
                f"INSERT INTO messages SELECT * FROM source.messages "
                f"WHERE topic_id IN ({id_placeholders}) ORDER BY id",
                topic_ids,
            )
        else:
            database.execute(
                f"INSERT INTO messages SELECT * FROM source.messages "
                f"WHERE topic_id IN ({id_placeholders}) AND timestamp <= ? ORDER BY id",
                [*topic_ids, last_timestamp],
            )
        database.execute("CREATE INDEX timestamp_idx ON messages(timestamp ASC)")
        database.commit()
    except Exception:
        database.close()
        output.unlink(missing_ok=True)
        raise
    database.close()
    validation = validate_cache(output)
    report = {
        "result": "PASS",
        "source": str(source),
        "output": str(output.resolve()),
        "source_size_bytes": source.stat().st_size,
        "cache_size_bytes": output.stat().st_size,
        "duration_limit_s": duration_s if duration_s > 0.0 else None,
        "build_duration_s": time.monotonic() - started,
        **validation,
    }
    output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    args = parse_args()
    session = args.session.resolve()
    source = select_db3(session)
    if args.duration_s < 0.0:
        raise ValueError("duration must not be negative")
    report = build_cache(source, args.output.resolve(), args.duration_s)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
