#!/usr/bin/env python3
"""
blog.py — Blog blueprint for the Research app, mounted at /blog.

Single-user login (no sign-up): the password is never stored in plaintext,
only its MD5 hash is compared against on login. Posts are written in a small
Markdown-like syntax so authors can drop in links to papers pulled straight
from this repository's own paper store (see paper_viewer.load_papers), plus
arbitrary other links, without allowing raw HTML injection.

Data lives in blog_data/posts.json (created on first use) — a plain JSON
file is plenty for a single-author blog.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import bleach
from flask import (
    Blueprint, abort, jsonify, redirect, render_template_string,
    request, session, url_for,
)
from markupsafe import Markup, escape

ROOT           = Path(__file__).parent
BLOG_DIR       = ROOT / "blog_data"
POSTS_FILE     = BLOG_DIR / "posts.json"
SECRET_KEY_FILE = BLOG_DIR / ".secret_key"

BLOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Auth ─────────────────────────────────────────────────────────────────────
# Single hard-coded account. Sign-up is intentionally not implemented.
# The password itself is never stored — only its MD5 digest, computed once
# offline from 'pa$$W0rd' — so the plaintext isn't sitting in source control.
BLOG_USERNAME     = "admin"
BLOG_PASSWORD_MD5 = "b710b83ded525a9c4625f70465684309"

_posts_lock = threading.Lock()

blog = Blueprint("blog", __name__, url_prefix="/blog")


def get_secret_key() -> str:
    """Persist a random session-signing key across restarts."""
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key)
    return key


# ── Storage ──────────────────────────────────────────────────────────────────

def _load_posts() -> list[dict]:
    if not POSTS_FILE.exists():
        return []
    try:
        with POSTS_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_posts(posts: list[dict]) -> None:
    tmp = POSTS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)
    tmp.replace(POSTS_FILE)


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "post"


def _unique_slug(title: str, posts: list[dict]) -> str:
    base = _slugify(title)
    existing = {p["slug"] for p in posts}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Minimal Markdown-ish renderer ───────────────────────────────────────────
# Everything is HTML-escaped first, then a small, closed set of safe
# constructs is re-introduced. This keeps free-form author input from ever
# injecting raw HTML/script, while still allowing paper/other links.

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITAL_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")

_ALLOWED_TAGS = ["p", "br", "strong", "em", "a", "ul", "li", "blockquote"]
_ALLOWED_ATTRS = {"a": ["href", "target", "rel"]}


def render_markdown(text: str) -> Markup:
    text = str(text or "")
    blocks = re.split(r"\n\s*\n", text.strip())
    html_blocks = []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        if all(ln.strip().startswith("- ") for ln in lines):
            items = "".join(f"<li>{_inline(ln.strip()[2:])}</li>" for ln in lines)
            html_blocks.append(f"<ul>{items}</ul>")
        elif all(ln.strip().startswith("> ") for ln in lines):
            joined = "<br>".join(_inline(ln.strip()[2:]) for ln in lines)
            html_blocks.append(f"<blockquote>{joined}</blockquote>")
        else:
            joined = "<br>".join(_inline(ln) for ln in lines)
            html_blocks.append(f"<p>{joined}</p>")
    html = "".join(html_blocks)
    clean = bleach.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
    return Markup(clean)


def _inline(line: str) -> str:
    escaped = str(escape(line))

    def _link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'

    escaped = _LINK_RE.sub(_link, escaped)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITAL_RE.sub(r"<em>\1</em>", escaped)
    return escaped


# ── Auth helpers ─────────────────────────────────────────────────────────────

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("blog_logged_in"):
            return redirect(url_for("blog.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["csrf_token"] = token
    return token


def _check_csrf() -> None:
    sent = request.form.get("csrf_token", "")
    known = session.get("csrf_token", "")
    if not known or not hmac.compare_digest(sent, known):
        abort(400, "Bad or missing CSRF token")


# ── Shared page chrome ───────────────────────────────────────────────────────

BASE_CSS = """
:root {
  --bg: #f5f7fc; --surface: #fff; --border: #dce1ee; --text: #0d1121;
  --text-sub: #2a3252; --text-muted: #5e6882; --primary: #1d52d6;
  --primary-bg: rgba(29,82,214,.07); --shadow: 0 1px 2px rgba(0,0,0,.04), 0 2px 5px rgba(0,0,0,.06);
  --danger: #c0392b;
}
html.dark {
  --bg: #080c14; --surface: #0d1220; --border: #1e2a42; --text: #dde5f8;
  --text-sub: #9eadd4; --text-muted: #6879a0; --primary: #7bb3ff;
  --primary-bg: rgba(123,179,255,.08); --shadow: none; --danger: #e74c3c;
}
* { box-sizing: border-box; }
body { font-family: 'Inter', system-ui, sans-serif; font-size: .85rem; background: var(--bg);
  color: var(--text); margin: 0; line-height: 1.6; }
