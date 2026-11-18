"""Advanced OpenFOAM controls and user-edited case files, applied to a freshly written case.

The case writers produce sensible defaults for each study. Two layers on top let an expert change anything:
  * advanced settings (convection scheme, correctors, relaxation, Courant number, solver tolerances): small edits of
    the generated dictionaries, so they work the same for every study;
  * case-file overrides: the full text of any dictionary under system/, constant/ (not the mesh) or 0/, 0.orig/,
    written over the generated file before every run.
"""
from __future__ import annotations

import re
from pathlib import Path

EDITABLE_ROOTS = ("system", "constant", "0", "0.orig")
MAX_FILE_BYTES = 2_000_000


def check_rel(rel: str) -> str:
    """A safe case-relative path of an editable dictionary (raises ValueError otherwise)."""
    rel = str(rel).replace("\\", "/").strip("/")
    parts = rel.split("/")
    if (not rel or parts[0] not in EDITABLE_ROOTS or any(x in ("", ".", "..") for x in parts)
            or "polyMesh" in parts or len(parts) > 5 or not re.fullmatch(r"[A-Za-z0-9_.+\-/]+", rel)):
        raise ValueError(f"'{rel}' is not an editable case file (system/, constant/ except the mesh, 0/ or 0.orig/)")
    return rel


# OpenFOAM dictionaries can run programs: #codeStream / #calc compile and run C++ while the file is read, coded
# boundary conditions, sources and function objects compile code, systemCall runs shell commands, libs (...) loads any
# DLL by path. An edited file is stored in project.json and written into the case before every run, so a study folder
# from someone else could carry such a file: these constructs are refused (here when a file is saved, again before the
# run). The generated case never needs them.
_CODE_WORDS = {"systemCall": "systemCall (runs commands)", "executeCalls": "systemCall commands",
               "writeCalls": "systemCall commands", "endCalls": "systemCall commands"}
# a code entry of a coded condition / function object / source: code, codeInclude, codeOptions, ... localCode
_CODE_ENTRY = re.compile(r"\b(code[A-Z]\w{0,40}|code|localCode)\b(?=\s*(#\{|\{|\"|\$))")
_WORD = re.compile(r"\w+")
_DIRECTIVE = re.compile(r"#\s*(\w+)[ \t]*([^\s;]*)")
_CASE_TAGS = ("<case>", "<system>", "<constant>")
# a library list key: any word ending in libs / Libs (libs, motionSolverLibs, functionObjectLibs, ...); the value is
# read token by token (no regular expression over the whole value: linear time on any input)
_LIBS = re.compile(r"(?<!\w)(\w{0,60}[Ll]ibs)\b")
_LIB_NAME = re.compile(r"\"?(lib)?[A-Za-z][A-Za-z0-9_]{0,100}(\.(so|dll|dylib))?\"?")
_LIB_KEYS = ("libs", "motionSolverLibs", "functionObjectLibs", "Libs")
# a quoted key is a regular expression in OpenFOAM ("(U|k)" {...}, ".*" {...}); one whose value is a list and that could
# stand for a library key is refused
_QUOTED_KEY = re.compile(r'(?m)^[ \t]*"([^"\n]{1,200})"\s*(\S?)')  # (group 2: the first character of the value)
_LIB_EXT = (".so", ".dll", ".dylib")
_TOKEN_SPLIT = re.compile(r"[\s\"'();{}]+")


