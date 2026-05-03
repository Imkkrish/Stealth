"""Per-request model routing classifier.

`is_coding_problem(text)` returns True when the input looks like a DSA / coding
question that warrants the slower-but-stronger model. Conservative heuristic:
when in doubt, route DSA. Mis-routing chit-chat to the strong model wastes a
bit of latency; mis-routing a real DSA question to the fast model wastes the
candidate's interview. So we bias toward DSA on ambiguity.

The classifier is intentionally a regex / keyword pass — no ML, no extra API
call. It runs on every `ask` and must stay sub-millisecond.
"""

from __future__ import annotations

import re

# DSA / interview-coding keyword vocabulary. Lowercased; matched as whole-word
# substrings on the lowercased input.
_DSA_KEYWORDS = (
    # Problem-statement boilerplate
    "given an array", "given a string", "given a list", "given an integer",
    "given a binary", "given a sorted", "given two", "given the head",
    "given a tree", "given a graph", "given a matrix",
    "find the", "return the", "return all", "return an array",
    "implement", "design a", "write a function", "write code",
    "write a program",
    # Classic problem names
    "two sum", "three sum", "longest substring", "longest palindrome",
    "trapping rain", "merge intervals", "n-queens", "word ladder",
    "regular expression matching", "valid parentheses", "next permutation",
    "rotate image", "rotate array", "spiral matrix", "search in rotated",
    "kth largest", "kth smallest", "container with most water",
    "letter combinations", "remove duplicates", "merge sorted", "merge k",
    "best time to buy", "house robber", "climbing stairs", "edit distance",
    "word break", "decode ways", "subarray sum", "maximum subarray",
    "minimum window", "sliding window", "evaluate division", "course schedule",
    "number of islands", "topological sort",
    # Data structures
    "linked list", "binary tree", "binary search tree", "trie", "heap",
    "priority queue", "hash map", "hash table", "disjoint set", "union find",
    "segment tree", "fenwick tree", "bit array",
    # Algorithm families
    "dynamic programming", "memoization", "tabulation",
    "depth-first", "depth first", "breadth-first", "breadth first",
    "dfs", "bfs", "backtracking", "greedy", "two pointer", "sliding window",
    "binary search", "quicksort", "mergesort", "topological",
    "dijkstra", "bellman-ford", "floyd-warshall", "kruskal", "prim",
    # Complexity / interview lingo
    "time complexity", "space complexity",
    # Platform names
    "leetcode", "hackerrank", "codeforces", "codechef", "interviewbit",
    "codility", "hackerearth", "neetcode", "lintcode", "atcoder",
)

# Code-shape signals — strings that appear in source code dumps. Any one is
# strong enough on its own.
_CODE_SHAPE = (
    "```",  # fenced code block
    "def ",
    "class ",
    "function ",
    "public static",
    "private static",
    "void main(",
    "int main(",
    "#include",
    "import ",
    "from ",
    "->", "::",
    "console.log",
    "System.out",
    "println(",
    "printf(",
)

# Sample-IO signals — common framing on coding-problem pages.
_IO_SIGNALS = (
    "input:", "output:", "example 1:", "example 2:", "example:",
    "constraints:", "explanation:",
)

# Big-O — accept O(n), O(log n), O(n^2), O(n log n), etc. The space after `O(`
# is rare; real big-O uses no space, but we tolerate either.
_BIGO = re.compile(r"\bO\(\s*[a-zA-Z0-9 +*/^lognm()]+\s*\)")

# Bracketed numeric arrays like `[1, 2, 3]` or `[2,7,11,15]`.
_NUMERIC_ARRAY = re.compile(r"\[\s*-?\d+(?:\s*,\s*-?\d+){2,}\s*\]")


def is_coding_problem(text: str) -> bool:
    """Return True when `text` looks like a DSA / coding problem.

    Cheap and conservative — biased toward returning True on ambiguity so the
    candidate gets the stronger model when it matters most.
    """
    if not text:
        return False

    lowered = text.lower()

    for kw in _DSA_KEYWORDS:
        if kw in lowered:
            return True

    for shape in _CODE_SHAPE:
        if shape in text:
            return True

    for sig in _IO_SIGNALS:
        if sig in lowered:
            return True

    if _BIGO.search(text):
        return True

    if _NUMERIC_ARRAY.search(text):
        return True

    return False