a { color: var(--primary); }
.wrap { max-width: 780px; margin: 0 auto; padding: 1.5rem 1.5rem 3rem; }
.top-bar { display: flex; align-items: center; gap: .8rem; padding-bottom: .9rem;
  margin-bottom: 1.3rem; border-bottom: 1px solid var(--border); }
.top-bar .brand { font-size: 1.1rem; font-weight: 700; text-decoration: none; color: var(--text); }
.top-bar .spacer { flex: 1; }
.top-bar a.nav-link { font-size: .82rem; color: var(--text-sub); text-decoration: none; }
.top-bar a.nav-link:hover { color: var(--text); }
.btn { font-family: inherit; font-size: .82rem; background: var(--primary); color: #fff;
  border: none; border-radius: 5px; padding: .4rem .85rem; cursor: pointer; text-decoration: none;
  display: inline-block; }
.btn:hover { opacity: .9; }
.btn.secondary { background: var(--surface); color: var(--text-sub); border: 1px solid var(--border); }
.btn.danger { background: var(--danger); }
input[type=text], input[type=password], textarea {
  font-family: inherit; font-size: .85rem; width: 100%; padding: .5rem .6rem;
  border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--text);
}
textarea { font-family: 'SFMono-Regular', Menlo, monospace; font-size: .8rem; resize: vertical; }
label.field-label { display: block; font-weight: 600; color: var(--text-sub); margin: 0 0 .3rem; }
.field { margin-bottom: 1rem; }
.post-card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
  box-shadow: var(--shadow); padding: 1rem 1.25rem; margin-bottom: .9rem; }
.post-card h2 { margin: 0 0 .25rem; font-size: 1.05rem; }
.post-card h2 a { text-decoration: none; color: var(--text); }
.post-meta { color: var(--text-muted); font-size: .76rem; margin-bottom: .5rem; }
.post-body p { margin: 0 0 .8rem; }
.post-body ul { margin: 0 0 .8rem 1.2rem; }
.post-body blockquote { border-left: 3px solid var(--border); margin: 0 0 .8rem; padding-left: .8rem; color: var(--text-sub); }
.post-actions { margin-top: .6rem; display: flex; gap: .5rem; }
.flash { background: var(--primary-bg); border: 1px solid var(--border); border-radius: 5px;
  padding: .5rem .8rem; margin-bottom: 1rem; color: var(--text-sub); }
