"""Complexity gate: every function/method must stay at grade A or B.

radon grades: A (1-5), B (6-10), C (11-20), D (21-30), E (31-40), F (41+).
We allow A and B only — anything in the 11-20 range (grade C) fails this
suite, forcing a refactor instead of letting complexity creep in.
"""

from pathlib import Path

from radon.complexity import cc_visit
from radon.visitors import Class, Function

MAX_COMPLEXITY = 10


def _iter_functions(path: Path):
    for source_file in sorted(path.rglob("*.py")):
        for block in cc_visit(source_file.read_text()):
            yield source_file, block


def test_no_function_exceeds_complexity_limit():
    offenders = []

    for source_file, block in _iter_functions(Path("festin")):
        if isinstance(block, Class):
            for inner in block.methods:
                if inner.complexity > MAX_COMPLEXITY:
                    offenders.append((str(source_file), inner.name, inner.complexity))
            continue
        if isinstance(block, Function) and block.complexity > MAX_COMPLEXITY:
            offenders.append((str(source_file), block.name, block.complexity))

    assert not offenders, (
        f"Functions with cyclomatic complexity > {MAX_COMPLEXITY} "
        f"(grade C+): {offenders}. Refactor them into smaller helpers."
    )


def test_all_package_files_parse():
    """cc_visit must see every module that declares functions: a syntax
    error would silently shrink the complexity scan. Modules with only
    constants (black_list, logo) legitimately produce no blocks."""
    sources = list(Path("festin").rglob("*.py"))
    assert len(sources) >= 8

    seen = {str(f) for f, _ in _iter_functions(Path("festin"))}
    constant_only = {
        "festin/__init__.py",
        "festin/black_list.py",
        "festin/logo.py",
        "festin/service/__init__.py",
    }
    expected = {str(f) for f in sources if str(f) not in constant_only}
    assert seen == expected
