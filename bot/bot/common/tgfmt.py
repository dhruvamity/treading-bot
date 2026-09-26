"""The Telegram message style (the owner's templates, 2026-09-26): one emoji and a bold title, a blank line, then short
lines in groups separated by blank lines; facts as monospace lines, bold labels over sections, at most one emoji a
message. Output is Telegram HTML (<b>, <i>, <code>, <pre>): no Markdown signs reach the chat."""

from __future__ import annotations

from html import escape

Block = str | list[str] | None


def b(x: object) -> str:
    return f"<b>{escape(str(x))}</b>"


def code(x: object) -> str:
    return f"<code>{escape(str(x))}</code>"


def codes(*lines: object) -> list[str]:
    """Monospace lines; empty ones are left out."""
    return [code(x) for x in lines if x not in (None, "")]


def section(label: str, lines: list[str]) -> list[str]:
    """A bold label over its lines (nothing when there are no lines)."""
    return [b(label), *lines] if lines else []


def head(emoji: str, title: str) -> str:
    return f"{emoji} {b(title)}" if emoji else b(title)


def card(emoji: str, title: str, *blocks: Block) -> str:
    """The title, then each block (a line or a list of lines, already HTML) after a blank line; empty blocks are
    skipped."""
    parts = [head(emoji, title)]
    for bl in blocks:
        text = bl if isinstance(bl, str) else "\n".join(x for x in (bl or []) if x)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def split_head(line: str) -> tuple[str, str]:
    """"🟢 LIVE STARTED" -> ("🟢", "LIVE STARTED"); a line without a leading emoji -> ("", line)."""
    first, _, rest = line.strip().partition(" ")
    if first and rest and not any(ch.isalnum() for ch in first) and first[0] not in "$-+(/[<":
        return first, rest.strip()
    return "", line.strip()


def plain_card(text: str, emoji: str = "") -> str:
    """Plain text in the house layout: the first line is the title (its own leading emoji, else `emoji`), every other
    line a monospace line, and an empty line starts a new group. Used for alerts and the pilot's events, which are
    written as plain text so the logs read the same."""
    lines = text.strip("\n").split("\n")
    e, title = split_head(lines[0])
    blocks: list[list[str]] = []
    cur: list[str] = []
    for ln in lines[1:]:
        if ln.strip():
            cur.append(code(ln.strip()))
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return card(e or emoji, title, *blocks)