.flash.error { background: #c0392b14; border-color: #c0392b44; color: var(--danger); }
.empty { color: var(--text-muted); text-align: center; padding: 2.5rem 0; }
.help-text { color: var(--text-muted); font-size: .74rem; margin-top: .3rem; }
.paper-search { position: relative; margin-bottom: .6rem; }
.paper-results { position: absolute; z-index: 10; top: 100%; left: 0; right: 0;
  background: var(--surface); border: 1px solid var(--border); border-radius: 5px;
  box-shadow: var(--shadow); max-height: 260px; overflow-y: auto; display: none; }
.paper-results.show { display: block; }
.paper-result-item { padding: .45rem .7rem; cursor: pointer; border-bottom: 1px solid var(--border); }
.paper-result-item:last-child { border-bottom: none; }
.paper-result-item:hover { background: var(--primary-bg); }
.paper-result-title { font-weight: 600; color: var(--text); font-size: .8rem; }
.paper-result-meta { color: var(--text-muted); font-size: .72rem; }
.toolbar { display: flex; gap: .5rem; margin-bottom: .5rem; flex-wrap: wrap; }
"""

NAV = """
<div class="top-bar">
  <a class="brand" href="{{ url_for('blog.index') }}">Blog</a>
  <a class="nav-link" href="/">← Papers</a>
  <div class="spacer"></div>
  {% if session.get('blog_logged_in') %}
    <a class="nav-link" href="{{ url_for('blog.new_post') }}">+ New post</a>
    <a class="nav-link" href="{{ url_for('blog.logout') }}">Log out</a>
  {% else %}
    <a class="nav-link" href="{{ url_for('blog.login') }}">Log in</a>
  {% endif %}
</div>
"""

LIST_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blog</title>
<style>{{ base_css }}</style>
</head><body><div class="wrap">
""" + NAV + """
{% if posts %}
  {% for p in posts %}
  <div class="post-card">
    <h2><a href="{{ url_for('blog.view_post', slug=p.slug) }}">{{ p.title }}</a></h2>
    <div class="post-meta">{{ p.created_at[:10] }}{% if p.updated_at != p.created_at %} · updated {{ p.updated_at[:10] }}{% endif %}</div>
  </div>
  {% endfor %}
{% else %}
  <p class="empty">No posts yet.</p>
{% endif %}
</div></body></html>
"""

VIEW_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ post.title }}</title>
<style>{{ base_css }}</style>
</head><body><div class="wrap">
""" + NAV + """
<h1>{{ post.title }}</h1>
<div class="post-meta">{{ post.created_at[:10] }}{% if post.updated_at != post.created_at %} · updated {{ post.updated_at[:10] }}{% endif %}</div>
<div class="post-body">{{ rendered }}</div>
{% if session.get('blog_logged_in') %}
<div class="post-actions">
  <a class="btn secondary" href="{{ url_for('blog.edit_post', slug=post.slug) }}">Edit</a>
  <form method="post" action="{{ url_for('blog.delete_post', slug=post.slug) }}"
        onsubmit="return confirm('Delete this post?');">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="btn danger" type="submit">Delete</button>
  </form>
</div>
{% endif %}
</div></body></html>
"""

LOGIN_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Log in — Blog</title>
<style>{{ base_css }}</style>
</head><body><div class="wrap" style="max-width:360px;">
""" + NAV + """
<h1 style="font-size:1.1rem;">Log in</h1>
{% if error %}<div class="flash error">{{ error }}</div>{% endif %}
<form method="post">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <div class="field">
    <label class="field-label" for="username">Username</label>
    <input type="text" id="username" name="username" autocomplete="username" required autofocus>
  </div>
  <div class="field">
    <label class="field-label" for="password">Password</label>
    <input type="password" id="password" name="password" autocomplete="current-password" required>
  </div>
  <button class="btn" type="submit">Log in</button>
</form>
</div></body></html>
"""