def _strip_comments(text: str) -> str:
    """The text without // and /* */ comments, skipping over "strings" (a // inside a string is not a comment).
    A single pass over the text."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            j = i + 1
            while j < n and text[j] not in '"/':
                j += 1
            out.append(text[i:j])
            i = j
    return "".join(out)


def _lib_list_ok(body: str, start: int) -> bool:
    """The value after a library key at body[start:] is a literal list '( name name ... )' of bare library names."""
    end = body.find(";", start)
    v = body[start:end if end >= 0 else len(body)].strip()
    if len(v) > 4000 or not (v.startswith("(") and v.endswith(")")):
        return False
    return all(_LIB_NAME.fullmatch(tok) for tok in v[1:-1].split())


def _key_could_be_libs(pattern: str) -> bool:
    if "ibs" in pattern.lower():
        return True
    try:
        rx = re.compile(pattern)
    except re.error:
        return True  # (a pattern Python cannot read: assume the worst)
    return any(rx.fullmatch(k) for k in _LIB_KEYS)


def check_safe(rel: str, text: str) -> None:
    """Raise ValueError if a case dictionary could run code, include a file from outside the case or load a library
    from a path. The checks run on the raw text and again on the text without comments, so neither a comment nor a
    string that looks like one can hide an entry. Every check is linear in the length of the text."""
    def refuse(what):
        raise ValueError(f"{rel}: {what} - not allowed in case files, it could run programs on this computer. Use the "
                         "generated settings, or run the case by hand outside the workbench.")

    if len(text) > MAX_FILE_BYTES:
        refuse("a file this large")
    for body in (text, _strip_comments(text)):
        if "#{" in body:
            refuse("a verbatim code block #{ ... #}")
        if _CODE_ENTRY.search(body):
            refuse("a code entry (compiled and run)")
        # words anywhere, so that 'type $t;' with 't systemCall;' elsewhere is caught too
        for m in _WORD.finditer(body):
            w = m.group(0)
            if w in _CODE_WORDS:
                refuse(_CODE_WORDS[w])
            if w.startswith("coded") or "Coded" in w:
                refuse(f"a coded type ({w[:40]}: compiles and runs code)")
        for m in _DIRECTIVE.finditer(body):
            word, arg = m.group(1).lower(), m.group(2).strip("\"'")
            if "codestream" in word or "calc" in word:
                refuse("#codeStream / #calc (compiles and runs code)")
            if "include" in word and (not arg or arg[0] in "/\\~$" or "$" in arg or ".." in arg
                                      or re.match(r"[A-Za-z]:", arg)
                                      or (arg.startswith("<") and not arg.startswith(_CASE_TAGS))):
                refuse(f"an include of a file outside the case (#{m.group(1)} {m.group(2)[:60]})")
        # a library file named by path, under any key
        for tok in _TOKEN_SPLIT.split(body):
            low = tok.lower()
            if any(e in low for e in _LIB_EXT) and any(c in tok for c in "/\\:~$"):
                refuse(f"a library given by path ({tok[:80]})")
        for m in _QUOTED_KEY.finditer(body):
            if m.group(2) != "{" and _key_could_be_libs(m.group(1)):
                refuse(f"a pattern key that could load libraries (\"{m.group(1)[:60]}\")")
        for m in _LIBS.finditer(body):
            if not _lib_list_ok(body, m.end()):
                refuse(f"'{m.group(1)}' that is not a plain list of OpenFOAM library names (like (forces "
                       "\"libfieldFunctionObjects.so\"))")


def list_files(case: Path) -> list[str]:
    out = []
    for root in EDITABLE_ROOTS:
        d = case / root
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            rel = f.relative_to(case).as_posix()
            if f.is_file() and "polyMesh" not in rel and f.stat().st_size <= MAX_FILE_BYTES:
                out.append(rel)
    return out


def _block(text: str, name: str):
    """(start, end) of a top-level dictionary 'name { ... }' (brace matched), or None."""
    m = re.search(rf"(?m)^{re.escape(name)}\s*\{{", text) or re.search(rf"(?m)^{re.escape(name)}\s*\n\{{", text)
    if not m:
        return None
    i, depth = text.index("{", m.start()), 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return m.start(), j + 1
    return None


def _sub_in_block(text, block, pattern, repl):
    b = _block(text, block)
    if not b:
        return text, 0
    inner, n = re.subn(pattern, repl, text[b[0]:b[1]])
    return text[:b[0]] + inner + text[b[1]:], n


def apply(case: Path, adv: dict, overrides: dict, log=lambda *_: None) -> list[str]:
    """Apply the advanced settings, then the file overrides. Returns a list of what was changed."""
    done = []
    adv = adv or {}
    schemes = list(case.glob("system/**/fvSchemes")) + list(case.glob("system/fvSchemes"))
    solutions = list(case.glob("system/**/fvSolution")) + list(case.glob("system/fvSolution"))
    conv = adv.get("convection", "default")
    if conv and conv != "default":
        for f in set(schemes):
            s = f.read_text()

            def rep(m):
                var = m.group(2)
                if var in ("K", "Ekp") or m.group(1).startswith("div(Ji"):
                    return m.group(0)
                scheme = {"first order": "bounded Gauss upwind", "second order": f"bounded Gauss linearUpwind grad({var})",
                          "LUST": f"bounded Gauss LUST grad({var})"}[conv]
                return f"{m.group(1)}{' ' * max(1, 16 - len(m.group(1)))}{scheme};"
            s2 = re.sub(r"(div\(phi,(\w+)\))\s+[^;\n]+;", rep, s)
            if s2 != s:
                f.write_text(s2, newline="\n")
        done.append(f"convection scheme: {conv}")
    nn = adv.get("non_orth")
    if nn is not None and nn >= 0:
        for f in set(solutions):
            s = f.read_text()
            s2 = re.sub(r"nNonOrthogonalCorrectors\s+\d+;", f"nNonOrthogonalCorrectors {int(nn)};", s)
            if s2 != s:
                f.write_text(s2, newline="\n")
        done.append(f"non-orthogonal correctors: {int(nn)}")
    relax = {k: adv.get(k) for k in ("relax_U", "relax_p", "relax_h", "relax_turb") if adv.get(k)}
    if relax:
        for f in set(solutions):
            s = f.read_text()
            if "relax_U" in relax:
                s, _ = _sub_in_block(s, "relaxationFactors", r"(\bU\s+)[0-9.eE+-]+;", rf"\g<1>{relax['relax_U']:g};")
            if "relax_p" in relax:
                s, _ = _sub_in_block(s, "relaxationFactors", r"(\b(?:p|p_rgh)\s+)[0-9.eE+-]+;", rf"\g<1>{relax['relax_p']:g};")
            if "relax_h" in relax:
                s, _ = _sub_in_block(s, "relaxationFactors", r"(\bh\s+)[0-9.eE+-]+;", rf"\g<1>{relax['relax_h']:g};")
            if "relax_turb" in relax:
                s, _ = _sub_in_block(s, "relaxationFactors", r'("\(k\|[^"]*"\s+)[0-9.eE+-]+;', rf"\g<1>{relax['relax_turb']:g};")
            f.write_text(s, newline="\n")
        done.append("relaxation: " + ", ".join(f"{k[6:]} {v:g}" for k, v in relax.items()))
    for key, word in (("n_correctors", "nCorrectors"), ("n_outer", "nOuterCorrectors")):
        v = adv.get(key)
        if v:
            for f in set(solutions):
                s = f.read_text()
                if not _block(s, "PIMPLE"):
                    continue
                s2, n = _sub_in_block(s, "PIMPLE", rf"\b{word}\s+\d+;", f"{word} {int(v)};")
                if not n:
                    b = _block(s2, "PIMPLE")
                    i = s2.index("{", b[0]) + 1
                    s2 = s2[:i] + f"\n    {word} {int(v)};" + s2[i:]
                f.write_text(s2, newline="\n")
            done.append(f"{word}: {int(v)}")
    co = adv.get("max_co")
    cd = case / "system" / "controlDict"
    if co and cd.exists():
        s = cd.read_text()
        s2 = re.sub(r"(?m)^maxCo\s+[0-9.eE+-]+;", f"maxCo           {co:g};", s)
        if s2 != s:
            cd.write_text(s2, newline="\n")
            done.append(f"max Courant number: {co:g}")
    tf = adv.get("tol_factor")
    if tf and tf != 1:
        for f in set(solutions):
            s = f.read_text()
            s2 = re.sub(r"(\btolerance\s+)([0-9.eE+-]+);", lambda m: f"{m.group(1)}{float(m.group(2)) * tf:.3g};", s)
            if s2 != s:
                f.write_text(s2, newline="\n")
        done.append(f"linear-solver tolerances x {tf:g}")
    for rel, text in (overrides or {}).items():
        rel = check_rel(rel)
        check_safe(rel, text)  # (project.json could have been edited outside the workbench)
        p = case / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, newline="\n")
        done.append(f"edited file: {rel}")
    # the whole case, not only the edited files: names and values from project.json end up in the dictionaries too
    for rel in list_files(case):
        check_safe(rel, (case / rel).read_text(errors="replace"))
    for d in done:
        log("Advanced: " + d)
    return done
