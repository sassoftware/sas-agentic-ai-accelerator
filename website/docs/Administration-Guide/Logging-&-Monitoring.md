---
sidebar_position: 9
---

# Logging

The logs from the LLM containers is standardized across all of the different models.
This has been done in order to be able to parse all of the relevant information from the logs and create monitoring on top of them.

In order to make use of these logs, which are written to the standard out of each container, you must collect the logs.
If you do not have an established logging and monitoring process, you can use the simplified logging script provided below.
Otherwise, if you do have an established way of collecting logs, ensure that you can export them to a folder that can be accessed from SAS Studio as a `.log` file (as that it is the input for the parsing utilities).

For more information on how to parse the log and load it into to SAS please take a look at `SAS-Viya-Integrations/Logging-Monitoring`.

## Simplified Logging via a Script

Only use this script if you do not have a more robust process in place.

Create a script—for example `collect_llm_logs.sh`—and ensure that it is executable.
Use the code below and change both the namespace and the log path.
The namespace should be the namespace into which you have deployed the models, and the path needs to be reachable from inside of SAS Studio.
You can also change the logging interval; note that any changes you make here will have to be reflected in the entry for the `crontab`.
The default here also replaces the log file as the assumption is that you pick up the new entries using the parsing script and append (though a full replacement is also supported).

```bash
# Set the script variables for your environment
llm_namspace="llm"
llm_log_path="/viya-share/pvs/sasdata/data/llm/llms.log"
llm_logging_interval="1h"
# Remove the next line to append to the log file isntead
rm $llm_log_path
for pod in $(kubectl get pods -n $llm_namspace -o name); do
  kubectl logs -n $llm_namspace $pod --all-containers --since=$llm_logging_interval >> $llm_log_path
done
```

Next add the following entry to your crontab (`crontab -e`), adjusting your path to the script:

```bash
0 * * * * /viya-share/pvs/sasdata/data/llm/collect_llm_logs.sh
```

This entry will run the log collection script at the top of every hour.