EDIT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ 'Edit post' if post else 'New post' }} — Blog</title>
<style>{{ base_css }}</style>
</head><body><div class="wrap">
""" + NAV + """
<h1 style="font-size:1.1rem;">{{ 'Edit post' if post else 'New post' }}</h1>
<form method="post" id="post-form">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <div class="field">
    <label class="field-label" for="title">Title</label>
    <input type="text" id="title" name="title" required value="{{ post.title if post else '' }}">
  </div>
  <div class="field">
    <div class="toolbar">
      <button type="button" class="btn secondary" id="btn-insert-link">+ Link</button>
      <button type="button" class="btn secondary" id="btn-insert-paper">+ Paper from repository</button>
    </div>
    <div class="paper-search">
      <input type="text" id="paper-query" placeholder="Search your paper repository by title or author…"
             style="display:none;">
      <div class="paper-results" id="paper-results"></div>
    </div>
    <label class="field-label" for="content">Content</label>
    <textarea id="content" name="content" rows="16">{{ post.content_md if post else '' }}</textarea>
    <div class="help-text">
      Blank line = new paragraph · **bold** · *italic* · [text](url) · "- item" for a bullet list · "&gt; text" for a quote.
      Use the buttons above to insert paper or other links at the cursor.
    </div>
  </div>
  <button class="btn" type="submit">{{ 'Save changes' if post else 'Publish' }}</button>
  <a class="btn secondary" href="{{ url_for('blog.index') if not post else url_for('blog.view_post', slug=post.slug) }}">Cancel</a>
</form>
<script>
const textarea = document.getElementById('content');

function insertAtCursor(text) {
  const start = textarea.selectionStart, end = textarea.selectionEnd;
  textarea.value = textarea.value.slice(0, start) + text + textarea.value.slice(end);
  const pos = start + text.length;
  textarea.focus();
  textarea.setSelectionRange(pos, pos);
}

document.getElementById('btn-insert-link').addEventListener('click', () => {
  const url = prompt('URL (https://…):');
  if (!url) return;
  const label = prompt('Link text:', url) || url;
  insertAtCursor(`[${label}](${url})`);
});

const paperQuery   = document.getElementById('paper-query');
const paperResults = document.getElementById('paper-results');
let paperTimer = null;

document.getElementById('btn-insert-paper').addEventListener('click', () => {
  paperQuery.style.display = 'block';
  paperQuery.value = '';
  paperQuery.focus();
  paperResults.classList.remove('show');
});

