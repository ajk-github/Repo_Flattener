#!/usr/bin/env python3
"""
Flatten a GitHub repo into a single static HTML page for fast skimming and Ctrl+F.
"""

from __future__ import annotations
import argparse
import html
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from dataclasses import dataclass
from typing import List

# External deps
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_for_filename, TextLexer
import markdown

MAX_DEFAULT_BYTES = 50 * 1024
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav", ".ogg", ".flac",
    ".ttf", ".otf", ".eot", ".woff", ".woff2",
    ".so", ".dll", ".dylib", ".class", ".jar", ".exe", ".bin",
}
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd", ".mkdn"}

@dataclass
class RenderDecision:
    include: bool
    reason: str  # "ok" | "binary" | "too_large" | "ignored"

@dataclass
class FileInfo:
    path: pathlib.Path  # absolute path on disk
    rel: str            # path relative to repo root (slash-separated)
    size: int
    decision: RenderDecision


def run(cmd: List[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True)


def git_clone(url: str, dst: str) -> None:
    run(["git", "clone", "--depth", "1", url, dst])


def git_head_commit(repo_dir: str) -> str:
    try:
        cp = run(["git", "rev-parse", "HEAD"], cwd=repo_dir)
        return cp.stdout.strip()
    except Exception:
        return "(unknown)"


def bytes_human(n: int) -> str:
    """Human-readable bytes: 1 decimal for KiB and above, integer for B."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    f = float(n)
    i = 0
    while f >= 1024.0 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    if i == 0:
        return f"{int(f)} {units[i]}"
    else:
        return f"{f:.1f} {units[i]}"


def looks_binary(path: pathlib.Path) -> bool:
    ext = path.suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True
    try:
        with path.open("rb") as f:
            chunk = f.read(8192)
        if b"\x00" in chunk:
            return True
        # Heuristic: try UTF-8 decode; if it hard-fails, likely binary
        try:
            chunk.decode("utf-8")
        except UnicodeDecodeError:
            return True
        return False
    except Exception:
        # If unreadable, treat as binary to be safe
        return True


def decide_file(path: pathlib.Path, repo_root: pathlib.Path, max_bytes: int) -> FileInfo:
    rel = str(path.relative_to(repo_root)).replace(os.sep, "/")
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        size = 0
    # Ignore VCS and build junk
    if "/.git/" in f"/{rel}/" or rel.startswith(".git/"):
        return FileInfo(path, rel, size, RenderDecision(False, "ignored"))
    if size > max_bytes:
        return FileInfo(path, rel, size, RenderDecision(False, "too_large"))
    if looks_binary(path):
        return FileInfo(path, rel, size, RenderDecision(False, "binary"))
    return FileInfo(path, rel, size, RenderDecision(True, "ok"))


def collect_files(repo_root: pathlib.Path, max_bytes: int) -> List[FileInfo]:
    infos: List[FileInfo] = []
    for p in sorted(repo_root.rglob("*")):
        if p.is_symlink():
            continue
        if p.is_file():
            infos.append(decide_file(p, repo_root, max_bytes))
    return infos


def generate_tree_fallback(root: pathlib.Path) -> str:
    """Minimal tree-like output if `tree` command is missing."""
    lines: List[str] = []

    def walk(dir_path: pathlib.Path, prefix: str = ""):
        entries = [e for e in dir_path.iterdir() if e.name != ".git"]
        entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
        for i, e in enumerate(entries):
            last = i == len(entries) - 1
            branch = "└── " if last else "├── "
            lines.append(prefix + branch + e.name)
            if e.is_dir():
                extension = "    " if last else "│   "
                walk(e, prefix + extension)

    lines.append(root.name)
    walk(root)
    return "\n".join(lines)


def try_tree_command(root: pathlib.Path) -> str:
    try:
        cp = run(["tree", "-a", "."], cwd=str(root))
        return cp.stdout
    except Exception:
        return generate_tree_fallback(root)


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def render_markdown_text(md_text: str) -> str:
    return markdown.markdown(md_text, extensions=["fenced_code", "tables", "toc"])


def highlight_code(text: str, filename: str, formatter: HtmlFormatter) -> str:
    try:
        lexer = get_lexer_for_filename(filename, stripall=False)
    except Exception:
        lexer = TextLexer(stripall=False)
    return highlight(text, lexer, formatter)


def slugify(path_str: str) -> str:
    # Simple slug: keep alnum, dash, underscore; replace others with '-'
    out = []
    for ch in path_str:
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("-")
    return "".join(out)


def get_file_icon(ext: str) -> str:
    """Get file icon based on extension."""
    icons = {
        '.py': '🐍',
        '.js': '🟨',
        '.ts': '🔵',
        '.jsx': '⚛️',
        '.tsx': '⚛️',
        '.html': '🌐',
        '.css': '🎨',
        '.scss': '🎨',
        '.sass': '🎨',
        '.json': '📋',
        '.xml': '📋',
        '.yaml': '📋',
        '.yml': '📋',
        '.md': '📝',
        '.txt': '📄',
        '.pdf': '📕',
        '.doc': '📘',
        '.docx': '📘',
        '.xls': '📊',
        '.xlsx': '📊',
        '.csv': '📊',
        '.sql': '🗄️',
        '.sh': '⚡',
        '.bat': '⚡',
        '.ps1': '⚡',
        '.php': '🐘',
        '.rb': '💎',
        '.go': '🐹',
        '.rs': '🦀',
        '.cpp': '⚙️',
        '.c': '⚙️',
        '.h': '⚙️',
        '.java': '☕',
        '.kt': '🟣',
        '.swift': '🐦',
        '.dart': '🎯',
        '.vue': '💚',
        '.svelte': '🧡',
        '.dockerfile': '🐳',
        '.gitignore': '📋',
        '.env': '🔐',
        '.lock': '🔒',
        '.log': '📜',
    }
    return icons.get(ext.lower(), '📄')


def generate_cxml_text(infos: List[FileInfo], repo_dir: pathlib.Path) -> str:
    """Generate CXML format text for LLM consumption."""
    lines = ["<documents>"]

    rendered = [i for i in infos if i.decision.include]
    for index, i in enumerate(rendered, 1):
        lines.append(f'<document index="{index}">')
        lines.append(f"<source>{i.rel}</source>")
        lines.append("<document_content>")

        try:
            text = read_text(i.path)
            lines.append(text)
        except Exception as e:
            lines.append(f"Failed to read: {str(e)}")

        lines.append("</document_content>")
        lines.append("</document>")

    lines.append("</documents>")
    return "\n".join(lines)


def build_html(repo_url: str, repo_dir: pathlib.Path, head_commit: str, infos: List[FileInfo]) -> str:
    # Use VS Code dark theme for syntax highlighting
    formatter = HtmlFormatter(nowrap=False, style='monokai')
    pygments_css = formatter.get_style_defs('.highlight')

    # Stats
    rendered = [i for i in infos if i.decision.include]
    skipped_binary = [i for i in infos if i.decision.reason == "binary"]
    skipped_large = [i for i in infos if i.decision.reason == "too_large"]
    skipped_ignored = [i for i in infos if i.decision.reason == "ignored"]
    total_files = len(rendered) + len(skipped_binary) + len(skipped_large) + len(skipped_ignored)

    # Directory tree
    tree_text = try_tree_command(repo_dir)

    # Generate CXML text for LLM view
    cxml_text = generate_cxml_text(infos, repo_dir)

    # Extract repo name for title
    repo_name = repo_url.split('/')[-1].replace('.git', '') if repo_url else 'Repository'

    # Table of contents with file icons
    toc_items: List[str] = []
    for i in rendered:
        anchor = slugify(i.rel)
        # Get file icon based on extension
        ext = pathlib.Path(i.rel).suffix.lower()
        icon = get_file_icon(ext)
        toc_items.append(
            f'<li><a href="#file-{anchor}"><span class="file-icon">{icon}</span>{html.escape(i.rel)}</a> '
            f'<span class="file-size">({bytes_human(i.size)})</span></li>'
        )
    toc_html = "".join(toc_items)

    # Render file sections with copy buttons
    sections: List[str] = []
    for i in rendered:
        anchor = slugify(i.rel)
        p = i.path
        ext = p.suffix.lower()
        icon = get_file_icon(ext)
        
        try:
            text = read_text(p)
            if ext in MARKDOWN_EXTENSIONS:
                body_html = f'<div class="markdown-content">{render_markdown_text(text)}</div>'
            else:
                code_html = highlight_code(text, i.rel, formatter)
                # Add copy button to code blocks
                body_html = f'''
                <div class="code-container">
                    <div class="code-header">
                        <span class="file-icon">{icon}</span>
                        <span class="file-path">{html.escape(i.rel)}</span>
                        <button class="copy-btn" onclick="copyCode(this)" data-content="{html.escape(text)}">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                            Copy
                        </button>
                    </div>
                    <div class="highlight">{code_html}</div>
                </div>
                '''
        except Exception as e:
            body_html = f'<pre class="error">Failed to render: {html.escape(str(e))}</pre>'
        
        sections.append(f"""
<section class="file-section" id="file-{anchor}">
  <div class="file-body">{body_html}</div>
</section>
""")

    # Skips lists
    def render_skip_list(title: str, items: List[FileInfo]) -> str:
        if not items:
            return ""
        lis = [
            f"<li><code>{html.escape(i.rel)}</code> "
            f"<span class='file-size'>({bytes_human(i.size)})</span></li>"
            for i in items
        ]
        return (
            f"<details class='skip-section'><summary>{html.escape(title)} ({len(items)})</summary>"
            f"<ul class='skip-list'>\n" + "\n".join(lis) + "\n</ul></details>"
        )

    skipped_html = (
        render_skip_list("Skipped binaries", skipped_binary) +
        render_skip_list("Skipped large files", skipped_large)
    )

    # Modern VS Code-inspired dark theme
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(repo_name)} - Flattened Repository</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📁</text></svg>">
<style>
  :root {{
    --bg-primary: #1e1e1e;
    --bg-secondary: #252526;
    --bg-tertiary: #2d2d30;
    --bg-hover: #37373d;
    --border-color: #3e3e42;
    --text-primary: #cccccc;
    --text-secondary: #9d9d9d;
    --text-muted: #6a6a6a;
    --accent-blue: #007acc;
    --accent-green: #4ec9b0;
    --accent-orange: #ce9178;
    --accent-purple: #c586c0;
    --scrollbar-bg: #2b2b2b;
    --scrollbar-thumb: #424242;
  }}

  * {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
  }}

  body {{
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.6;
    font-size: 14px;
  }}

  /* Custom scrollbar */
  ::-webkit-scrollbar {{
    width: 14px;
    height: 14px;
  }}

  ::-webkit-scrollbar-track {{
    background: var(--scrollbar-bg);
  }}

  ::-webkit-scrollbar-thumb {{
    background: var(--scrollbar-thumb);
    border-radius: 10px;
    border: 3px solid var(--scrollbar-bg);
  }}

  ::-webkit-scrollbar-thumb:hover {{
    background: #555;
  }}

  /* Navbar */
  .navbar {{
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border-color);
    padding: 0 20px;
    height: 50px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
    backdrop-filter: blur(10px);
  }}

  .navbar-brand {{
    display: flex;
    align-items: center;
    gap: 12px;
    font-weight: 600;
    font-size: 16px;
  }}

  .navbar-brand .logo {{
    font-size: 20px;
  }}

  .repo-info {{
    display: flex;
    align-items: center;
    gap: 15px;
    font-size: 13px;
    color: var(--text-secondary);
  }}

  .repo-info a {{
    color: var(--accent-blue);
    text-decoration: none;
  }}

  .repo-info a:hover {{
    text-decoration: underline;
  }}

  /* Layout */
  .main-container {{
    display: grid;
    grid-template-columns: 300px minmax(0, 1fr);
    height: calc(100vh - 50px);
  }}

  /* Sidebar */
  .sidebar {{
    background: var(--bg-secondary);
    border-right: 1px solid var(--border-color);
    overflow-y: auto;
    position: sticky;
    top: 50px;
    height: calc(100vh - 50px);
  }}

  .sidebar-header {{
    padding: 20px 16px 16px 16px;
    border-bottom: 1px solid var(--border-color);
    background: var(--bg-tertiary);
  }}

  .sidebar-title {{
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}

  .view-toggle {{
    display: flex;
    gap: 8px;
  }}

  .toggle-btn {{
    padding: 8px 16px;
    border: 1px solid var(--border-color);
    background: var(--bg-primary);
    color: var(--text-secondary);
    cursor: pointer;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    gap: 6px;
  }}

  .toggle-btn:hover {{
    background: var(--bg-hover);
    border-color: var(--accent-blue);
  }}

  .toggle-btn.active {{
    background: var(--accent-blue);
    color: white;
    border-color: var(--accent-blue);
  }}

  .sidebar-content {{
    padding: 16px;
  }}

  .toc {{
    list-style: none;
  }}

  .toc li {{
    margin: 2px 0;
  }}

  .toc a {{
    display: flex;
    align-items: center;
    padding: 6px 12px;
    color: var(--text-secondary);
    text-decoration: none;
    border-radius: 4px;
    transition: all 0.2s ease;
    font-size: 13px;
    gap: 8px;
  }}

  .toc a:hover {{
    background: var(--bg-hover);
    color: var(--text-primary);
  }}

  .file-icon {{
    font-size: 14px;
    width: 16px;
    flex-shrink: 0;
  }}

  .file-size {{
    margin-left: auto;
    font-size: 11px;
    color: var(--text-muted);
  }}

  /* Main content */
  .content {{
    overflow-y: auto;
    background: var(--bg-primary);
  }}

  .content-inner {{
    max-width: none;
    padding: 20px;
  }}

  /* File sections */
  .file-section {{
    margin-bottom: 24px;
  }}

  .code-container {{
    background: var(--bg-secondary);
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--border-color);
  }}

  .code-header {{
    background: var(--bg-tertiary);
    padding: 12px 16px;
    border-bottom: 1px solid var(--border-color);
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
  }}

  .file-path {{
    color: var(--text-primary);
    font-family: 'Cascadia Code', 'Fira Code', Monaco, 'Courier New', monospace;
    flex: 1;
  }}

  .copy-btn {{
    background: var(--bg-primary);
    border: 1px solid var(--border-color);
    color: var(--text-secondary);
    padding: 6px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 6px;
    transition: all 0.2s ease;
  }}

  .copy-btn:hover {{
    background: var(--bg-hover);
    border-color: var(--accent-blue);
    color: var(--accent-blue);
  }}

  .copy-btn.copied {{
    background: var(--accent-green);
    border-color: var(--accent-green);
    color: white;
  }}

  .highlight {{
    background: var(--bg-secondary) !important;
    margin: 0;
    padding: 16px;
  }}

  .highlight pre {{
    background: transparent !important;
    margin: 0;
    padding: 0;
    font-family: 'Cascadia Code', 'Fira Code', Monaco, 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.5;
    color: var(--text-primary);
  }}

  /* Markdown content */
  .markdown-content {{
    padding: 20px;
    background: var(--bg-secondary);
    border-radius: 8px;
    border: 1px solid var(--border-color);
  }}

  .markdown-content h1, .markdown-content h2, .markdown-content h3 {{
    color: var(--text-primary);
    margin-top: 24px;
    margin-bottom: 12px;
  }}

  .markdown-content h1:first-child,
  .markdown-content h2:first-child,
  .markdown-content h3:first-child {{
    margin-top: 0;
  }}

  .markdown-content p {{
    margin-bottom: 16px;
    color: var(--text-secondary);
  }}

  .markdown-content code {{
    background: var(--bg-primary);
    padding: 2px 6px;
    border-radius: 3px;
    font-family: 'Cascadia Code', 'Fira Code', Monaco, monospace;
    font-size: 12px;
    color: var(--accent-orange);
  }}

  .markdown-content pre {{
    background: var(--bg-primary);
    padding: 16px;
    border-radius: 6px;
    overflow-x: auto;
    margin: 16px 0;
  }}

  /* Tree section */
  .tree-section {{
    background: var(--bg-secondary);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 24px;
    border: 1px solid var(--border-color);
  }}

  .tree-section h2 {{
    color: var(--text-primary);
    font-size: 16px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  .tree-section pre {{
    background: var(--bg-primary);
    color: var(--text-secondary);
    padding: 16px;
    border-radius: 6px;
    overflow-x: auto;
    font-family: 'Cascadia Code', 'Fira Code', Monaco, 'Courier New', monospace;
    font-size: 12px;
    line-height: 1.4;
    border: 1px solid var(--border-color);
  }}

  /* Skip sections */
  .skip-section {{
    background: var(--bg-secondary);
    border-radius: 8px;
    margin-bottom: 16px;
    border: 1px solid var(--border-color);
  }}

  .skip-section summary {{
    padding: 16px;
    cursor: pointer;
    font-weight: 500;
    color: var(--text-primary);
    border-bottom: 1px solid var(--border-color);
  }}

  .skip-section[open] summary {{
    border-bottom: 1px solid var(--border-color);
  }}

  .skip-list {{
    padding: 16px;
    list-style: none;
  }}

  .skip-list li {{
    padding: 8px 0;
    color: var(--text-secondary);
    font-size: 13px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}

  .skip-list code {{
    background: var(--bg-primary);
    padding: 4px 8px;
    border-radius: 4px;
    font-family: 'Cascadia Code', 'Fira Code', Monaco, 'Courier New', monospace;
    color: var(--accent-orange);
  }}

  /* Stats section */
  .stats-section {{
    background: var(--bg-secondary);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 24px;
    border: 1px solid var(--border-color);
  }}

  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
  }}

  .stat-item {{
    text-align: center;
    padding: 16px;
    background: var(--bg-primary);
    border-radius: 6px;
  }}

  .stat-value {{
    font-size: 24px;
    font-weight: 600;
    color: var(--accent-blue);
    margin-bottom: 4px;
  }}

  .stat-label {{
    font-size: 12px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}

  /* LLM view */
  #llm-view {{
    display: none;
    padding: 20px;
  }}

  #llm-text {{
    width: 100%;
    height: 70vh;
    background: var(--bg-secondary);
    color: var(--text-primary);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 16px;
    font-family: 'Cascadia Code', 'Fira Code', Monaco, 'Courier New', monospace;
    font-size: 12px;
    line-height: 1.4;
    resize: vertical;
  }}

  #llm-text:focus {{
    outline: 2px solid var(--accent-blue);
    border-color: var(--accent-blue);
  }}

  .copy-hint {{
    margin-top: 12px;
    color: var(--text-muted);
    font-size: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  /* Error styles */
  .error {{
    color: #f48771;
    background: var(--bg-tertiary);
    padding: 16px;
    border-radius: 6px;
    border-left: 4px solid #f48771;
  }}

  /* Responsive design */
  @media (max-width: 768px) {{
    .main-container {{
      grid-template-columns: 1fr;
    }}
    
    .sidebar {{
      display: none;
    }}
    
    .navbar {{
      padding: 0 16px;
    }}
    
    .navbar-brand {{
      font-size: 14px;
    }}
    
    .repo-info {{
      display: none;
    }}
    
    .stats-grid {{
      grid-template-columns: repeat(2, 1fr);
    }}
  }}

  /* Pygments overrides for dark theme */
  {pygments_css}
</style>
</head>
<body>

<!-- Navbar -->
<nav class="navbar">
  <div class="navbar-brand">
    <span class="logo">📁</span>
    <span>git/{html.escape(repo_name)}</span>
  </div>
  <div class="repo-info">
    <span>📦 <a href="{html.escape(repo_url)}" target="_blank">{html.escape('/'.join(repo_url.split('/')[-2:]) if '/' in repo_url else repo_url)}</a></span>
    <span>🔗 {html.escape(head_commit[:8])}</span>
  </div>
</nav>

<!-- Main container -->
<div class="main-container">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-title">Explorer</div>
      <div class="view-toggle">
        <button class="toggle-btn active" onclick="showHumanView()">👤 Human</button>
        <button class="toggle-btn" onclick="showLLMView()">🤖 LLM</button>
      </div>
    </div>
    <div class="sidebar-content">
      <ul class="toc">
        {toc_html}
      </ul>
    </div>
  </div>

  <!-- Main content -->
  <div class="content">
    <div class="content-inner">
      
      <div id="human-view">
        <!-- Stats section -->
        <div class="stats-section">
          <div class="stats-grid">
            <div class="stat-item">
              <div class="stat-value">{total_files}</div>
              <div class="stat-label">Total Files</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{len(rendered)}</div>
              <div class="stat-label">Rendered</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{len(skipped_binary) + len(skipped_large)}</div>
              <div class="stat-label">Skipped</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{bytes_human(sum(i.size for i in rendered))}</div>
              <div class="stat-label">Total Size</div>
            </div>
          </div>
        </div>

        <!-- Directory tree -->
        <div class="tree-section">
          <h2>🌳 Directory Structure</h2>
          <pre>{html.escape(tree_text)}</pre>
        </div>

        <!-- Skipped items -->
        {skipped_html}

        <!-- File sections -->
        {''.join(sections)}
      </div>

      <div id="llm-view">
        <div class="stats-section">
            <div class="stats-section">
  <h2>🤖 LLM View - CXML Format</h2>
  <div style="display: flex; align-items: flex-start; gap: 16px; margin-bottom: 16px;">
    <p style="margin: 0; flex: 1;">This is the raw CXML format that you can copy and paste directly into Claude, ChatGPT, or other LLMs for code analysis:</p>
    <button class="copy-btn" onclick="copyLLMText()">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
      </svg>
      Copy All
    </button>
  </div>
  <textarea id="llm-text" readonly>{html.escape(cxml_text)}</textarea>
  <div class="copy-hint">
          </div>
        </div>
      </div>
      
    </div>
  </div>
</div>

<script>
// Copy functionality
function copyCode(button) {{
  const content = button.getAttribute('data-content');
  navigator.clipboard.writeText(content).then(() => {{
    const originalText = button.innerHTML;
    button.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="20,6 9,17 4,12"></polyline>
      </svg>
      Copied!
    `;
    button.classList.add('copied');
    
    setTimeout(() => {{
      button.innerHTML = originalText;
      button.classList.remove('copied');
    }}, 2000);
  }}).catch(err => {{
    console.error('Failed to copy: ', err);
  }});
}}

function copyLLMText() {{
  const textArea = document.getElementById('llm-text');
  textArea.select();
  document.execCommand('copy');
  
  const button = event.target.closest('button');
  const originalText = button.innerHTML;
  button.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="20,6 9,17 4,12"></polyline>
    </svg>
    Copied!
  `;
  button.classList.add('copied');
  
  setTimeout(() => {{
    button.innerHTML = originalText;
    button.classList.remove('copied');
  }}, 2000);
}}

// View switching
function showHumanView() {{
  document.getElementById('human-view').style.display = 'block';
  document.getElementById('llm-view').style.display = 'none';
  document.querySelectorAll('.toggle-btn').forEach(btn => btn.classList.remove('active'));
  event.target.classList.add('active');
}}

function showLLMView() {{
  document.getElementById('human-view').style.display = 'none';
  document.getElementById('llm-view').style.display = 'block';
  document.querySelectorAll('.toggle-btn').forEach(btn => btn.classList.remove('active'));
  event.target.classList.add('active');
}}

// Smooth scrolling for sidebar links
document.querySelectorAll('.toc a[href^="#"]').forEach(anchor => {{
  anchor.addEventListener('click', function (e) {{
    e.preventDefault();
    const target = document.querySelector(this.getAttribute('href'));
    if (target) {{
      target.scrollIntoView({{
        behavior: 'smooth',
        block: 'start'
      }});
    }}
  }});
}});
</script>
</body>
</html>
"""


def derive_temp_output_path(repo_url: str) -> pathlib.Path:
    """Derive a temporary output path from the repo URL."""
    # Extract repo name from URL like https://github.com/owner/repo or https://github.com/owner/repo.git
    parts = repo_url.rstrip('/').split('/')
    if len(parts) >= 2:
        repo_name = parts[-1]
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
        filename = f"{repo_name}.html"
    else:
        filename = "repo.html"

    return pathlib.Path(tempfile.gettempdir()) / filename


def main() -> int:
    ap = argparse.ArgumentParser(description="Flatten a GitHub repo to a single HTML page")
    ap.add_argument("repo_url", help="GitHub repo URL (https://github.com/owner/repo[.git])")
    ap.add_argument("-o", "--out", help="Output HTML file path (default: temporary file derived from repo name)")
    ap.add_argument("--max-bytes", type=int, default=MAX_DEFAULT_BYTES, help="Max file size to render (bytes); larger files are listed but skipped")
    ap.add_argument("--no-open", action="store_true", help="Don't open the HTML file in browser after generation")
    args = ap.parse_args()

    # Set default output path if not provided
    if args.out is None:
        args.out = str(derive_temp_output_path(args.repo_url))

    tmpdir = tempfile.mkdtemp(prefix="flatten_repo_")
    repo_dir = pathlib.Path(tmpdir, "repo")

    try:
        print(f"📁 Cloning {args.repo_url} to temporary directory: {repo_dir}", file=sys.stderr)
        git_clone(args.repo_url, str(repo_dir))
        head = git_head_commit(str(repo_dir))
        print(f"✓ Clone complete (HEAD: {head[:8]})", file=sys.stderr)

        print(f"📊 Scanning files in {repo_dir}...", file=sys.stderr)
        infos = collect_files(repo_dir, args.max_bytes)
        rendered_count = sum(1 for i in infos if i.decision.include)
        skipped_count = len(infos) - rendered_count
        print(f"✓ Found {len(infos)} files total ({rendered_count} will be rendered, {skipped_count} skipped)", file=sys.stderr)

        print(f"🔨 Generating HTML...", file=sys.stderr)
        html_out = build_html(args.repo_url, repo_dir, head, infos)

        out_path = pathlib.Path(args.out)
        print(f"💾 Writing HTML file: {out_path.resolve()}", file=sys.stderr)
        out_path.write_text(html_out, encoding="utf-8")
        file_size = out_path.stat().st_size
        print(f"✓ Wrote {bytes_human(file_size)} to {out_path}", file=sys.stderr)

        if not args.no_open:
            print(f"🌐 Opening {out_path} in browser...", file=sys.stderr)
            webbrowser.open(f"file://{out_path.resolve()}")

        print(f"🗑️  Cleaning up temporary directory: {tmpdir}", file=sys.stderr)
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()