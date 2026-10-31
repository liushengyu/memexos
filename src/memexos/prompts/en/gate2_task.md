
---

## This review

- The current directory is a knowledge base after compiling (a git repository). Starting point: `BASE = $base`
- This batch's raw sources (read-only):
$raws
- Changed wiki files:
$changed

Useful commands: `git diff $base -- wiki index.md` (the changes), `git show $base:<path>` (the text before), `cat`, `rg`.

Review by the rubric and give your final result strictly in the given JSON schema.
$fix_note
