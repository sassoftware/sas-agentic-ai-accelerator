/***************************************************************************************
	Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0

    Scipt to demonstrate the structure and JSON creation for a
      Prompt Experiment Tracker

    It is highly discouraged to work with this script, it serves only as an example
      to demonstrate the data structure behind the Prompt Experiement Tracker
***************************************************************************************/
* Specify a path on the file system if you want to see the actual structure;
filename petfile temp;

* Here is the data structure to create an example prompt experiment tracker json;
data work.promptExperimentTracker;
    length runId 8. systemPrompt userPrompt $32767. model $100. options $1000. response $32767. run_time prompt_length output_length cost cheapest_prompt best_prompt judge_rank judge_best judge_cost 8. judge_model $100. judge_confidence $16. judge_mode $16. judge_panel $512. judge_agreement $16. judge_chairman_model $100. judge_chairman_reasoning $32767.;
	runID=1;
	systemPrompt="test";
	userPrompt="test";
	output;
	runID=1;
	systemPrompt="";
	userPrompt="";
	model="model1";
	options="{test:test}";
	response="Test";
	run_time=1;
	prompt_length=1;
	output_length=2;
	best_prompt=0;
	output;
	runID=1;
	systemPrompt="";
	userPrompt="";
	model="model2";
	options="{test:test}";
	response="Test";
	run_time=1;
	prompt_length=1;
	output_length=2;
	output;
	runID=2;
	systemPrompt="test";
	userPrompt="test";
	best_prompt=1;
	output;
	systemPrompt="";
	userPrompt="";
	model="model1";
	options="{test:test}";
	response="Test";
	run_time=1;
	prompt_length=1;
	output_length=2;
	best_prompt=1;
	output;
	runID=2;
	systemPrompt="";
	userPrompt="";
	model="model3";
	options="{test:test}";
	response="Test";
	run_time=1;
	prompt_length=1;
	output_length=2;
	best_prompt=0;
	output;
run;

proc json out=petfile pretty;
	export work.promptExperimentTracker / nosastags;
run; quit;