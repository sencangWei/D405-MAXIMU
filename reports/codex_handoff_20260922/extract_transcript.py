#!/usr/bin/env python3
"""把 Claude Code 的 jsonl 会话记录提取成人可读的交接材料。

为什么不直接给 jsonl：单条会话 121MB / 39144 行，绝大部分是工具结果与
system-reminder，codex 读不动也没用。这里只留三类：
  user_turns.md         —— 真人输入的每一句（**意图的权威记录**，逐字）
  assistant_text.md     —— 助手的文字回合（结论/判断），工具调用不入
  commands.log          —— 跑过的每条 Bash 命令（可复现性）

用法: extract_transcript.py <jsonl> <输出目录> [--full]
"""
import json
import sys
from pathlib import Path


def blocks(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return c if isinstance(c, list) else []


def main():
    src, outdir = Path(sys.argv[1]), Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)

    users, assists, cmds = [], [], []
    for line in src.open(encoding="utf-8"):
        if '"type":"queue-operation"' in line[:64] or not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        t, ts = r.get("type"), (r.get("timestamp") or "")[:19]
        if r.get("isSidechain"):
            continue  # 子 agent 的回合不算主线
        msg = r.get("message") or {}

        if t == "user":
            # 只收真人打字；工具结果（tool_result）与系统提醒一律丢
            if (r.get("origin") or {}).get("kind") != "human":
                continue
            txt = "\n".join(b.get("text", "") for b in blocks(msg) if b.get("type") == "text")
            if txt.strip():
                users.append((ts, txt.strip()))

        elif t == "assistant":
            parts = []
            for b in blocks(msg):
                if b.get("type") == "text" and b.get("text", "").strip():
                    parts.append(b["text"].strip())
                elif b.get("type") == "tool_use" and b.get("name") == "Bash":
                    cmd = (b.get("input") or {}).get("command", "")
                    if cmd.strip():
                        cmds.append((ts, cmd.strip()))
            if parts:
                assists.append((ts, "\n\n".join(parts)))

    (outdir / "user_turns.md").write_text(
        "# 用户原话（逐字，按时间）\n\n"
        "> 这是本次长期任务的**意图权威记录**。凡是与别处摘要冲突的，以本文件为准。\n\n"
        + "\n\n".join(f"## [{ts}] #{i+1}\n\n{txt}" for i, (ts, txt) in enumerate(users)),
        encoding="utf-8")
    (outdir / "assistant_text.md").write_text(
        "# 助手文字回合（结论与判断；工具调用不入）\n\n"
        + "\n\n".join(f"### [{ts}] #{i+1}\n\n{txt}" for i, (ts, txt) in enumerate(assists)),
        encoding="utf-8")
    (outdir / "commands.log").write_text(
        "# 跑过的 Bash 命令（按时间）—— 复现用\n\n"
        + "\n\n".join(f"# [{ts}] #{i+1}\n{cmd}" for i, (ts, cmd) in enumerate(cmds)),
        encoding="utf-8")

    for f in ("user_turns.md", "assistant_text.md", "commands.log"):
        p = outdir / f
        print(f"  {f:<22} {p.stat().st_size/1024:9.1f} KB")
    print(f"  用户回合 {len(users)}  助手回合 {len(assists)}  命令 {len(cmds)}")


if __name__ == "__main__":
    main()