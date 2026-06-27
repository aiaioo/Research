# Debugging Learnings

## Python `\n` in a JS string inside a template literal

**Date:** 2026-06-27

### What happened

A Flask app embeds a `<script>` block inside a Python string (`TEMPLATE = "..."`).
Inside that script, a `confirm()` dialog was written as:

```python
if (!confirm('Delete this paper?\n\nAre you sure?')) return;
```

Python interprets `\n` as a real newline character. When the template is rendered to
HTML, the script block contains:

```javascript
if (!confirm('Delete this paper?
              
Are you sure?')) return;
```

A JavaScript single- or double-quoted string literal **cannot span multiple lines**.
The browser's JS parser sees a syntax error and **aborts the entire `<script>` block**.

### Why it was hard to spot

- The source code looks fine — `\n` is a normal, readable escape sequence.
- The symptom was not "delete button broken" but rather **unrelated features** (filter
  checkboxes, bookmark persistence via `fetch`) silently stopped working.
- All those features depended on event listeners registered later in the same script
  block. Because the parser never got that far, none of them were attached.
- The search form still worked because it uses native HTML form submission — no JS
  event listener needed.

### How it was found

Rendering the page via Flask's test client and printing the raw `<script>` block:

```python
html = app.test_client().get('/').data.decode('utf-8')
start = html.rfind('<script>')
end   = html.rfind('</script>')
print(html[start:end+9])
```

The literal newline inside the string literal was immediately visible in the output.

### Fix

Use `\\n` in the Python string so it renders as the JS escape sequence `\n` (not a
literal newline):

```python
if (!confirm('Delete this paper?\\n\\nAre you sure?')) return;
```

### General rules

| Goal | Write in Python template string |
|------|---------------------------------|
| JS newline escape `\n` | `\\n` |
| JS tab escape `\t` | `\\t` |
| JS backslash `\\` | `\\\\` |

### Broader lesson

When debugging "multiple unrelated features broken at once" in a web app, check
whether all the broken features share a single JS entry point. A parse error anywhere
in a `<script>` block silently kills everything that follows it — it does not throw a
visible page error, and the features that break may look completely unrelated to the
line that actually failed.

Always inspect **rendered output**, not just source code, when hunting JS bugs in
server-side templates.
