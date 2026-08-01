/********************************************************************************
    Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Build RAG_RUN_COST: what each RAG ingestion run cost to embed.

    The RAG pipeline records every run in `rag_runs` INSIDE the vector store,
    and the Load step publishes that table to CAS as <PREFIX>_RUNS. This
    script joins those published runs to EMBEDDING_FACT_SHEET and applies the
    price, the same way the LLM Usage Report joins LLM_LOGS to
    LLM_FACT_SHEET - one table, one join, no new plumbing.

    Two cost bases, taken from the fact sheet's cost_type:

      Tokens   embed_tokens  x input_token_price   (hosted API embeddings)
      Seconds  embed_seconds x second_cost         (SCR containers)

    WHAT embed_seconds MEANS (owner decision 2026-08-01): it is the embedding
    time of THIS request - the compute the run actually consumed - and that is
    intentional. It is deliberately NOT the container's uptime bill. A
    long-running SCR container costs its hourly rate whether or not anything
    is embedded; this view answers "what did this ingestion consume", which is
    the question you can attribute to a corpus.

    A model absent from the fact sheet produces a missing cost rather than a
    zero, so an unpriced model reads as unknown instead of free.

    Usage: set the macro variables and run. Re-run after each ingestion, or
    schedule it alongside the ingestion job.
********************************************************************************/

/* CAS library holding the fact sheets (see Load-Fact-Sheets.sas) */
%let _rcv_fact_caslib = Public;
/* CAS library the RAG pipeline published its run history to */
%let _rcv_runs_caslib = Public;
/* Published run-history tables: one per RAG setup, comma-separated.
   The Load step names them <PIPELINE PREFIX>_RUNS. */
%let _rcv_runs_tables = RAG_HR_RUNS;
/* Output table */
%let _rcv_out_caslib  = Public;
%let _rcv_out_table   = RAG_RUN_COST;

cas _rcvSess;
caslib _all_ assign;

/* ---- stack every published run table into one ---------------------------- */
data work._rcv_runs;
    length source_table $ 64;
    set
    %let _rcv_i = 1;
    %do %while (%scan(&_rcv_runs_tables., &_rcv_i., %str(,)) ne );
        %let _rcv_t = %scan(&_rcv_runs_tables., &_rcv_i., %str(,));
        &_rcv_runs_caslib..&_rcv_t. (in=_in&_rcv_i.)
        %let _rcv_i = %eval(&_rcv_i. + 1);
    %end;
    ;
    %let _rcv_i = 1;
    %do %while (%scan(&_rcv_runs_tables., &_rcv_i., %str(,)) ne );
        %let _rcv_t = %scan(&_rcv_runs_tables., &_rcv_i., %str(,));
        if _in&_rcv_i. then source_table = "&_rcv_t.";
        %let _rcv_i = %eval(&_rcv_i. + 1);
    %end;
run;

/* ---- normalise the run measures to numeric --------------------------------
   The history publish stages its CAS tables as varchar (see the Load step's
   cas_stage), so a table published before that was fixed carries its counts
   and timings as text. Reading them through input() costs nothing when they
   are already numeric and rescues them when they are not, so this view works
   against old and new publishes alike.                                     */
data work._rcv_runs_n;
    set work._rcv_runs;
    length _chunks_written _embed_calls _embed_tokens _embed_seconds
           _collection_chunks _docs_ingested 8;
    _chunks_written    = input(cats(chunks_written),    ?? best32.);
    _collection_chunks = input(cats(collection_chunks), ?? best32.);
    _docs_ingested     = input(cats(docs_ingested),     ?? best32.);
    _embed_calls       = input(cats(embed_calls),       ?? best32.);
    _embed_tokens      = input(cats(embed_tokens),      ?? best32.);
    _embed_seconds     = input(cats(embed_seconds),     ?? best32.);
run;

/* ---- normalise the prices to numeric -------------------------------------
   The fact sheets use "." as the not-applicable placeholder, so a column
   that is blank for every SCR model imports as CHARACTER on one load and
   NUMERIC on another, depending on what proc import guessed. cats() renders
   either, and the ?? modifier turns "." into a missing value instead of a
   log full of invalid-data notes. Without this the join silently fails with
   "Expression using multiplication (*) requires numeric types".            */