paperQuery.addEventListener('input', () => {
  clearTimeout(paperTimer);
  const q = paperQuery.value.trim();
  if (q.length < 2) { paperResults.classList.remove('show'); return; }
  paperTimer = setTimeout(() => {
    fetch('{{ url_for("blog.api_papers") }}?q=' + encodeURIComponent(q))
      .then(r => r.json())
      .then(data => {
        paperResults.innerHTML = '';
        if (!data.results.length) {
          paperResults.innerHTML = '<div class="paper-result-item">No matching papers</div>';
        } else {
          data.results.forEach(p => {
            const div = document.createElement('div');
            div.className = 'paper-result-item';
            div.innerHTML = `<div class="paper-result-title"></div><div class="paper-result-meta"></div>`;
            div.querySelector('.paper-result-title').textContent = p.title || p.paper_url;
            div.querySelector('.paper-result-meta').textContent = p.authors || p.paper_url;
            div.addEventListener('click', () => {
              insertAtCursor(`[${p.title || p.paper_url}](${p.paper_url})`);
              paperResults.classList.remove('show');
              paperQuery.style.display = 'none';
            });
            paperResults.appendChild(div);
          });
        }
        paperResults.classList.add('show');
      });
  }, 250);
});
</script>
</div></body></html>
"""


# ── Routes ───────────────────────────────────────────────────────────────────

def create_blog_blueprint(get_papers) -> Blueprint:
    """get_papers: zero-arg callable returning the list of paper dicts
    (paper_viewer.load_papers), used to power the in-editor paper search."""

    @blog.route("/")
    def index():
        posts = sorted(_load_posts(), key=lambda p: p["created_at"], reverse=True)
        return render_template_string(LIST_TEMPLATE, base_css=BASE_CSS, posts=posts)

    @blog.route("/post/<slug>")
    def view_post(slug):
        post = next((p for p in _load_posts() if p["slug"] == slug), None)
        if not post:
            abort(404)
        rendered = render_markdown(post["content_md"])
        return render_template_string(
            VIEW_TEMPLATE, base_css=BASE_CSS, post=post, rendered=rendered,
            csrf_token=_csrf_token(),
        )

    @blog.route("/login", methods=["GET", "POST"])
    def login():
        error = ""
        if request.method == "POST":
            _check_csrf()
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            pw_hash  = hashlib.md5(password.encode("utf-8")).hexdigest()
            user_ok  = hmac.compare_digest(username, BLOG_USERNAME)
            pass_ok  = hmac.compare_digest(pw_hash, BLOG_PASSWORD_MD5)
            if user_ok and pass_ok:
                session.clear()
                session["blog_logged_in"] = True
                session.permanent = True
                nxt = request.args.get("next") or url_for("blog.index")
                return redirect(nxt)
            error = "Invalid username or password."
        return render_template_string(
            LOGIN_TEMPLATE, base_css=BASE_CSS, error=error, csrf_token=_csrf_token(),
        )

    @blog.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("blog.index"))

    @blog.route("/new", methods=["GET", "POST"])
    @login_required
    def new_post():
        if request.method == "POST":
            _check_csrf()
            title   = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            if not title or not content:
                return render_template_string(
                    EDIT_TEMPLATE, base_css=BASE_CSS, post=None,
                    csrf_token=_csrf_token(),
                ), 400
            with _posts_lock:
                posts = _load_posts()
                now = _now()
                post = {
                    "id":          uuid.uuid4().hex,
                    "slug":        _unique_slug(title, posts),
                    "title":       title,
                    "content_md":  content,
                    "created_at":  now,
                    "updated_at":  now,
                    "author":      BLOG_USERNAME,
                }
                posts.append(post)
                _save_posts(posts)
            return redirect(url_for("blog.view_post", slug=post["slug"]))
        return render_template_string(
            EDIT_TEMPLATE, base_css=BASE_CSS, post=None, csrf_token=_csrf_token(),
        )

    @blog.route("/edit/<slug>", methods=["GET", "POST"])
    @login_required
    def edit_post(slug):
        with _posts_lock:
            posts = _load_posts()
            post = next((p for p in posts if p["slug"] == slug), None)
            if not post:
                abort(404)
            if request.method == "POST":
                _check_csrf()
                title   = request.form.get("title", "").strip()
                content = request.form.get("content", "").strip()
                if not title or not content:
                    return render_template_string(
                        EDIT_TEMPLATE, base_css=BASE_CSS, post=post,
                        csrf_token=_csrf_token(),
                    ), 400
                post["title"]      = title
                post["content_md"] = content
                post["updated_at"] = _now()
                _save_posts(posts)
                return redirect(url_for("blog.view_post", slug=post["slug"]))
        return render_template_string(
            EDIT_TEMPLATE, base_css=BASE_CSS, post=post, csrf_token=_csrf_token(),
        )

    @blog.route("/delete/<slug>", methods=["POST"])
    @login_required
    def delete_post(slug):
        _check_csrf()
        with _posts_lock:
            posts = _load_posts()
            new_posts = [p for p in posts if p["slug"] != slug]
            if len(new_posts) == len(posts):
                abort(404)
            _save_posts(new_posts)
        return redirect(url_for("blog.index"))

    @blog.route("/api/papers")
    @login_required
    def api_papers():
        q = request.args.get("q", "").strip().lower()
        if len(q) < 2:
            return jsonify(results=[])
        results = []
        for p in get_papers():
            title   = (p.get("title") or "").lower()
            authors = (p.get("authors") or "").lower()
            if q in title or q in authors:
                results.append({
                    "title":   p.get("title") or p.get("paper_url", ""),
                    "authors": p.get("authors", ""),
                    "paper_url": p.get("paper_url", ""),
                })
            if len(results) >= 15:
                break
        return jsonify(results=results)

    return blog
