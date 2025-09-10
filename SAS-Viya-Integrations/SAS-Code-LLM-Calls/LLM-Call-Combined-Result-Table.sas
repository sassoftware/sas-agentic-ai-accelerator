/*********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Turns multiple LLM response into a table for tracking

    This script is meant to be run after the
      LLM-Call-Result-Table.sas has run

    The output table is called:
      work._crc_result_table_combined

    crc is short for:
      Create Results Combined
*********************************************************************************/
%macro _crc_append_results(_crc_runID);
    %local _crc_runID
        _crc_models
        _crc_i;

    proc sql noPrint;
        select count(memname) into :_crc_models
            from dictionary.members
                where libname EQ 'WORK' and memtype EQ 'DATA' and memname contains "_CRT_RESULT_TABLE_&_crc_runID._";
    run; quit;

    %if &_crc_runID. EQ 1 %then %do;
        data work._crc_result_table_combined;
            set
            %do _crc_i = 1 %to &_crc_models.;
                %if &_crc_i. EQ 1 %then %do;
                    work._crt_result_table_&_crc_runID._&_crc_i.
                %end;
                %else %do;
                    work._crt_result_table_&_crc_runID._&_crc_i.(where=(systemPrompt=''))
                %end;
            %end;
            ;
            runId = &_crc_runID.;
        run;
    %end;
    %else %do;
        data work._crc_result_table_append;
            set
            %do _crc_i = 1 %to &_crc_models.;
                %if &_crc_i. EQ 1 %then %do;
                    work._crt_result_table_&_crc_runID._&_crc_i.
                %end;
                %else %do;
                    work._crt_result_table_&_crc_runID._&_crc_i.(where=(systemPrompt=''))
                %end;
            %end;
            ;
            runId = &_crc_runID.;
        run;

        proc append base=work._crc_result_table_combined data=work._crc_result_table_append;
        run; quit;

        * Clean up;
        proc datasets lib=work noList;
            delete _crc_result_table_append;
        run; quit;
    %end;
%mend _crc_append_results;

%_crc_append_results(1);

* Clean up;
%sysmacdelete _crc_append_results;