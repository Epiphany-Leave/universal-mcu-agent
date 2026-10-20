#!/usr/bin/env bash
# Universal MCU Agent Platform installer/updater (Linux/macOS)
# User-owned data is never removed, including when --force is used.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$SCRIPT_DIR/core"
PLATFORM_VERSION="$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION")"
PYTHON_BIN="${PYTHON:-python3}"

AGENT="claude-code"
PROJECT=""
FORCE=false
UPDATE=false
CLEAN_LEGACY_CLAUDE=false

usage() {
    echo "用法: ./install.sh --agent <claude-code|codex|opencode|cursor|pi|all> [--project <path>] [--force] [--update] [--clean-legacy-claude]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent) [[ $# -ge 2 ]] || { echo "[ERROR] --agent requires a value" >&2; exit 2; }; AGENT="$2"; shift 2 ;;
        --project) [[ $# -ge 2 ]] || { echo "[ERROR] --project requires a value" >&2; exit 2; }; PROJECT="$2"; shift 2 ;;
        --force) FORCE=true; shift ;;
        --update) UPDATE=true; shift ;;
        --clean-legacy-claude) CLEAN_LEGACY_CLAUDE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "[ERROR] Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$AGENT" in
    claude-code|codex|opencode|cursor|pi|all) ;;
    *) echo "[ERROR] Unknown agent: $AGENT" >&2; usage >&2; exit 2 ;;
esac

if [[ "$AGENT" != "claude-code" || -n "$PROJECT" ]]; then
    [[ -n "$PROJECT" ]] || { echo "[ERROR] --agent $AGENT requires --project" >&2; exit 2; }
    [[ -d "$PROJECT" ]] || { echo "[ERROR] Project does not exist: $PROJECT" >&2; exit 2; }
    PROJECT="$(cd "$PROJECT" && pwd)"
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "[ERROR] Python 3.10+ is required" >&2; exit 3; }
"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
    echo "[ERROR] Python 3.10+ is required" >&2; exit 3;
}
"$PYTHON_BIN" -c 'import yaml, jsonschema' >/dev/null 2>&1 || {
    echo "[ERROR] Missing core Python dependencies. Run: $PYTHON_BIN -m pip install '$SCRIPT_DIR'" >&2
    exit 3
}

for required in runtime tools workflows templates config registry schemas analysis knowledge; do
    [[ -d "$CORE_DIR/$required" ]] || { echo "[ERROR] Missing source: core/$required" >&2; exit 3; }
done
[[ -f "$SCRIPT_DIR/adapters/common/runtime-entry.md" ]] || { echo "[ERROR] Missing unified runtime entry" >&2; exit 3; }

timestamp() { printf '%s-%s-%05d' "$(date -u +%Y%m%dT%H%M%SZ)" "$$" "$RANDOM"; }

backup_user_data() {
    local agent_dir="$1" stamp="$2" backup="$agent_dir/backups/$stamp"
    mkdir -p "$backup"
    for name in state custom config.yaml artifact-manifest.yaml project-contract.json policy.lock; do
        [[ -e "$agent_dir/$name" ]] && cp -a "$agent_dir/$name" "$backup/$name"
    done
    if [[ -d "$agent_dir/knowledge/project" ]]; then
        mkdir -p "$backup/knowledge"
        cp -a "$agent_dir/knowledge/project" "$backup/knowledge/project"
    fi
    local legacy_backup="$backup/legacy-runtime-state"
    for name in intents work-packages claims attestations sessions memories phase-state phase-state.json evidence-debts evidence-debts.json assumptions engineering-assumptions.json ledger capabilities.json execution-ledger.json; do
        if [[ -e "$agent_dir/runtime/$name" ]]; then
            mkdir -p "$legacy_backup"
            cp -a "$agent_dir/runtime/$name" "$legacy_backup/$name"
        fi
    done
    echo "  [BACKUP] $backup"
}

