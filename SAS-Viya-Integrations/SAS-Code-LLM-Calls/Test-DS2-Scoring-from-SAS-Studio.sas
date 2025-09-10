/*********************************************************************************
    Copyright © 2024, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
    SPDX-License-Identifier: Apache-2.0
*********************************************************************************/

%let viyaHost=%sysfunc(getoption(SERVICESBASEURL));
* Add the endpoint you want to call here - note this example is build for the phi_3_mini_4k model;
* If you change the endpoint you will proabably have to change the options part of the body;
%let llmEndpoint = &viyaHost./llm/phi_3_mini_4k/phi_3_mini_4k;

* Here is a test that can be run in SAS Studio;
proc ds2;
  data _null_;
    dcl package http webreq();
    dcl package json j();
    dcl varchar(100) url;
    dcl varchar(1024) token;
    dcl int rc status tokenType parseFlags;
    dcl int output_length prompt_length;
    dcl double run_time;
    dcl varchar(1024) llmGenerated;

    dcl varchar(32767) character set utf8 body response;

    method run();
      url = %tslit(&llmEndpoint.);
      body = '{"inputs": [{"name": "userPrompt","value": "Make: Volvo, Model:  S60 R 4dr, Price: 35382 $"},{"name": "systemPrompt","value": "Generate an email in English for a car salesman to make an offering for his customer. Provide information about the discounted price, the model and the make. Discount the price of 10% and calculate the new one."}, {"name": "max_options", "value": 2048}]}';
      webreq.createPostMethod(url);
      webreq.setRequestContentType('application/json');
      webreq.setRequestBodyAsString(body);
      webreq.executeMethod();
      status = webreq.getStatusCode();    
      if (status eq 200) then do;
        webreq.getResponseBodyAsString(response, rc);
        rc = j.createParser(response);
        do while (rc = 0);
          j.getNextToken(rc, token, tokenType, parseFlags);
          if(token eq 'output_length') then do;
              j.getNextToken(rc, token, tokenType, parseFlags);
              output_length = token;
          end;
          else if(token eq 'prompt_length') then do;
              j.getNextToken(rc, token, tokenType, parseFlags);
              prompt_length = token;
          end;
          else if(token eq 'response') then do;
              j.getNextToken(rc, token, tokenType, parseFlags);
              llmGenerated = token;
          end;
          else if(token eq 'run_time') then do;
              j.getNextToken(rc, token, tokenType, parseFlags);
              run_time = token;
          end;
        end;
        j.getNextToken(rc, token, tokenType, parseFlags);
        if(token eq 'run_time') then do;
              j.getNextToken(rc, token, tokenType, parseFlags);
              run_time = token;
        end;
        rc = j.destroyParser();
      end;
      put output_length= prompt_length= llmGenerated= run_time=;
    end;
run; quit;

* Clean up;
%symdel viyaHost llmEndpoint;