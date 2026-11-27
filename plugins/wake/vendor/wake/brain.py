#!/usr/bin/env python3
"""brain.py: the helper for a Wake vault. Standard library only, so it also runs from inside the vault
(`python _system/scripts/brain.py ...`) on a machine that never installed Wake. `wake brain ...` is the same code.

Commands:
  lint  [--json] [--strict]       health check: dead links, orphans, index gaps, frontmatter, conflict markers, stale hot.md
  find  <words...> [-n N]         ranked search over notes (titles, aliases, tags, text)
  log   <op> <title> [--agent A] [--detail TEXT]...   add an entry to the top of log.md
  new   <type> <title> [--force]  create a note from _system/templates (project, person, entity, concept, source, answer, daily)
  stats                           note counts by folder and type
  sync  [-m MESSAGE]              commit everything, git pull --rebase, push (when a remote exists)

Which vault: --vault, else $WAKE_VAULT, else $BRAIN_VAULT, else the vault this script sits in
(<vault>/_system/scripts/brain.py), else the nearest folder above the working directory that holds
AGENTS.md and hot.md, else ~/vault.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".obsidian", ".trash", "raw", "_system", "node_modules"}
META_NOTES = {"index", "log", "hot", "home", "readme", "agents", "claude"}
KNOWLEDGE_DIRS = ("me", "people", "projects", "wiki")
# The owner's own space and unfiled material: no frontmatter schema, and stray links there are not policed.
OWNER_DIRS = ("inbox", "notes", "daily", "archive")
TYPE_FOLDERS = {
    "project": "projects",
    "person": "people",
    "entity": "wiki/entities",
    "concept": "wiki/concepts",
    "source": "wiki/sources",
    "answer": "wiki/answers",
    "daily": "daily",
}
LINK_RE = re.compile(r"!?\[\[([^\]\|#\^]*)(?:[#\^][^\]\|]*)?(?:\|[^\]]*)?\]\]")
FENCE_RE = re.compile(r"(^|\n)(```|~~~).*?\n\2", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CONFLICT_RE = re.compile(r"^(<{7}|>{7})( |$)", re.M)
BAD_TITLE_CHARS = set('\\/:*?"<>|')
TODAY = dt.date.today()
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def find_vault(explicit: str | None = None, start: Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    for var in ("WAKE_VAULT", "BRAIN_VAULT"):
        if os.environ.get(var):
            return Path(os.environ[var]).expanduser().resolve()
    here = Path(__file__).resolve()
    if here.parent.name == "scripts" and here.parent.parent.name == "_system":
        return here.parents[2]
    cwd = (start or Path.cwd()).resolve()
    for folder in (cwd, *cwd.parents):
        if (folder / "AGENTS.md").is_file() and (folder / "hot.md").is_file():
            return folder
    return Path.home() / "vault"


VAULT = find_vault()


# ---------------------------------------------------------------- parsing

def split_frontmatter(text: str) -> tuple[dict, str, bool]:
    """Return (properties, body, has_frontmatter). Handles the flat YAML subset the vault uses."""
    if not text.startswith("---"):
        return {}, text, False
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text, False
    block = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    props: dict = {}
    key = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s*(.*)$", line)
        if item and key:
            if not isinstance(props.get(key), list):
                props[key] = []
            props[key].append(unquote(item.group(1)))
            continue
        kv = re.match(r"^([A-Za-z0-9_\-]+):\s*(.*)$", line)
        if kv:
            key, value = kv.group(1), kv.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                props[key] = [unquote(v.strip()) for v in inner.split(",") if v.strip()] if inner else []
            else:
                props[key] = unquote(value) if value else []
    return props, body, True


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def strip_code(text: str) -> str:
    return INLINE_CODE_RE.sub("", FENCE_RE.sub("\n", text))


class Note:
    def __init__(self, path: Path):
        self.path = path
        self.rel = path.relative_to(VAULT).as_posix()
        self.name = path.stem
        self.text = path.read_text(encoding="utf-8", errors="replace")
        self.props, self.body, self.has_fm = split_frontmatter(self.text)
        aliases = self.props.get("aliases") or []
        self.aliases = [aliases] if isinstance(aliases, str) else list(aliases)
        self.links = [m.group(1).strip() for m in LINK_RE.finditer(strip_code(self.text)) if m.group(1).strip()]

    @property
    def top(self) -> str:
        return self.rel.split("/", 1)[0] if "/" in self.rel else ""

    @property
    def is_meta(self) -> bool:
        return self.top == "" and self.name.lower() in META_NOTES


def load_notes() -> list[Note]:
    notes = []
    for path in sorted(VAULT.rglob("*.md")):
        parts = path.relative_to(VAULT).parts
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        notes.append(Note(path))
    return notes


def all_file_names() -> set[str]:
    """Lower-case names (with and without extension) of every non-markdown file, for links to bases, canvases, images."""
    names = set()
    for path in VAULT.rglob("*"):
        if path.is_file() and ".git" not in path.parts and path.suffix != ".md":
            rel = path.relative_to(VAULT).as_posix().lower()
            names.update({path.name.lower(), rel})
    return names


def build_resolver(notes: list[Note]):
    table: dict[str, Note] = {}
    for n in notes:
        table.setdefault(n.name.lower(), n)
        table.setdefault(n.rel[:-3].lower(), n)
        for a in n.aliases:
            table.setdefault(str(a).lower(), n)
    files = all_file_names()

    def resolve(target: str):
        t = target.strip().lower().replace("\\", "/")
        if t.endswith(".md"):
            t = t[:-3]
        if t in table:
            return table[t]
        if t in files or t.split("/")[-1] in files:
            return "file"
        return None

    return resolve


# ---------------------------------------------------------------- commands

def cmd_lint(args) -> int:
    notes = load_notes()
    resolve = build_resolver(notes)
    inbound: dict[str, set[str]] = {n.rel: set() for n in notes}
    dead, frontmatter, empty, conflicts = [], [], [], []
    index = next((n for n in notes if n.rel == "index.md"), None)
    indexed: set[str] = set()

    for n in notes:
        if CONFLICT_RE.search(strip_code(n.text)):
            conflicts.append(n.rel)
        for target in n.links:
            hit = resolve(target)
            if hit is None:
                if n.top not in OWNER_DIRS:
                    dead.append({"note": n.rel, "link": target})
            elif isinstance(hit, Note) and hit.rel != n.rel:
                if not n.is_meta:
                    inbound[hit.rel].add(n.rel)
                if n is index:
                    indexed.add(hit.rel)

    knowledge = [n for n in notes if n.top in KNOWLEDGE_DIRS]
    orphans = [n.rel for n in knowledge if not inbound[n.rel]]
    not_indexed = [n.rel for n in knowledge if n.rel not in indexed]

    for n in notes:
        if n.is_meta and n.name.lower() in {"readme", "agents", "claude"}:
            continue
        if n.top in OWNER_DIRS:
            continue
        problems = []
        if not n.has_fm:
            problems.append("no frontmatter")
        else:
            for key in ("type", "created", "updated"):
                if not n.props.get(key):
                    problems.append(f"missing {key}")
            for key in ("created", "updated"):
                v = n.props.get(key)
                if isinstance(v, str) and v and not DATE_RE.match(v):
                    problems.append(f"{key} not YYYY-MM-DD")
            c, u = n.props.get("created"), n.props.get("updated")
            if isinstance(c, str) and isinstance(u, str) and DATE_RE.match(c) and DATE_RE.match(u) and u < c:
                problems.append("updated before created")
        if problems:
            frontmatter.append({"note": n.rel, "problems": problems})
        if len(n.body.strip()) < 40 and not n.is_meta and n.top != "journal":
            empty.append(n.rel)

    hot_age = None
    hot = next((n for n in notes if n.rel == "hot.md"), None)
    if hot and isinstance(hot.props.get("updated"), str) and DATE_RE.match(hot.props["updated"]):
        hot_age = (TODAY - dt.date.fromisoformat(hot.props["updated"])).days
    inbox = sorted(p.relative_to(VAULT).as_posix() for p in (VAULT / "inbox").glob("*")
                   if p.is_file() and p.name != ".gitkeep")

    report = {
        "notes": len(notes),
        "dead_links": dead,
        "orphans": orphans,
        "not_in_index": not_indexed,
        "frontmatter": frontmatter,
        "conflict_markers": conflicts,
        "near_empty": empty,
        "hot_age_days": hot_age,
        "inbox_waiting": inbox,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Brain lint: {len(notes)} notes in {VAULT}")
        section("Dead links", [f"{d['note']} -> [[{d['link']}]]" for d in dead])
        section("Orphans (no inbound links outside index/log/hot)", orphans)
        section("Not in index.md", not_indexed)
        section("Frontmatter problems", [f"{f['note']}: {', '.join(f['problems'])}" for f in frontmatter])
        section("Unresolved merge conflict markers", conflicts)
        section("Near-empty notes", empty)
        section("Waiting in inbox/", inbox)
        if hot_age is None:
            print("\nhot.md: no valid 'updated' date")
        elif hot_age > 7:
            print(f"\nhot.md: stale ({hot_age} days old), refresh it")
        else:
            print(f"\nhot.md: fresh ({hot_age} days old)")
    errors = len(dead) + len(frontmatter) + len(conflicts)
    return 1 if (args.strict and errors) else 0


def section(title: str, items: list[str]) -> None:
    print(f"\n{title}: {len(items) if items else 'none'}")
    for item in items:
        print(f"  - {item}")


def cmd_find(args) -> int:
    words = [w.lower() for w in args.words]
    scored = []
    for n in load_notes():
        title = n.name.lower()
        aliases = " ".join(str(a).lower() for a in n.aliases)
        tags = n.props.get("tags") or []
        tags = " ".join(tags if isinstance(tags, list) else [tags]).lower()
        body = n.text.lower()
        score = 0
        for w in words:
            score += 12 * (w in title) + 8 * (w in aliases) + 4 * (w in tags) + min(body.count(w), 10)
        if all(w in body or w in title for w in words):
            score += 5
        if score:
            scored.append((score, n))
    scored.sort(key=lambda s: (-s[0], s[1].rel))
    for score, n in scored[: args.n]:
        first = next((l.strip() for l in n.body.splitlines() if l.strip() and not l.startswith("#")), "")
        print(f"{score:4d}  {n.rel}\n      {first[:140]}")
    if not scored:
        print("No matches.")
    return 0


def cmd_log(args) -> int:
    path = VAULT / "log.md"
    text = path.read_text(encoding="utf-8")
    marker = "<!-- entries -->"
    entry = [f"## [{TODAY.isoformat()}] {args.op} | {args.title}"]
    if args.agent:
        entry.append(f"- agent: {args.agent}")
    entry += [f"- {d}" for d in args.detail or []]
    block = "\n".join(entry) + "\n"
    if marker in text:
        text = text.replace(marker, marker + "\n\n" + block.rstrip("\n"), 1)
    else:
        text = text.rstrip("\n") + "\n\n" + block
    text = re.sub(r"^updated: .*$", f"updated: {TODAY.isoformat()}", text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"Logged: {entry[0]}")
    return 0


def fallback_template(kind: str) -> str:
    return ("---\ntype: " + kind + "\ncreated: {{date}}\nupdated: {{date}}\ntags:\n  - " + kind +
            "\n---\n\n# {{title}}\n")


def cmd_new(args) -> int:
    kind = args.type.lower()
    if kind not in TYPE_FOLDERS:
        print(f"Unknown type {kind!r}; choose from {', '.join(TYPE_FOLDERS)}")
        return 2
    title = args.title or (TODAY.isoformat() if kind == "daily" else "")
    if not title or BAD_TITLE_CHARS & set(title) or title.strip(". ") != title:
        print(f"Not a usable note title: {title!r} (no path characters, no leading or trailing dots or spaces)")
        return 2
    template = VAULT / "_system" / "templates" / f"{kind.capitalize()}.md"
    target = VAULT / TYPE_FOLDERS[kind] / f"{title}.md"
    if target.exists() and not args.force:
        print(f"Exists: {target.relative_to(VAULT).as_posix()}")
        return 1
    body = template.read_text(encoding="utf-8") if template.exists() else fallback_template(kind)
    body = body.replace("{{title}}", title).replace("{{date}}", TODAY.isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8", newline="\n")
    print(f"Created: {target.relative_to(VAULT).as_posix()} (add it to index.md)")
    return 0


def cmd_stats(args) -> int:
    notes = load_notes()
    by_folder: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for n in notes:
        folder = n.rel.rsplit("/", 1)[0] if "/" in n.rel else "(root)"
        by_folder[folder] = by_folder.get(folder, 0) + 1
        t = n.props.get("type") or "(none)"
        by_type[str(t)] = by_type.get(str(t), 0) + 1
    links = sum(len(n.links) for n in notes)
    print(f"{len(notes)} notes, {links} links")
    for title, table in (("By folder", by_folder), ("By type", by_type)):
        print(f"\n{title}:")
        for k, v in sorted(table.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {v:4d}  {k}")
    raw = [p for p in (VAULT / "raw").rglob("*") if p.is_file() and p.name != ".gitkeep"]
    print(f"\nraw/ files: {len(raw)}")
    return 0


def git(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(VAULT), *cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check, **NO_WINDOW)


def cmd_sync(args) -> int:
    """Commit local changes first, then rebase onto the remote. No autostash: if the rebase conflicts it is
    aborted, the local commit stays as it was, and no conflict markers ever land in a note."""
    if not (VAULT / ".git").exists():
        print("Not a git repo; nothing to sync.")
        return 1
    has_remote = bool(git("remote", check=False).stdout.strip())
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        msg = args.message or f"brain: sync {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        commit = git("commit", "-m", msg, check=False)
        if commit.returncode != 0:
            print("git commit failed (is user.name / user.email set?):\n" + commit.stdout + commit.stderr)
            return 1
        print(commit.stdout.strip().splitlines()[0])
    else:
        print("Nothing to commit.")
    if not has_remote:
        print("No remote configured; committed locally only.")
        return 0
    has_upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).returncode == 0
    if has_upstream:
        pull = git("pull", "--rebase", check=False)
        if pull.returncode != 0:
            rebasing = (VAULT / ".git" / "rebase-merge").exists() or (VAULT / ".git" / "rebase-apply").exists()
            if rebasing:
                git("rebase", "--abort", check=False)
            print(("git pull hit a conflict and was undone; your changes are committed locally and untouched. "
                   if rebasing else "git pull failed; ")
                  + "Resolve by hand (git pull --rebase in the vault):\n" + pull.stdout + pull.stderr)
            return 1
    push = git("push", check=False) if has_upstream else git("push", "-u", "origin", "HEAD", check=False)
    print("Pushed." if push.returncode == 0 else "git push failed:\n" + push.stderr)
    return push.returncode


def build_parser() -> argparse.ArgumentParser:
    # --vault is accepted before or after the subcommand. The subparser copy uses SUPPRESS so that
    # omitting it there doesn't overwrite a value given before the subcommand.
    help_text = "the vault folder (default: see the top of this file)"
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", default=argparse.SUPPRESS, help=help_text)
    p = argparse.ArgumentParser(prog="brain.py", description="Helper for the brain vault.")
    p.add_argument("--vault", default=None, help=help_text)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("lint", help="health check", parents=[common])
    s.add_argument("--json", action="store_true")
    s.add_argument("--strict", action="store_true",
                   help="exit 1 on dead links, frontmatter errors or conflict markers")
    s.set_defaults(func=cmd_lint)

    s = sub.add_parser("find", help="ranked search", parents=[common])
    s.add_argument("words", nargs="+")
    s.add_argument("-n", type=int, default=10)
    s.set_defaults(func=cmd_find)

    s = sub.add_parser("log", help="add a log.md entry", parents=[common])
    s.add_argument("op")
    s.add_argument("title")
    s.add_argument("--agent")
    s.add_argument("--detail", action="append")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("new", help="create a note from a template", parents=[common])
    s.add_argument("type")
    s.add_argument("title", nargs="?", default="")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("stats", help="counts", parents=[common])
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("sync", help="commit, pull --rebase, push", parents=[common])
    s.add_argument("-m", "--message")
    s.set_defaults(func=cmd_sync)
    return p


def main(argv: list[str] | None = None) -> int:
    global VAULT
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.vault:
        VAULT = find_vault(args.vault)
    if not VAULT.is_dir():
        print(f"No vault at {VAULT}. Create one with `wake init`, or set WAKE_VAULT / pass --vault.")
        return 2
    try:
        return args.func(args)
    except subprocess.CalledProcessError as error:
        print(f"git failed: {' '.join(map(str, error.cmd))}\n{error.stdout or ''}{error.stderr or ''}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