migrate_legacy_state() {
    local agent_dir="$1" stamp="$2" report="$agent_dir/state/migration-reports/$stamp.json"
    mkdir -p "$(dirname "$report")"
    "$PYTHON_BIN" - "$agent_dir" "$report" <<'PY'
import json
import re
import shutil
import sys
from pathlib import Path

agent = Path(sys.argv[1])
report_path = Path(sys.argv[2])
legacy = agent / "runtime"
state = agent / "state"
items = [
    ("intents", "intents"), ("work-packages", "work-packages"),
    ("claims", "claims"), ("attestations", "attestations"),
    ("sessions", "sessions"), ("memories", "memories"),
    ("capabilities.json", "capabilities.json"),
    ("phase-state", "phase-state.json"), ("phase-state.json", "phase-state.json"),
    ("evidence-debts", "evidence-debts.json"), ("evidence-debts.json", "evidence-debts.json"),
    ("assumptions", "engineering-assumptions.json"),
    ("engineering-assumptions.json", "engineering-assumptions.json"),
    ("ledger", "execution-ledger.json"),
    ("execution-ledger.json", "execution-ledger.json"),
]
actions = []

def revision(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict):
            raw = value.get("revision", value.get("metadata", {}).get("revision", 0))
            return int(raw or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    try:
        match = re.search(r"(?m)^\s*revision\s*:\s*(\d+)\s*$", path.read_text(encoding="utf-8-sig"))
        return int(match.group(1)) if match else 0
    except OSError:
        return 0

def migrate_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.move(str(src), str(dst))
        actions.append({"source": str(src), "destination": str(dst), "action": "moved", "revision": revision(dst)})
        return
    src_revision, dst_revision = revision(src), revision(dst)
    if src_revision > dst_revision:
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
        shutil.move(str(src), str(dst))
        action = "legacy-replaced-lower-revision"
    else:
        if src.is_dir():
            shutil.rmtree(src)
        else:
            src.unlink()
        action = "state-kept-equal-or-higher-revision"
    actions.append({"source": str(src), "destination": str(dst), "action": action,
                    "legacy_revision": src_revision, "state_revision": dst_revision})

state.mkdir(parents=True, exist_ok=True)
for source_name, destination_name in items:
    src = legacy / source_name
    dst = state / destination_name
    if not src.exists():
        continue
    if src.is_dir():
        # Record-shaped state is canonicalized as one JSON file.  An old
        # directory cannot safely be guessed into that record, so preserve it
        # under state/legacy instead of creating a directory named *.json.
        directory_dst = dst
        if Path(destination_name).suffix == ".json":
            directory_dst = state / "legacy" / source_name
            actions.append({
                "source": str(src), "destination": str(directory_dst),
                "action": "legacy-directory-preserved-for-manual-review",
            })
        for child in sorted((p for p in src.rglob("*") if p.is_file()), key=lambda p: len(p.parts)):
            migrate_file(child, directory_dst / child.relative_to(src))
        for child in sorted((p for p in src.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            child.rmdir()
        src.rmdir()
    else:
        migrate_file(src, dst)

payload = {
    "schema_version": "1.0",
    "source": str(legacy),
    "destination": str(state),
    "status": "migrated" if actions else "no-legacy-state",
    "actions": actions,
}
report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
    echo "  [MIGRATE] $report"
}

replace_managed_content() {
    local agent_dir="$1" stamp="$2" staging
    staging="$(mktemp -d "$agent_dir/.install-staging.XXXXXX")"

    for name in runtime tools workflows templates config registry schemas analysis; do
        "$PYTHON_BIN" - "$CORE_DIR/$name" "$staging/$name" <<'PY'
import shutil
import sys
shutil.copytree(
    sys.argv[1],
    sys.argv[2],
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyc.*", "*.pyo", ".pytest_cache"),
)
PY
    done
    cp "$SCRIPT_DIR/adapters/common/runtime-entry.md" "$staging/agent-entry.md"

    for name in runtime tools workflows templates config registry schemas analysis; do
        rm -rf "$agent_dir/$name"
        mv "$staging/$name" "$agent_dir/$name"
        echo "  [MANAGED] $name/"
    done
    mv "$staging/agent-entry.md" "$agent_dir/agent-entry.md"
    rmdir "$staging"

    # All non-project knowledge is managed. project/ is never removed or overwritten.
    mkdir -p "$agent_dir/knowledge"
    shopt -s dotglob nullglob
    local path base
    for path in "$agent_dir/knowledge"/*; do
        base="$(basename "$path")"
        [[ "$base" == "project" ]] || rm -rf "$path"
    done
    for path in "$CORE_DIR/knowledge"/*; do
        base="$(basename "$path")"
        [[ "$base" == "project" ]] || cp -a "$path" "$agent_dir/knowledge/$base"
    done
    shopt -u dotglob nullglob
    # Force these release metadata files even if --force was not supplied.
    cp "$CORE_DIR/knowledge/VERSION" "$agent_dir/knowledge/VERSION"
    cp "$CORE_DIR/knowledge/CHANGELOG.md" "$agent_dir/knowledge/CHANGELOG.md"
    echo "  [MANAGED] knowledge/platform/ and non-project knowledge"
}

ensure_user_content() {
    local project="$1"
    local agent_dir="$project/.mcu-agent"
    mkdir -p "$agent_dir/state" "$agent_dir/custom/skills" "$agent_dir/custom/workflows" "$agent_dir/custom/registry"
    touch "$agent_dir/custom/skills/.gitkeep" "$agent_dir/custom/workflows/.gitkeep"
    if [[ ! -e "$agent_dir/custom/registry/workflows.yaml" && -f "$CORE_DIR/templates/custom-registry-template.yaml" ]]; then
        cp "$CORE_DIR/templates/custom-registry-template.yaml" "$agent_dir/custom/registry/workflows.yaml"
    fi
    if [[ ! -e "$agent_dir/config.yaml" && -f "$CORE_DIR/templates/mcu-config-template.yaml" ]]; then
        cp "$CORE_DIR/templates/mcu-config-template.yaml" "$agent_dir/config.yaml"
        local escaped_name
        escaped_name="$(basename "$project" | sed 's/[&/]/\\&/g')"
        sed -i.bak "s/<项目名称>/$escaped_name/g" "$agent_dir/config.yaml" && rm -f "$agent_dir/config.yaml.bak"
    fi
    if [[ ! -e "$agent_dir/artifact-manifest.yaml" ]]; then
        cp "$CORE_DIR/templates/artifact-manifest-template.yaml" "$agent_dir/artifact-manifest.yaml"
    fi
    if [[ ! -d "$agent_dir/knowledge/project" ]]; then
        mkdir -p "$agent_dir/knowledge"
        cp -a "$CORE_DIR/knowledge/project" "$agent_dir/knowledge/project"
    fi
}

smoke_test() {
    local agent_dir="$1"
    for name in runtime tools workflows templates config registry schemas analysis knowledge; do
        [[ -d "$agent_dir/$name" ]] || { echo "[ERROR] Smoke test: missing $name" >&2; return 1; }
    done
    [[ -f "$agent_dir/runtime/cli.py" && -f "$agent_dir/agent-entry.md" \
       && -f "$agent_dir/config.yaml" && -f "$agent_dir/artifact-manifest.yaml" ]] || {
        echo "[ERROR] Smoke test: runtime entry or user configuration is incomplete" >&2; return 1;
    }
    PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" "$agent_dir/runtime/cli.py" --help >/dev/null
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$agent_dir" "$PYTHON_BIN" -c \
        'from runtime.workflow_registry import WorkflowRegistry; WorkflowRegistry().descriptor("project-setup")' >/dev/null
    echo "  [SMOKE] runtime CLI and workflow policy OK"
}

write_managed_agents_block() {
    local file="$1"
    "$PYTHON_BIN" - "$file" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
begin = "<!-- BEGIN UNIVERSAL MCU AGENT -->"
end = "<!-- END UNIVERSAL MCU AGENT -->"
block = begin + "\nRead and follow `.mcu-agent/agent-entry.md` for all Universal MCU Agent work.\n" + end
text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
if text.count(begin) != text.count(end):
    raise SystemExit(f"malformed managed block in {path}")
pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
match = pattern.search(text)
if match:
    prefix = text[:match.start()]
    suffix = pattern.sub("", text[match.end():])
    text = prefix + block + suffix
else:
    text = text.rstrip()
    text = (text + "\n\n" if text else "") + block
path.write_text(text.rstrip() + "\n", encoding="utf-8")
PY
    echo "  [ENTRY] managed block: $file"
}

write_managed_gitignore() {
    local file="$1/.gitignore"
    "$PYTHON_BIN" - "$file" <<'PY'
import re
import sys
from pathlib import Path
path = Path(sys.argv[1])
begin = "# BEGIN UNIVERSAL MCU AGENT"
end = "# END UNIVERSAL MCU AGENT"
block = begin + "\n.mcu-agent/state/\n.mcu-agent/workspaces/\n.mcu-agent/backups/\n.mcu-agent/.install-staging.*/\n" + end
text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
if text.count(begin) != text.count(end):
    raise SystemExit(f"malformed Universal MCU Agent gitignore block in {path}")
pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
text = pattern.sub(block, text) if pattern.search(text) else ((text.rstrip() + "\n\n") if text.strip() else "") + block
path.write_text(text.rstrip() + "\n", encoding="utf-8")
PY
    echo "  [GITIGNORE] managed state/workspace block: $file"
}

install_pi_skill() {
    local project="$1"
    local source="$SCRIPT_DIR/adapters/pi/skills/mcu-agent"
    local destination="$project/.agents/skills/mcu-agent"
    [[ -f "$source/SKILL.md" ]] || { echo "[ERROR] Missing pi MCU Skill source" >&2; return 1; }
    mkdir -p "$(dirname "$destination")"
    rm -rf "$destination"
    cp -a "$source" "$destination"
    echo "  [SKILL] $destination"
}

copy_thin_entry() {
    local destination="$1" source="$SCRIPT_DIR/adapters/common/runtime-entry.md"
    mkdir -p "$(dirname "$destination")"
    "$PYTHON_BIN" - "$destination" "$source" <<'PY'
import re
import sys
from pathlib import Path
path, source = Path(sys.argv[1]), Path(sys.argv[2])
begin = "<!-- BEGIN UNIVERSAL MCU AGENT ENTRY -->"
end = "<!-- END UNIVERSAL MCU AGENT ENTRY -->"
body = source.read_text(encoding="utf-8").strip()
block = begin + "\n" + body + "\n" + end
text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
if text.count(begin) != text.count(end):
    raise SystemExit(f"malformed managed entry block in {path}")
pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
if pattern.search(text):
    text = pattern.sub(block, text)
elif text.lstrip().startswith("# Universal MCU Agent Runtime Entry"):
    # Migrate the old installer-owned whole-file entry without preserving a
    # duplicated stale runtime contract.
    text = block
else:
    text = ((text.rstrip() + "\n\n") if text.strip() else "") + block
path.write_text(text.rstrip() + "\n", encoding="utf-8")
PY
    echo "  [ENTRY] managed block: $destination"
}

assert_safe_agent_tree() {
    local project="$1"
    "$PYTHON_BIN" - "$project" <<'PY'
import os
import stat
import sys
from pathlib import Path

project = Path(sys.argv[1]).resolve()
agent = project / ".mcu-agent"
reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
def unsafe(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & reparse)
managed = [
    agent,
    project / ".agents", project / ".agents/skills", project / ".agents/skills/mcu-agent",
    project / ".opencode", project / ".opencode/instructions",
    project / ".opencode/instructions/mcu-agent.md",
    project / "AGENTS.md", project / "CLAUDE.md", project / ".cursorrules",
    project / ".gitignore",
]
for target in managed:
    current = project
    try:
        relative = target.relative_to(project)
    except ValueError:
        raise SystemExit(f"[ERROR] managed installer path escapes project: {target}")
    for component in relative.parts:
        current = current / component
        if unsafe(current):
            raise SystemExit(f"[ERROR] refusing installer operation with linked/reparse managed path: {current}")
if agent.exists():
    if not agent.is_dir() or agent.resolve() != agent:
        raise SystemExit(f"[ERROR] refusing installer operation through linked/reparse .mcu-agent: {agent}")
    for root, directories, files in os.walk(agent, followlinks=False):
        for name in directories + files:
            path = Path(root) / name
            if unsafe(path):
                raise SystemExit(f"[ERROR] refusing installer operation with linked/reparse entry: {path}")
PY
}

deploy_project() {
    local project="$1" stamp existed=false
    local agent_dir="$project/.mcu-agent"
    assert_safe_agent_tree "$project"
    stamp="$(timestamp)"
    [[ -d "$agent_dir" ]] && existed=true
    mkdir -p "$agent_dir"
    if $UPDATE && [[ ! -f "$agent_dir/VERSION" ]]; then
        echo "[ERROR] $agent_dir is not an installed project" >&2
        return 4
    fi
    if [[ -f "$agent_dir/VERSION" ]] && ! $UPDATE && ! $FORCE; then
        echo "  [KEEP] managed runtime already installed; use --update or --force to refresh"
        smoke_test "$agent_dir"
        return
    fi
    # Any operation that can migrate legacy data or replace an existing
    # installation first creates a recoverable user-data backup.
    if $existed || $UPDATE || $FORCE; then
        backup_user_data "$agent_dir" "$stamp"
    fi
    migrate_legacy_state "$agent_dir" "$stamp"
    ensure_user_content "$project"
    replace_managed_content "$agent_dir" "$stamp"
    if ! smoke_test "$agent_dir"; then
        echo "[ERROR] Smoke test failed; VERSION was not updated" >&2
        return 5
    fi
    printf '%s\n' "$PLATFORM_VERSION" > "$agent_dir/VERSION"
    echo "  [VERSION] $PLATFORM_VERSION"
}

remove_legacy_claude() {
    local claude_home="$HOME/.claude"
    rm -rf "$claude_home/skills/mcu"
    local name
    for name in mcu-build mcu-code-qa mcu-datasheet mcu-entry mcu-firmware mcu-orchestrator mcu-project-setup mcu-requirements mcu-schematic; do
        rm -f "$claude_home/commands/$name.md"
    done
}

install_entry_for_agent() {
    local agent="$1" project="$2"
    [[ -n "$project" ]] && write_managed_gitignore "$project"
    case "$agent" in
        codex) write_managed_agents_block "$project/AGENTS.md" ;;
        pi)
            write_managed_agents_block "$project/AGENTS.md"
            install_pi_skill "$project"
            ;;
        claude-code)
            if [[ -n "$project" ]]; then copy_thin_entry "$project/CLAUDE.md"; else copy_thin_entry "$HOME/.claude/CLAUDE.md"; fi ;;
        opencode) copy_thin_entry "$project/.opencode/instructions/mcu-agent.md" ;;
        cursor) copy_thin_entry "$project/.cursorrules" ;;
    esac
}

[[ -n "$PROJECT" ]] && assert_safe_agent_tree "$PROJECT"
echo "Universal MCU Agent Platform v$PLATFORM_VERSION — $AGENT"
$FORCE && echo "  [INFO] --force refreshes managed files only; user data remains protected"
$CLEAN_LEGACY_CLAUDE && remove_legacy_claude

if [[ "$AGENT" == "claude-code" && -z "$PROJECT" ]]; then
    install_entry_for_agent claude-code ""
elif [[ "$AGENT" == "all" ]]; then
    # One deployment, and one shared AGENTS.md block for the pi/Codex entry.
    deploy_project "$PROJECT"
    write_managed_agents_block "$PROJECT/AGENTS.md"
    install_pi_skill "$PROJECT"
    write_managed_gitignore "$PROJECT"
    copy_thin_entry "$PROJECT/CLAUDE.md"
    copy_thin_entry "$PROJECT/.opencode/instructions/mcu-agent.md"
    copy_thin_entry "$PROJECT/.cursorrules"
else
    deploy_project "$PROJECT"
    install_entry_for_agent "$AGENT" "$PROJECT"
fi

echo "Operation complete."