data work._rcv_facts;
    set &_rcv_fact_caslib..EMBEDDING_FACT_SHEET;
    length _unit_tokens _unit_seconds 8;
    _unit_tokens  = input(cats(input_token_price), ?? best32.);
    _unit_seconds = input(cats(second_cost),       ?? best32.);
run;

/* ---- price them ---------------------------------------------------------- */
proc sql;
    create table work._rcv_cost as
    select
        r.source_table            label = 'Published run table',
        r.run_id                  label = 'Run ID',
        r.rag_project             label = 'RAG project',
        r.collection              label = 'Collection',
        r.backend                 label = 'Vector database',
        r.started_at              label = 'Run start',
        r.finished_at             label = 'Run end',
        r.status                  label = 'Run status',
        r.embed_model             label = 'Embedding model',
        f.provider                label = 'Provider',
        f.deployment_type         label = 'Deployment',
        f.cost_type               label = 'Cost basis',
        r._docs_ingested          as docs_ingested       label = 'Documents ingested',
        r._chunks_written         as chunks_written      label = 'Chunks written',
        r._collection_chunks      as collection_chunks   label = 'Live chunks in collection',
        r._embed_calls            as embed_calls         label = 'Embedding calls',
        r._embed_tokens           as embed_tokens        label = 'Embedding tokens',
        r._embed_seconds          as embed_seconds       label = 'Embedding seconds',
        /* the price that applied, so a row can be audited without the sheet */
        case when f.cost_type = 'Tokens'  then f._unit_tokens
             when f.cost_type = 'Seconds' then f._unit_seconds
             else . end           as unit_price     label = 'Unit price',
        /* missing, not zero, when the model is unknown or unpriced */
        case when f.cost_type = 'Tokens'  and f._unit_tokens  ne .
                  then r._embed_tokens  * f._unit_tokens
             when f.cost_type = 'Seconds' and f._unit_seconds ne .
                  then r._embed_seconds * f._unit_seconds
             else . end           as embed_cost     label = 'Embedding cost',
        /* what a rebuild of the whole collection would cost at this rate */
        case when r._chunks_written > 0 and calculated embed_cost ne .
                  then calculated embed_cost / r._chunks_written
             else . end           as cost_per_chunk label = 'Cost per chunk'
    from work._rcv_runs_n as r
    left join work._rcv_facts as f
      on r.embed_model = f.model_id
    order by r.started_at desc;
quit;

/* ---- publish ------------------------------------------------------------- */
proc casUtil incaslib="&_rcv_out_caslib." outcaslib="&_rcv_out_caslib.";
    droptable casdata="&_rcv_out_table." quiet;
    load data=work._rcv_cost casout="&_rcv_out_table.";
    promote casdata="&_rcv_out_table." casout="&_rcv_out_table.";
    save casdata="&_rcv_out_table." casout="&_rcv_out_table." replace;
run; quit;

/* ---- say what could not be priced, rather than leaving it to be noticed --- */
proc sql noprint;
    select count(distinct embed_model) into :_rcv_unpriced trimmed
    from work._rcv_cost where embed_cost = . and embed_model is not null;
quit;
%put NOTE: RAG_RUN_COST built in &_rcv_out_caslib..;
%if %sysevalf(&_rcv_unpriced. > 0) %then %do;
    %put WARNING: &_rcv_unpriced. embedding model(s) have no price in EMBEDDING_FACT_SHEET;
    %put WARNING- their runs show a missing cost. Reload the fact sheet or add the model.;
%end;

proc datasets lib=work nolist;
    delete _rcv_runs _rcv_runs_n _rcv_cost _rcv_facts;
run; quit;

cas _rcvSess terminate;
%symdel _rcv_fact_caslib _rcv_runs_caslib _rcv_runs_tables
        _rcv_out_caslib _rcv_out_table _rcv_i _rcv_t _rcv_unpriced / nowarn;
