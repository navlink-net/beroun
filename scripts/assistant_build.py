#!/usr/bin/env python3
# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The build step that chooses the assistant of an OS Beroun image (owner, 2026-10-31: the build chooses it, never the user).

    scripts/assistant_build.py apply <caroline|claude|codex> <root>     root = a tree that has  and  (a staged copy, an extracted image tree)
    scripts/assistant_build.py list [<root>]                            the providers of a tree

A build has one assistant, and Caroline is always in it: she hosts Communicator and is the default. `apply` makes the tree that of the chosen build:
  * assistants/build.json says which provider is in the slot;
  * the providers that are not in the build are taken out: their folder in assistants, the files they list in assistant.json ("files": paths under ) that
    no provider of the build lists too (the shared ones stay), the views only they have (system/<view>), and their entry in system/services.json.
The shell (runtime/shelld.py) takes a provider whose module is missing as one the build does not have. Nothing of this is on a device after the build: no switch, no files of the others.
Standard library only."""
import json, os, shutil, sys

DEFAULT = "caroline"


def specs(root):
    base = os.path.join(root, "assistants")
    out = {}
    for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        f = os.path.join(base, d, "assistant.json")
        if os.path.isfile(f):
            out[d] = json.load(open(f, encoding="utf-8"))
    return out


def apply(root, chosen):
    found = specs(root)
    if chosen not in found:
        raise SystemExit(f"no provider '{chosen}' in {root} (there are: {', '.join(found)})")
    keep = {DEFAULT, chosen} & set(found)
    drop = set(found) - keep
    kept_files = {f for k in keep for f in found[k].get("files", [])}
    kept_views = {v for k in keep for v in (found[k].get("views") or {}).values()}
    removed = []

    def rm(path):
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
        else:
            return
        removed.append(os.path.relpath(path, root).replace(os.sep, "/"))

    for d in sorted(drop):
        for f in found[d].get("files", []):
            if f not in kept_files:
                rm(os.path.join(root, f))
        for v in (found[d].get("views") or {}).values():
            if v not in kept_views:
                rm(os.path.join(root, "system", v))
        rm(os.path.join(root, "assistants", d))
    svc = os.path.join(root, "system", "services.json")
    if os.path.isfile(svc):
        gone = {found[d]["service"] for d in drop}
        entries = json.load(open(svc, encoding="utf-8"))
        json.dump([e for e in entries if e.get("id") not in gone], open(svc, "w", encoding="utf-8", newline="\n"), indent=1)
    with open(os.path.join(root, "assistants", "build.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"assistant": chosen}, f)
        f.write("\n")
    return {"assistant": chosen, "kept": sorted(keep), "dropped": sorted(drop), "removed": removed}


def main(argv):
    if argv[:1] == ["apply"] and len(argv) == 3:
        r = apply(os.path.abspath(argv[2]), argv[1])
        print(f"assistant of this build: {r['assistant']}; in the image: {', '.join(r['kept'])}; left out: {', '.join(r['dropped']) or 'none'} ({len(r['removed'])} files and folders removed)")
        return 0
    if argv[:1] == ["list"]:
        root = os.path.abspath(argv[1]) if len(argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for k, s in specs(root).items():
            print(f"{k}: {s['name']}, {len(s.get('files', []))} own files")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
