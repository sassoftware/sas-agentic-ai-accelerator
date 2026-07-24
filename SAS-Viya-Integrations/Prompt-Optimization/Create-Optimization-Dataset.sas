/*********************************************************************************
    Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Template: build a governed CAS dataset for Prompt Builder optimization
    (the "Use a CAS table" dataset source of the Optimize section).

    Schema the optimize job expects (column names are matched case-insensitively):
      - ONE COLUMN PER PROMPT VARIABLE, named exactly like the variable in the
        Prompt Builder's variables manager (e.g. a prompt using {{word}} needs
        a column "word").
      - If the prompt has NO variables, a single column "userPrompt" holding
        the full user prompt of each example.
      - A column "response" holding the reference answer the optimization
        should steer toward. Rows with an empty response are skipped.

    How to use:
      1. Replace the example variable column(s) below with your prompt's
         variables and fill in your rows (or load them from any table you
         already govern - only the column names matter).
      2. Pick the caslib. It must be accessible from the compute context that
         runs the optimize job; a shared caslib (like Public) lets a whole
         team maintain the dataset.
      3. Run this program, then enter the caslib + table name in the Optimize
         section's "Use a CAS table" fields.

    The optimize job snapshots the exact rows it used into the prompt's model
    (Prompt-Optimization-Dataset-<n>.json), so later edits to this table never
    orphan the provenance of past runs.
*********************************************************************************/

%let dataset_caslib  = Public;
%let dataset_table   = PROMPT_OPTIMIZATION_DATASET;

cas _optbuild;
caslib _all_ assign sessref=_optbuild;

/* One row per example: the variable column(s) + the reference response.
   Replace "word" with your prompt's variable name(s) - one column each. */
data casuser._staging;
    length word $ 200 response $ 2000;
    infile datalines dsd truncover;
    input word $ response $;
    datalines;
hot,cold
big,small
fast,slow
light,dark
happy,sad
open,closed
early,late
strong,weak
;
run;

/* Load + promote into the target caslib so the optimize job's compute
   session can see the table. Re-running replaces the previous version. */
proc casutil session=_optbuild;
    droptable casdata="&dataset_table." incaslib="&dataset_caslib." quiet;
run;
data &dataset_caslib..&dataset_table. (promote=yes);
    set casuser._staging;
run;
proc casutil session=_optbuild;
    droptable casdata="_staging" incaslib="casuser" quiet;
run; quit;

cas _optbuild terminate;
