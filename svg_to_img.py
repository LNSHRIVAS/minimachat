"""
svg_to_img — LLM SVG → PNG renderer for terminals.

Zero runtime dependency beyond Python + a single binary download.
The resvg binary (~5MB) is downloaded automatically on first run.

Usage:
    from svg_to_img import render, show, save, SYSTEM_PROMPT

    svg = call_your_llm("draw a diagram", system=SYSTEM_PROMPT)
    png = render(svg)
    show(png)
    save(png, "output/diagram.png")
"""

import base64, json, os, platform, shutil, subprocess, sys, tempfile, urllib.request, zipfile
from pathlib import Path

# ── Binary management ─────────────────────────────────────────

BINARY_DIR = Path.home() / ".svg_to_img"
BINARY_DIR.mkdir(parents=True, exist_ok=True)

def _platform():
    s = platform.system().lower()
    m = platform.machine().lower()
    if s == "windows":
        return ("windows", "x86_64" if m in ("amd64", "x86_64") else "aarch64")
    if s == "linux":
        arch = "x86_64" if m in ("amd64", "x86_64") else ("aarch64" if m == "aarch64" else m)
        return ("linux", arch)
    if s == "darwin":
        arch = "aarch64" if m == "arm64" else "x86_64"
        return ("macos", arch)
    raise OSError(f"Unsupported platform: {s}")

def _binary_name(os_name):
    return "resvg.exe" if os_name == "windows" else "resvg"

def _binary_path():
    return BINARY_DIR / _binary_name(_platform()[0])

def _asset_name(os_name, arch):
    # resvg release asset names
    names = {
        ("windows", "x86_64"): "resvg-win64.zip",
        ("linux", "x86_64"): "resvg-linux-x86_64.tar.gz",
        ("linux", "aarch64"): "resvg-linux-aarch64.tar.gz",
        ("macos", "x86_64"): "resvg-macos-x86_64.zip",
        ("macos", "aarch64"): "resvg-macos-aarch64.zip",
    }
    return names[(os_name, arch)]

def _download_resvg():
    os_name, arch = _platform()
    name = _asset_name(os_name, arch)
    url = f"https://github.com/RazrFalcon/resvg/releases/latest/download/{name}"
    dest = BINARY_DIR / name
    binary_name = _binary_name(os_name)

    print(f"Downloading resvg for {os_name}/{arch}...", file=sys.stderr)
    urllib.request.urlretrieve(url, dest)

    if name.endswith(".tar.gz"):
        import tarfile
        with tarfile.open(dest, "r:gz") as tf:
            for member in tf.getmembers():
                if os.path.basename(member.name) == binary_name:
                    with tf.extractfile(member) as src, open(_binary_path(), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    break
    else:
        with zipfile.ZipFile(dest, "r") as zf:
            for member in zf.namelist():
                if os.path.basename(member) == binary_name or os.path.basename(member) == f"{binary_name}.exe":
                    with zf.open(member) as src, open(_binary_path(), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    break

    dest.unlink()
    _binary_path().chmod(0o755)
    print(f"Downloaded resvg to {_binary_path()}", file=sys.stderr)

def _ensure_binary():
    binary = _binary_path()
    if not binary.exists():
        _download_resvg()
    return str(binary)

# ── Core SVG → PNG ───────────────────────────────────────────

def render(svg, width=800, background="#ffffff"):
    """SVG string → PNG bytes using resvg CLI."""
    binary = _ensure_binary()
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w", encoding="utf-8") as f:
        f.write(svg)
        svg_path = f.name
    try:
        png_path = svg_path.replace(".svg", ".png")
        result = subprocess.run(
            [binary, "--width", str(width), "--background", background, svg_path, png_path],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"resvg failed: {result.stderr.decode()}")
        with open(png_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(svg_path)
        png_path = svg_path.replace(".svg", ".png")
        if os.path.exists(png_path):
            os.unlink(png_path)

def save(png, path):
    """Write PNG bytes to file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return str(path)

def show(png):
    """Display PNG inline in terminal (Kitty/iTerm2 protocol)."""
    b64 = base64.b64encode(png).decode()
    term = os.environ.get("TERM_PROGRAM", "")
    if "iTerm" in term:
        sys.stdout.write(f"\x1b]1337;File=inline=1;width=auto:{b64}\x07\n")
    else:
        sys.stdout.write(f"\x1b_G;a=T,f=100,d=1:{b64}\x1b\\\n")
    sys.stdout.flush()

# ── System prompt for LLM ────────────────────────────────────

SYSTEM_PROMPT = """You are a diagram generation engine.
Respond ONLY with valid SVG code inside ```svg ... ``` blocks.
The SVG will be rendered inline in the app (living figure). PNG export is only when the user asks to save.

Guidelines:
- Use <svg viewBox="0 0 W H" xmlns="http://www.w3.org/2000/svg">
- Use <rect>, <circle>, <path>, <text>, <line>, <ellipse> as needed
- Use <defs> for gradients, filters, markers
- Add <filter> with feDropShadow for depth
- Use responsive viewBox (don't set absolute width/height on root)
- Text: use font-family="system-ui, sans-serif" and text-anchor
- Arrows: use <marker> with marker-end on <path> or <line>
- Colors: use a consistent palette
- Keep it readable — good contrast, clear labels, logical layout

For diagrams: boxes with text labels, connecting arrows, color-coded sections.
For illustrations: geometric shapes, gradients, layered compositions.
Output ONLY the SVG code. No explanation."""

# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    svg = sys.argv[1] if len(sys.argv) > 1 else (
        '<svg viewBox="0 0 400 200" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="400" height="200" fill="#f0f9ff" rx="16"/>'
        '<circle cx="200" cy="100" r="60" fill="#3b82f6"/>'
        '<text x="200" y="110" text-anchor="middle" fill="white" '
        'font-family="sans-serif" font-size="18" font-weight="bold">Hello</text></svg>'
    )
    png = render(svg)
    path = save(png, "output/diagram.png")
    kb = len(png) / 1024
    print(f"Saved output/diagram.png ({kb:.1f} KB)")
    show(png)
