# out/ — the measurement harness

Code that MEASURES the reader, kept apart from the data it measures. Nothing
in this folder contains corpus material, and nothing in it writes into this
repository.

    $PDF2MMD_LIBRARY   where the corpus is       default ~/pdfdrill-library
    $PDFDRILL_SRC      PDFDRILL's src/           default ~/MX/PDFDRILL/src
    $PDF2MMD_CODE      the reader                default the parent of this folder

The corpus (`wzlxjtu-*`, each with its `golden/`) is licensed to TEES, Texas
A&M — "not to be reproduced or disclosed without written authorization". It
lives outside this repository and must stay there. So does every build these
scripts produce.

## What is here

| | |
|---|---|
| `corpusrun.py` | run the reader over all 102 documents, and install the result |
| `sweep.py` | which gold equations are not reproduced, and the refusals behind them |
| `canon.py` | the dialect-aware comparison: is this reading right, however spelled |
| `eqtable_716.py` | the four-column table — No, gold, MathPix, pdf2mmd |
| `test_table.py` | the table pipeline, fed deliberately malformed elements |

## The loop

```bash
cd ~/pdf2mmd/out

python3 corpusrun.py /tmp/run --install     # 1. convert, and install
python3 sweep.py                            # 2. what is still missing
python3 canon.py                            # 3. correct / arranged / wrong
python3 eqtable_716.py                      # 4. the table, to look at
```

Step 1 writes to a scratch directory and, with `--install`, copies each
document's output to `<doc>/pdf2mmd/page.md` in the library. **Steps 2–4 read
only the library.** A run you have not installed cannot change a measurement
by accident — which is the point of the separation.

## Building the 716 table, in detail

```bash
python3 eqtable_716.py                               # default output path
python3 eqtable_716.py --out /somewhere/table.tex    # elsewhere
python3 eqtable_716.py --no-compile                  # .tex only
python3 eqtable_716.py --no-unmatched                # omit the per-document
                                                     # table of blocks that
                                                     # match no gold equation
```

Blocks matching no gold equation are shown BY DEFAULT, in a small table of
their own after each document's. They used to be counted in the caption and
not shown, and 61 of them (MathPix 8, pdf2mmd 53) never appeared anywhere:
a reader looking for a document's Nth equation found N-1 rows and no sign of
the rest. Counted is not shown. They are kept out of the main table because
every row there exists to be read ACROSS -- gold beside the two readings of
it -- and a row with an empty gold column breaks that run.

Run `python3 -m pytest test_table.py -q` after changing anything in this
folder. It feeds unbalanced braces, an unpaired `\left`, a comment
character, a parameter character, a subscript in text mode, an empty block
and a 4000-character line through the whole chain, and asserts the two
properties the instrument depends on: every gold equation gets a row, and
every block a source emitted is either matched or listed.

It writes `716-equations.tex` and compiles it to `716-equations.pdf`. Five
things happen, in this order:

1. **Read three sources per document.** Gold from `golden/*_gt.tex` — eleven
   display environments occur in this corpus and all are taken, plus `\[..\]`
   and a `center` block whose content is mathematics. MathPix from
   `<slug>.md`. This reader from `pdf2mmd/page.md`.

2. **Match by CONTENT, never by position.** The three sources do not agree on
   how many blocks an equation is, so the Nth block of one is not the Nth of
   another. `key()` normalises spelling — `\mid` for `|`, `{a\over b}` for
   `\frac{a}{b}`, font commands and spacing away — and every gold equation
   takes its best unit above 0.35 similarity. A unit is a whole block OR one
   row of an `aligned`, because a fused run can hold several authored
   equations. Greedy, highest ratio first, each unit claimed once.

3. **Judge every cell on its own.** Each `$\displaystyle …$` is set alone in
   a probe document and compiled; a line number is then a cell. Only the
   FIRST erroring line of a pass is a verdict — TeX cascades — so that cell
   is dropped and the probe runs again. A cell that fails is shown as its
   SOURCE, marked, never as a blank: an empty cell that means "did not
   render" is indistinguishable from one that means "nothing was read", and
   that confusion is what this table exists to prevent.

4. **Compile to a fixpoint.** `report_tex.compile_fixpoint` demotes any row
   that still errors and recompiles. After step 3 there is normally nothing
   left to demote; `compile: (pages, errors, demoted)` should end `, 0)`.

5. **Stamp it.** Page 1 carries the reader's commit and the build time, so a
   table and the code that made it can be matched at a glance.

### What it needs

- `pdfdrill.report_tex` from the PDFDRILL repository, for the preamble and
  the compile fixpoint. That is the only cross-repository dependency; point
  `$PDFDRILL_SRC` at it.
- `xelatex`. Not pdflatex — the output carries real Unicode.

### Reading the result

`gold N; matched MathPix m, pdf2mmd p; unmatched blocks …` per document. A
dash means that source produced nothing for that equation. `no match` means
the source produced blocks but none of them is this equation. Neither ever
means the sources agree.
