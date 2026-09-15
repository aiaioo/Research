#!/usr/bin/env python3
"""
comments.py — Per-paper comment storage for the Research app.

Comments are either typed directly by the (single, logged-in) site owner, or
generated automatically when a blog post links to a paper already in the
library — those show up as a comment pointing back to the post, so a reader
browsing the paper list can see it was discussed on the blog.

Data lives in blog_data/comments.json, next to the blog posts store.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT          = Path(__file__).parent
BLOG_DIR      = ROOT / "blog_data"
COMMENTS_FILE = BLOG_DIR / "comments.json"
BLOG_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()

# Same "[label](url)" syntax blog posts use for links (see blog._LINK_RE) —
# duplicated here rather than imported so this module has no dependency on blog.py.
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")

try:
    from paper_searcher import ARXIV_RE, OPENREVIEW_RE, ACL_RE, HF_PAPER_RE, arxiv_canonical
    _NORMALISE_AVAILABLE = True
except Exception:
    _NORMALISE_AVAILABLE = False

_ARXIV_HTML_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?")


def normalise_paper_url(url: str) -> str:
    """Best-effort canonicalisation so a manually-pasted arXiv/HF/OpenReview/ACL
    link in a blog post still matches the paper's stored paper_url even if the
    author used a different (but equivalent) URL form."""
    if not _NORMALISE_AVAILABLE:
        return url
    m = _ARXIV_HTML_RE.search(url)
    if m:
        return arxiv_canonical(m.group(1))
    m = HF_PAPER_RE.search(url)
    if m:
        return arxiv_canonical(m.group(1))
    m = OPENREVIEW_RE.search(url)
    if m:
        return f"https://openreview.net/forum?id={m.group(1)}"
    m = ACL_RE.search(url)
    if m:
        return f"https://aclanthology.org/{m.group(1)}"
    return url


# ── Storage ──────────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    if not COMMENTS_FILE.exists():
        return []
    try:
        with COMMENTS_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(comments: list[dict]) -> None:
    tmp = COMMENTS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(comments, f, indent=2, ensure_ascii=False)
    tmp.replace(COMMENTS_FILE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Queries ──────────────────────────────────────────────────────────────────

def grouped_by_paper() -> dict[str, list[dict]]:
    """All comments, grouped by paper_url and sorted oldest-first. One file
    read for the whole page instead of one per paper card."""
    groups: dict[str, list[dict]] = {}
    for c in _load():
        groups.setdefault(c["paper_url"], []).append(c)
    for lst in groups.values():
        lst.sort(key=lambda c: c["created_at"])
    return groups


def get_comments(paper_url: str) -> list[dict]:
    return sorted(
        (c for c in _load() if c.get("paper_url") == paper_url),
        key=lambda c: c["created_at"],
    )


# ── Mutations ────────────────────────────────────────────────────────────────

def add_comment(paper_url: str, text: str, author: str) -> dict:
    text = text.strip()
    comment = {
        "id":         uuid.uuid4().hex,
        "paper_url":  paper_url,
        "text":       text,
        "author":     author,
        "created_at": _now(),
        "source":     "user",
    }
    with _lock:
        comments = _load()
        comments.append(comment)
        _save(comments)
    return comment


def delete_comment(comment_id: str) -> bool:
    """Delete a single user-authored comment. Auto-generated blog-mention
    comments aren't deletable this way — they follow the blog post's lifecycle
    (see delete_blog_post_mentions / sync_blog_post_mentions)."""
    with _lock:
        comments = _load()
        new_comments = [
            c for c in comments
            if not (c["id"] == comment_id and c.get("source") == "user")
        ]
        if len(new_comments) == len(comments):
            return False
        _save(new_comments)
        return True


def delete_comments_for_paper(paper_url: str) -> int:
    """Remove every comment (user or blog-mention) attached to a paper —
    called when the paper itself is deleted."""
    with _lock:
        comments = _load()
        keep = [c for c in comments if c.get("paper_url") != paper_url]
        removed = len(comments) - len(keep)
        if removed:
            _save(keep)
        return removed


def sync_blog_post_mentions(post: dict, known_paper_urls: set) -> None:
    """Re-derive the auto-generated 'mentioned in blog post' comments for one
    post: drop whatever it previously produced, then add one per paper it
    currently links to that exists in the paper library. Call after creating
    or editing a post so edits (added/removed paper links) stay in sync."""
    mentioned = set()
    for _, url in _LINK_RE.findall(post.get("content_md", "")):
        if url in known_paper_urls:
            mentioned.add(url)
            continue
        canon = normalise_paper_url(url)
        if canon in known_paper_urls:
            mentioned.add(canon)

    with _lock:
        comments = _load()
        comments = [
            c for c in comments
            if not (c.get("source") == "blog" and c.get("blog_slug") == post["slug"])
        ]
        for paper_url in mentioned:
            comments.append({
                "id":         uuid.uuid4().hex,
                "paper_url":  paper_url,
                "text":       "",
                "author":     post.get("author", ""),
                "created_at": post.get("updated_at") or _now(),
                "source":     "blog",
                "blog_slug":  post["slug"],
                "blog_title": post["title"],
            })
        _save(comments)


def delete_blog_post_mentions(slug: str) -> None:
    """Called when a blog post is deleted, so its mention-comments don't
    linger on papers pointing at a now-dead post."""
    with _lock:
        comments = _load()
        keep = [
            c for c in comments
            if not (c.get("source") == "blog" and c.get("blog_slug") == slug)
        ]
        if len(keep) != len(comments):
            _save(keep)
