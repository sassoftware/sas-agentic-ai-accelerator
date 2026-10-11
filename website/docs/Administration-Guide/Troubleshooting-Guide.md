---
title: Troubleshooting Guide
---

# Troubleshooting Guide

The Troubleshooting Guide tries to provide solution orientied fixes to reported issues. If your issue is not on this please raise an issue or if you already have a solution please contribute it as a pull request.

## SAS Intelligent Decisioning

This part of the troubleshooting guide describes issues that can occur when working with this in SAS Intelligent Decisioning.

### Duplicate declaration for VARIABLE

This error can be encountered during testing within SAS Intelligent Decisioning. Here is how the issue might show up for a variable called **llmurl**:

![Duplicate Variable Error](../../static/Duplicate-Variable-Error.png)

```SAS
ERROR: Compilation error.
ERROR: Line 167: Duplicate declaration for llmurl.
```

This error occurs when your model (for example the prompt template model) has the same variable twice in its *Variable* tab inside of SAS Model Manager. If that is the case, you have identified the source of the error. In order to fix this, please ensure that you are using the current version of the *Prompt Builder UI* (compare the versions using the [project CHANGELOG](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/CHANGELOG.md)). If you created the model not using that tool, please make sure that model variables are correctly updated when creating new versions of the model.

### DS2 "pymas" package encountered a failure in the 'execute' method

This error can be encountered when testing or running a decision that contains a prompt template that was manifested with the *Include the LLM call in the manifested model* option:

```SAS
ERROR: Line 53: DS2 "pymas" package encountered a failure in the 'execute' method.
```

It means that the Python score code of the model raised an unhandled error. The most common cause is the LLM call itself failing — for example because the certificate of the endpoint hosting the LLM containers is not trusted or the endpoint is not reachable from where the model runs. Here is how to address it:

1. Ensure that you are using the current version of the *Prompt Builder UI* (compare the versions using the [project CHANGELOG](https://github.com/sassoftware/sas-agentic-ai-accelerator/blob/main/CHANGELOG.md)) and manifest the prompt again. Since version 1.0.0 a failing LLM call no longer raises an error: the failure reason is returned through the *response* output of the model (e.g. *LLM call failed: ...*) and *parse_status* returns 0. Keep the *response* output selected while validating a decision, so you can see why a call failed.
2. If the *response* output reports a TLS/certificate problem: the model verifies the connection against the CA bundle at */security/trustedcerts.pem*, which SAS Viya mounts into the pods of CAS and of the compute servers and which covers LLM containers hosted behind the SAS Viya ingress. If your LLM containers are hosted elsewhere, point the *LLMCONTAINERCABUNDLE* environment variable to a CA bundle that covers them — see [Deployment of Decisions](./Deployment-of-Decisions.md) for all supported environment variables and how to set them per runtime. If the problem only shows once the decision is published to MAS, see [CERTIFICATE_VERIFY_FAILED in the response of a published model](#certificate-verify-failed-published-model) below.
3. If the model fails at publish time instead (*py.publish() failed*), the *requests* package is most likely missing from the Python environment. The manifested model ships a *requirements.json*, so SAS Container Runtime destinations install the package automatically during publishing; for runs in SAS Intelligent Decisioning tests, CAS or MAS, an administrator has to make the *requests* package available in the configured Python environment.

Please note that the *Call LLM node* path (prompt templates manifested without the included LLM call) is not affected by this — for that path see the Micro Analytic Service and SAS Container Runtime sections below.

## Micro Analytic Service

This part of the troubleshooting guide describes issues that can occur when publishing decisions or models that call the LLMs to the SAS Micro Analytic Service (MAS).

### 500 Internal Server Error - EXECUTEMETHOD failed for HTTP POST method with URL

This error occurs because the endpoint under which the LLMs are hosted doesn't have a valid certificate from the standpoint of your SAS Micro Analytic Service. This fix has to be done by an administrator with the ability to patch the SAS Viya deployment - for more information check out the [SAS documentation](https://go.documentation.sas.com/doc/en/mascdc/default/masag/p0kuxnv51taahin1cf23yhr0g23z.htm):

1.   Go to *$BASE_PATH/sasinstall/sas-bases/examples/sas-microanalytic-score/config* and copy the *mas-add-environment-variables.yaml*
2.   Then adjust the yaml in the following way - this assumes that you have deployed the LLMs behind the same ingress as SAS Viya if not you need to point it to your certificate accordingly:

```yaml
---
apiVersion: builtin
kind: PatchTransformer
metadata:
  name: mas-add-environment-variables
patch: |-
  - op: add
    path: /spec/template/spec/containers/0/env/-
    value:
      name: SSLCALISTLOC
      value: /security/trustedcerts.pem
target:
  group: apps
  kind: Deployment
  name: sas-microanalytic-score
```

3.   Add the transformer to your *kustomization.yaml* and apply it.
4.   Check the environment variables of the sas-microanalytic-score pod definition and there under the sas-microanalytic-score container check that the environment variable was added accordingly.
5.   Now rerun your the decision/model call and check that the error has been actually fixed.

### CERTIFICATE_VERIFY_FAILED ... self-signed certificate in certificate chain in the response of a published model {#certificate-verify-failed-published-model}

This error can be encountered in the publishing validation of a decision on a MAS destination, when the decision contains a model that calls the LLM or decision model container itself: a prompt template manifested with the *Include the LLM call in the manifested model* option, a decision template, or the retrieval model of a RAG setup. The validation ends without an error, and every row carries the message in the model's text output (*response*, *answer* or *retrieval_status*) while the other outputs are empty:

```text
Decision model call failed: HTTPSConnectionPool(host=..., port=443): Max retries exceeded with url: /llm/von_1_2/von_1_2 (Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain')))
```

The same decision returns answers in the in-app test of SAS Intelligent Decisioning, and a decision that uses the *Call LLM node* works on the same destination. The cause is that MAS runs the Python code of the model in a separate runner container that does not have the CA bundle (*/security/trustedcerts.pem*) the main container has. This fix has to be done by an administrator with the ability to patch the SAS Viya deployment:

1.   Confirm the cause and mount the bundle file into the Python runner container as described in [Publishing to SAS Micro Analytic Service: the certificate bundle](./Deployment-of-Decisions.md#mas-certificate-bundle) — the one file with a *subPath* mount, not the whole *security* volume, which also holds the private key directory of the pod. That keeps TLS verification on and is the fix for production.
2.   Check that */security/trustedcerts.pem* is listed inside the runner container, and that nothing else is under */security* there, with the commands given there.
3.   Run the publishing validation again and check that the rows carry answers.

On a test system with a closed cluster network, verification can be switched off instead — with the option **Verify TLS certificates in manifested models** of the Prompt Builder, followed by manifesting the model again (for a retrieval model registered with the custom step *RAG - Register Setup*: with the step's checkbox **Verify TLS certificates in the registered retrieval model**, followed by running the step again), or with the environment variable *LLMCONTAINERSSLVERIFY=false* on the destination (*RAGEMBED_SSLVERIFY=false* for a retrieval model). The model then no longer checks which container answers; the same section says when that is acceptable. Where MAS runs Python in the runner container, set the variable on the runner as well — see [the variable and the Python runner](./Deployment-of-Decisions.md#the-variable-and-the-python-runner).

### HTTP status 201, an empty error column and empty outputs in a publishing validation: the execution timed out {#empty-outputs-published-decision}

The publishing validation of a decision on a MAS destination ends without an error, a row has HTTP status 201 and an empty error column — and every output of the decision is empty in that row, also the outputs of nodes that have nothing to do with each other. The log of the validation job has no ERROR and no WARNING line. The same decision returns its outputs in the in-app test of SAS Intelligent Decisioning, and the same validation, run again without any change, can return other rows empty than before.

SAS Micro Analytic Service ended the execution of such a row because it took longer than 30 seconds. That was measured on 2026-10-11 on SAS Viya 2026.09, with a decision of three model nodes that were all manifested in the Prompt Builder: the retrieval model of a RAG setup, a prompt template with the included LLM call that is given the retrieved context, and a decision template. Three places show it:

- **The table of the publishing validation**: HTTP status 201, an empty error column, every output empty. Nothing in it names a timeout.
- **A direct REST call** of the step of the published module answers HTTP status 201 as well, with `"executionState": "timedOut"` and `"outputs": []`.
- **The log of the pod *sas-microanalytic-score***: *Failure encountered during execution in revision … of module context '…'. Return code: 0x8b3ff145.* and *Micro Analytic Service failed to execute method*. With the log level debug, the scheduling thread writes `0x803fc006=tkWait()` exactly 30.0 seconds after its line *Scheduled work on worker thread*, and the two failure lines follow.

What was measured about the 30 seconds:

- **The publishing validation sends the rows of its input table at the same time.** Three rows were scheduled within 12 milliseconds.
- **One call at a time stayed within them.** In single calls the retrieval node took 0.2 seconds, the LLM node 1.3 to 4.2 seconds and the decision model node 1.7 to 4.1 seconds; a whole call took 6.5 to 9 seconds. Six single calls one after another all returned HTTP status 201, `retrieval_status` *ok*, three hits, an envelope of 1969 characters and the answer of the LLM from the retrieved context.
- **Three rows at once did not.** The durations the service logs for each node (*NODE DURATION*) were 0.2 to 2.2 seconds for the retrieval node, 5 to 6 seconds for the LLM node of the first row and more than 29 seconds for the two others, and 24 to 25 seconds for the decision model node while the LLM container was busy. The LLM and the decision model were both self-hosted and ran on the same CPU node; with the retrieved context the prompt of the LLM was about 900 characters, where it was about 50 without.
- **The same validation gave two results.** The same module with the same table, run twice without any change: the first run returned all three rows empty, the second returned one row complete — its three nodes had taken 29.65 seconds in total — and two empty.
- **Three direct calls at once** all answered `timedOut`, after 30.8, 31.7 and 36.5 seconds.

In the measured decision a row was lost by the time its models took when they were called at the same time: the same rows came back complete in single calls, with nothing in the decision or in a model changed. That is one decision on one system, with two self-hosted models on one CPU node; another decision can be slow for another reason. What was ruled out for the measured one: **the lengths of the variables of a model are not the cause**: with the two context outputs of the retrieval model declared with 100 characters, single calls returned the envelope complete, with its 1969 characters ([the lengths of the variables](./RAG-Ingestion-and-Retrieval.md#the-lengths-of-the-variables)). A publishing validation that returned every row after a change to the decision or to a model had finished within the 30 seconds that time; run it more than once before reading a change as the remedy.

What a site can do follows from the durations above; none of it was measured as a remedy:

1.   **Fewer calls at once.** Validate with a table of one row, or call the module row by row. A caller of the published decision that sends its requests one after another is the case that stayed within the time.
2.   **A faster model, or models that do not share a node.** The time was spent in the two self-hosted models on one CPU node: the decision model took 24 to 25 seconds while the LLM container was busy, where it took 1.7 to 4.1 seconds alone. Whether a GPU node, a node for each of the two or a hosted model for one of them brings a validation of several rows under the 30 seconds was not measured.
3.   **A shorter prompt.** With the retrieved context the prompt of the LLM was about 900 characters instead of about 50. A smaller `k` of the retrieval model, or smaller chunks, shorten it.

No setting for the time limit is named here because none was found: the configuration definition *sas.microanalyticservice.system* of that system holds no timeout property (it holds *core.numthreads* 4 and, under *asynchronousexecution*, *corepoolsize* 5 and *maxpoolsize* 10), and no way to change the 30 seconds was tried.

## SAS Job Execution

This part of the troubleshooting guide describes what can be found in the log of a job that runs a program of the accelerator - the RAG ingestion job, a job generated from a RAG flow or the log parser.

### Two ERROR lines about Cloud Analytic Services before the first line of the program {#cas-connect-errors-before-the-program}

On the test system (SAS Viya 2026.09, 2026-10-11) every job log of the *SAS Studio compute context* began with two ERROR lines, before the first source line of the program:

```text
ERROR: Failed to connect to host '<SAS Viya host>', port 5570.
ERROR: Unable to connect to Cloud Analytic Services ... on port 5570 ...
```

They stood in the logs of the RAG ingestion job, of the jobs generated from a RAG flow and of the log parser, and equally in a job that held none of the accelerator's code. The jobs ended as `completed` when nothing else failed, `SYSCC` was 0 after the log parser had run, and the CAS session the ingestion job starts itself connected on the same port. Why that system writes the two lines was not established: this is an observation of one system, and another system may not show it.

What follows from it where a log shows these lines:

1.   A check that counts the ERROR lines of a job log never finds 0 there. Start at the first source line of the program, or read the state of the job.
2.   The lines of the accelerator name their program: `ERROR: RAG ingestion failed`, `ERROR: RAG - <step> failed`, `ERROR: The log could not be parsed`. An ERROR line before source line 1 is none of them.

## SAS Container Runtime

This part of the troubleshooting guide describes issues that can occur when publishing decisions or models that call the LLMs to a SAS Container Runtime (SCR) publishing destination (independent of AWS, Azure, GCP or Private Docker).

### SSL Error: Missing CA trust list

This error is mostly likely caused by the fact that you are using a certificate for the LLM containers that isn't valid according other SCR containers - this might be because you are using a self-signed certificate or organization signed. This fix has to be done by the person that deploys the container - for more information check out the [SAS documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/p0r7reghey2nmsn1lubyxbibzwjh.htm):

```yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <CONTAINER>
  namespace: <NS>
spec:
  selector:
    matchLabels:
      app: <APP_NAME>
  template:
    metadata:
      labels:
        app: <APP_NAME>
    spec:
      containers:
      - name: <APP_NAME>
        image: <CONTAINER_REGISTRY>/<CONTAINER>:latest
        resources:
          requests:
            memory: "4000Mi"
            cpu: "1000m"
        ports:
        - containerPort: 8080
          protocol: TCP
        env:
        - name: SAS_SCR_SSL_CERTIFICATE
          value: /ssl/certificate-file.crt
 ... Shortend for brevity
```

Please note that you have to mount the certificate file in a volume to the container!