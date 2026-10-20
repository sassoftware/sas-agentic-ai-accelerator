---
sidebar_position: 12
title: Deployment of Decisions
---

This page details considerations when deploying the decisions in MAS/SCR.

Every prompt template checks for an environment variable called *LLMCONTAINERPATH*. If this variable does not exist it will default to the value that was provided as an endpoint for the LLM Prompt Builder where that prompt template was created. Setting this environment variable will enable you to deploy the same decision across multiple environments and also changing the LLM endpoints in case you have fully isolated environments.

## Prompt templates with the included LLM call

If a prompt was manifested with the *Include the LLM call in the manifested model* option, the generated Python model performs the LLM call itself using the *requests* package — a *requirements.json* is stored with the model so publishing destinations that build a Python environment install it. A manifested **decision template** always calls its decision model this way. In addition to *LLMCONTAINERPATH*, these models check three more environment variables:

- *LLMCONTAINERCABUNDLE*: path to the CA bundle used to verify the TLS connection to the LLM container. It defaults to */security/trustedcerts.pem*. SAS Viya mounts that file into the pods of CAS and of the compute servers, so a model that runs there — the in-app test of SAS Intelligent Decisioning, a SAS Studio session — verifies an ingress secured by the deployment's own CA without any setting. The file is **not** in every container that runs Python: where SAS Micro Analytic Service runs Python in a separate runner container, it is missing there — see [Publishing to SAS Micro Analytic Service: the certificate bundle](#mas-certificate-bundle). When the file does not exist, the model verifies against the public certificate authorities that come with *requests*, and a certificate signed by the deployment's own CA is then refused.
- *LLMCONTAINERSSLVERIFY*: switches TLS verification on or off for this destination, whatever the model was manifested with. *false*, *no* or *0* switches it off; *true*, *yes* or *1* switches it on. Upper and lower case are the same. When the variable is not set, or holds any other word, the model does what was written into it when it was manifested — see [the default written at manifest time](#tls-default-at-manifest-time).
- *LLMCONTAINERTIMEOUT*: timeout for the LLM call in seconds (default 600).

If the LLM call fails (network issue, TLS failure or a non-200 response), the model does not raise an error — the failure message is returned in the *response* output, the parsed output variables keep their default values and *parse_status* returns 0. A decision template returns the message in its *answer* output, and its other outputs stay empty.

### The default written at manifest time {#tls-default-at-manifest-time}

The Options pane of the Prompt Builder has the checkbox **Verify TLS certificates in manifested models** (`sslVerify`, ticked by default — see [the options of the Prompt Builder](./Setup-Additional-UIs.md#configuration-properties-panel)). Its state is written into the score code of every model manifested from then on:

| The option when the model was manifested | *LLMCONTAINERSSLVERIFY* on the destination | The model |
| --- | --- | --- |
| ticked | not set, or another word | verifies — against the bundle, or against the public CAs when the bundle file is missing |
| ticked | *false*, *no*, *0* | does not verify |
| not ticked | not set, or another word | does not verify |
| not ticked | *true*, *yes*, *1* | verifies, as in the first row |

Three things follow from that:

- The option is a default for **new** manifests. A model that is already manifested keeps what it was manifested with until it is manifested again; changing the option changes no registered model and no published one.
- The environment variable wins in both directions, so a model manifested with verification off can be made to verify on one destination, and the reverse, without manifesting it again.
- With the option ticked, a model behaves as a model manifested before release 2.3.0 does.

The option reaches the models that make an HTTPS call themselves: a prompt template with the included LLM call, a decision template, and the retrieval model of a RAG setup, which reads two variables of its own — see [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md#tls-verification-in-the-retrieval-model). A retrieval model registered with the SAS Studio custom step *RAG - Register Setup* is not manifested in the Prompt Builder and does not get the option: the step has a checkbox of its own, **Verify TLS certificates in the registered retrieval model**, which writes the same default — see [the checkbox of the custom step](./RAG-Ingestion-and-Retrieval.md#tls-register-setup-step). The option does not change a prompt manifested for the *Call LLM* node, where the node makes the call, nor anything the Prompt Builder page does in the browser, nor the ingestion job of a RAG setup.

### Finding the models that do not verify {#finding-the-models-that-do-not-verify}

A model manifested while the option was not ticked carries the tag **TLS-Verification-Off** in SAS Model Manager: a decision template, the retrieval model of a RAG setup, and a prompt template manifested with the included LLM call. The step that manifests it shows a warning before the button is clicked — *Finalize* for a decision template, *Finalize* for a prompt only while **Include the LLM call in the manifested model** is ticked, and *Manifest setup* for a RAG setup. A retrieval model registered with the custom step *RAG - Register Setup* while its checkbox **Verify TLS certificates in the registered retrieval model** is not ticked carries the same tag, and the log of the step ends with WARNING lines that say so. A prompt manifested for the *Call LLM* node makes no call itself, so it gets neither the warning nor the tag. The tag stands with the model's other tags, such as *LLM-Call-Included*. To list every such model, filter the models on that tag in SAS Model Manager, or ask the model repository:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://<sas-viya-host>/modelRepository/models?filter=contains(tags,'TLS-Verification-Off')&limit=100" \
  | jq -r '.items[] | [.name, .projectName, .modifiedBy] | @tsv'
```

The tag says what the model was manifested with, not what a destination does: *LLMCONTAINERSSLVERIFY=true* on a destination makes a tagged model verify there, and *LLMCONTAINERSSLVERIFY=false* stops an untagged one from verifying. Manifesting the model again with the option ticked removes the tag, on all three kinds; running the custom step again with its checkbox ticked removes it from a model the step registered.

The tag follows the score code the model holds, with one case the page cannot decide. A manifest whose score code SAS Model Manager refused is reported as failed and leaves the tags as they were. When the upload of the score code gets no answer at all — the connection drops, or an ingress answers with its error page — SAS Model Manager may have stored the code all the same; the manifest then says beside its button that *it is not known whether* the score code was stored, leaves the tags as they were and asks for the manifest to be repeated. Until a manifest of that model completes, its tag may not match its score code, in either direction; the line in the score code named below is what counts. The tags are the first thing written once the score code is stored, before `requirements.json` and the attributes of the model, so a manifest of a prompt or decision template that stops at a later request — *The manifest was not completed* beside its button — leaves the tags of the score code that is stored. When the tags could not be updated, the manifest says so beside its button in both directions — the tag could not be written to a model that does not verify, or could not be taken away from one that does — so that it can be put right in SAS Model Manager.

To read the setting in the score code itself, open the model in SAS Model Manager and look for one line. The score code of a prompt template with the included LLM call and of a decision template holds `sslVerifyDefault = True` or `sslVerifyDefault = False`. The `retrieve_context.py` of a retrieval model holds `SSL_VERIFY = True` or `SSL_VERIFY = False` in its MANIFEST block. A prompt manifested for the *Call LLM* node holds neither line. Nothing else in the score code of a prompt or decision template carries the name `sslVerifyDefault`: the Prompt Builder refuses a variable, an output variable or a question of that name.

## The Call LLM node: one id on every site {#the-call-llm-node}

A step of a decision names its custom node type only by its id: a decision that calls the *Call LLM* node runs on another site only where that site has the node under the same id. From release 2.3.0 the transfer package of the accelerator brings the node under one id, `f9efcbb7-f57b-4a62-b56d-1a89595e2039`, as the dependency of the decision `Decisions/Call LLM Node` - a decision that holds only that node and is not meant to be published. `mdb builders-import` imports both: it creates the node type under that id, or overwrites the node type of that id in place, which replaces local edits of its code and its terms.

A site that created the node with `Create-Custom-SAS-Intelligent-Decisioning-Node.sas` has it under an id of its own. A node type cannot be given another id, and its name is unique, so the import cannot bring the node there: `mdb builders-import` leaves the decision and the node out and ends with exit code 1. Run this once, before the import:

```bash
mdb decision-node-migrate           # dry run: the node type and every decision version that uses it
mdb decision-node-migrate --apply
```

The command renames your node type to `Call LLM (before f9efcbb7)`, creates `Call LLM` under the id of the package with the same code and terms, and moves every decision that uses the old node type - every version, the locked ones included, keeping the version ids - by a transfer re-import of the decision over itself. Published MAS and SCR modules hold compiled code and need nothing. The old node type is kept; `--delete-old` deletes it, and only when no version of any decision calls it any more. Do not delete it by hand while a version calls it: SAS Intelligent Decisioning then clears the node of every such version, locked ones included, and creating the id again does not bring it back. [Moving the Call LLM node to its id](./Model-Definition-Builder.md#decision-node-migrate) has the details.

## Kubernetes vs Azure Container Apps/Instances

Please note that it is not supported to change the type of deployment between environments so if you have the LLM containers deployed in Kubernetes in one environment, they will have to be deployed in the same fashion in all other environments as well.

If you have selected a Kubernetes based deployment set the variable value to the corresponding of this: *https://base-url/llm*.

If you have selected an Azure Container Apps/Instaces based deployment set the variable value to the corresponding value of this: *randomString.region.azurecontainerapps.io* from the https://model.randomString.region.azurecontainerapps.io/model URL.

## MAS vs SCR

Please note that you do not have to set this environment variable if you are ok with utilizing the hardcoded value provided by LLM Prompt Builder.

If you are deploying the decisions to a SCR destination than just add the *LLMCONTAINERPATH* environment variable as part of the **env** definition in the *YAML* file. That means you can give every instance potentially a different endpoint!

If you are deploying the decisions to MAS you will have to add the *LLMCONTAINERPATH* environment variable to the *sas-microanalytic-score* pod via a PatchTransformer. That means you can only have one instance of this per MAS deployment and if it is set once it will apply to all.

Here is an example of a PatchTransformer:
```yaml
apiVersion: builtin
kind: PatchTransformer
metadata:
  name: mas-llm-transformer
patch: |-  
  # Add LLM environment variable
  - op: add
    path: /spec/template/spec/containers/0/env/-
    value:
      name: LLMCONTAINERPATH
      value: <YOURVALUE>
target:
  group: apps
  kind: Deployment
  name: sas-microanalytic-score
  version: v1
```

This transformer adds the variable to the first container of the pod, the main container *sas-microanalytic-score*. Every prompt template reads *LLMCONTAINERPATH* in its Python score code, with or without the included LLM call: a prompt manifested for the *Call LLM* node builds the URL it hands to the node in that code too. Where MAS runs Python in a separate runner container, the score code does not run in the first container — set the variable on the runner as well, as [the variable and the Python runner](#the-variable-and-the-python-runner) below shows.

## Publishing to SAS Micro Analytic Service: the certificate bundle {#mas-certificate-bundle}

A decision that works in the in-app test of SAS Intelligent Decisioning can fail with a certificate error once it is published to SAS Micro Analytic Service (MAS). The in-app test runs in CAS, where the CA bundle is mounted. MAS can run Python in a container where it is not.

What follows was measured on one deployment (SAS Viya on Kubernetes, namespace `viya4`). Container and volume names can differ on yours, so the steps start with a command that shows what you have.

### What the deployment looked like

- The deployment `sas-microanalytic-score` has two containers: `sas-microanalytic-score` and a Python runner, there named `viya4-mas-python-runner` — the name carries the deployment's name prefix.
- The pod has a volume named `security`, an `emptyDir` that the init container `sas-certframe` fills. It holds the trust bundle `trustedcerts.pem`, and more than that: the private key directory of the pod, `private`, is in the same volume.
- The main container mounts the whole volume at `/security`, and parts of it a second time elsewhere by `subPath` (`cacerts`, `private`). It has `SSLCALISTLOC=/security/trustedcerts.pem`.
- The Python runner mounts four volumes, and `security` is not one of them: `pytmp` at `/tmp`, `pylog` at `/pylog`, `python-volume` at `/opt/sas/viya/home/sas-pyconfig` and `astores-volume` at `/models/resources/viya`. It has no certificate environment variable, and inside it `/security/trustedcerts.pem` does not exist.

A manifested model that calls the model container itself runs in the Python runner. It looks for the bundle, does not find the file, falls back to the public certificate authorities, and refuses the certificate of an ingress that is signed by the deployment's own CA.

### The symptom

The publishing validation of the decision ends with HTTP 201, and every row carries the error in the model's text output — *answer* for a decision template, *response* for a prompt template — while the other outputs are empty:

```text
Decision model call failed: HTTPSConnectionPool(host=..., port=443): Max retries exceeded with url: /llm/von_1_2/von_1_2 (Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain')))
```

For a prompt template the line starts with `LLM call failed:`. The URL names the model container that was called, here `von_1_2`.

### How to tell that it is this case

- The same decision returns answers in the **in-app test** of SAS Intelligent Decisioning, which runs in CAS, and fails in the **publishing validation** on the MAS destination.
- A decision that reaches the same model container through the **Call LLM** node works on the same MAS destination. The node is DS2 code and runs in the main container, which has the bundle.
- The Python runner has no bundle. Find the container names, then look for the file:

  ```bash
  kubectl -n <namespace> get deployment sas-microanalytic-score -o jsonpath='{.spec.template.spec.containers[*].name}'
  kubectl -n <namespace> exec deploy/sas-microanalytic-score -c <prefix>-mas-python-runner -- ls -l /security/trustedcerts.pem
  ```

  `No such file or directory` is this case. If the file is listed, the cause is another one: the certificate of the endpoint is not signed by a CA in that bundle — point *LLMCONTAINERCABUNDLE* at a bundle that covers it.

### Two ways out

1. **Mount the bundle file into the Python runner.** Verification stays on, and no model has to be manifested again. This is the fix for production; the steps are in the next section.
2. **Switch verification off** for the models concerned: clear **Verify TLS certificates in manifested models** in the Options pane of the Prompt Builder and manifest the model again (for a retrieval model registered with the custom step *RAG - Register Setup*: clear the step's checkbox **Verify TLS certificates in the registered retrieval model** and run the step again), or set *LLMCONTAINERSSLVERIFY=false* on the destination (see [the variable and the Python runner](#the-variable-and-the-python-runner)). Either way the model no longer checks that the answer comes from the real model container: whatever answers on that address inside the network is believed, and the prompt or the state is sent to it. That is acceptable on a test system whose cluster network is closed to others. It is not a setting for production — there, mount the bundle.

   On the deployment described here the option made exactly that difference: a decision whose decision template was manifested with verification off returned answers in the publishing validation on the MAS destination, and the same decision with the template manifested with verification on failed there with the error above.

### Mounting the bundle into the Python runner

The volume already exists in the pod; the runner only lacks the mount. Mount the bundle **file**, not the volume. The `security` volume also holds the private key directory of the pod — `/security/private` in the main container — and the runner is the container that executes the code of the models. A mount with `subPath` gives the runner the one file it needs and nothing else of the volume.

In both forms, replace `<prefix>-mas-python-runner` with the container name that the first command above printed.

**For a test.** Save the patch as `mas-python-runner-trust.patch.yaml`:

```yaml
spec:
  template:
    spec:
      containers:
        - name: <prefix>-mas-python-runner
          volumeMounts:
            - name: security
              mountPath: /security/trustedcerts.pem
              subPath: trustedcerts.pem
              readOnly: true
```

Then apply it. The patch is a file because a patch written on the command line is quoted differently in PowerShell and in bash:

```bash
kubectl -n <namespace> patch deployment sas-microanalytic-score --type=strategic --patch-file mas-python-runner-trust.patch.yaml
```

The patch changes the pod template, so it restarts the MAS pods — plan it like any other restart of MAS. A one-off patch is also overwritten by the next update of the deployment, which is why the second form exists.

To see what the patch does before anything changes, add `--dry-run=client -o yaml` to the command. On the deployment this was written against, that dry run added the mount to the runner and left the main container as it was.

**To keep it.** Add the same patch, with the header kustomize needs, to the site configuration of the deployment, for example as `site-config/sas-microanalytic-score/mas-python-runner-trust.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sas-microanalytic-score
spec:
  template:
    spec:
      containers:
        - name: <prefix>-mas-python-runner
          volumeMounts:
            - name: security
              mountPath: /security/trustedcerts.pem
              subPath: trustedcerts.pem
              readOnly: true
```

List it under `patches:` in `kustomization.yaml`:

```yaml
patches:
  - path: site-config/sas-microanalytic-score/mas-python-runner-trust.yaml
    target:
      kind: Deployment
      name: sas-microanalytic-score
```

Then build and apply the deployment as your site normally does.

**Check the result** after either form, once the rollout has ended. On the deployment described here the form for a test was applied on 2026-10-11 by the owner of the cluster, with the patch and the command as they stand above:

- The deployment rolled to a new pod, and that pod became ready with both containers (2/2).
- In the runner, `ls -l /security` then listed exactly one entry, `trustedcerts.pem` (367,876 bytes). The directory `private` of the volume was not visible there.
- A decision whose decision template is manifested with verification **on** — its score code holds `sslVerifyDefault = True` and passes `verify=sslVerify` to the call — was published again to the MAS destination `maslocal` with *Replace item with the same name*. Its publishing validation ended with *Completed successfully* on four rows: four answers with HTTP status 201 and an empty error column. Before the patch the same decision had answered `CERTIFICATE_VERIFY_FAILED` there.
- A second decision, with three model nodes that were all manifested in the Prompt Builder — the retrieval model of a RAG setup, a prompt template with the included LLM call and a decision template — was published to `maslocal` the same day, with the bundle mounted and verification on. All three nodes ran; its publishing validation ended on three rows with HTTP status 201 and an empty error column, and the prompt template and the decision template answered. The retrieval model answered `retrieval failed: RuntimeError: no vector-store credentials resolved: …` in `retrieval_status`, with no chunk: the runner had no `PGVECTOR_RAG_USER` and `PGVECTOR_RAG_PW`. [The retrieval model in a decision](./RAG-Ingestion-and-Retrieval.md#the-retrieval-model-in-a-decision) names the variables it needs.
- The same decision with the user and the password of the vector store passed per call, in the `options` input of the retrieval model: called one row at a time, six calls one after another each returned HTTP status 201, `retrieval_status` *ok*, three hits, an envelope of 1969 characters and the answer of the LLM from the retrieved context — with verification on in the score code of all three models. Its publishing validation on `maslocal` gave different results from run to run: all three rows complete in one, and in others rows with HTTP status 201, an empty error column and **empty outputs** of every node. That was not the certificate, and not the lengths of the model's variables: SAS Micro Analytic Service had ended those executions after 30 seconds, because the validation sends its rows at the same time and the two self-hosted models behind the nodes then took longer than that ([the execution timed out](./Troubleshooting-Guide.md#empty-outputs-published-decision)).
- The same decision with `PGVECTOR_RAG_USER` and `PGVECTOR_RAG_PW` as environment variables: set on the containers of the deployment from a Kubernetes secret (`kubectl set env deployment/sas-microanalytic-score --containers=* --from=secret/<name>`; the pod restarts), a call with `{}` as the `options` of the retrieval model returned `retrieval_status` *ok* and three hits. Which of the containers needs the two variables was not separated.

That was the one-off patch, not the kustomize form: on that deployment the mount is lost at the next apply of the deployment unless the kustomize form is added. Not measured: the kustomize form itself on a cluster; the call of a retrieval model to the SAS Viya credentials service from SAS Micro Analytic Service, which it does not make when the credentials come with the call or from the environment; the credentials of the vector store as environment variables in the test of SAS Intelligent Decisioning and in a SAS Container Runtime container; and whether a variable set on the main container alone reaches the Python in the runner, which [the variable and the Python runner](#the-variable-and-the-python-runner) is about. Container and volume names can differ on your deployment, so the check stays part of the procedure.

```bash
kubectl -n <namespace> rollout status deployment sas-microanalytic-score
kubectl -n <namespace> exec deploy/sas-microanalytic-score -c <prefix>-mas-python-runner -- ls -l /security/trustedcerts.pem
kubectl -n <namespace> exec deploy/sas-microanalytic-score -c <prefix>-mas-python-runner -- ls /security
```

The first `ls` has to list the file. The second should show that file and nothing else — no `private` directory. Then run the publishing validation of the decision again: the rows have to carry answers instead of the error. If the file is listed and an error stays, read its text again — a different text is a different cause.

### The variable and the Python runner {#the-variable-and-the-python-runner}

A Python model reads the environment of the container its code runs in. That holds for every *LLMCONTAINER...* variable: *LLMCONTAINERPATH*, which every prompt template and every decision template reads, and *LLMCONTAINERCABUNDLE*, *LLMCONTAINERSSLVERIFY* and *LLMCONTAINERTIMEOUT*, which the models that make the call themselves read.

The PatchTransformer in [MAS vs SCR](#mas-vs-scr) adds a variable to the first container of the pod, which on the deployment described here is the main one and not the runner. The containers of a pod do not share their environment, and whether MAS hands the environment of its main container on to the Python it starts in the runner has not been measured. So where MAS runs Python in a separate runner, add the variable to the runner as well, by its name, and look at what the runner really has with the command below:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sas-microanalytic-score
spec:
  template:
    spec:
      containers:
        - name: <prefix>-mas-python-runner
          env:
            - name: LLMCONTAINERPATH
              value: <YOURVALUE>
```

It is listed under `patches:` like the mount above. Another variable is another entry of the same `env` list — *LLMCONTAINERSSLVERIFY* with the value `"false"`, in quotes, for the second of the two ways out; the *RAGEMBED_...* and *RAGSTORE_...* variables of a retrieval model go there in the same way, and so do the user and the password of its vector store, *PGVECTOR_RAG_USER* and *PGVECTOR_RAG_PW* for pgvector — a retrieval model without them answers *no vector-store credentials resolved* and returns no chunk ([the retrieval model in a decision](./RAG-Ingestion-and-Retrieval.md#the-retrieval-model-in-a-decision) has the full list). A password in the `env` of a deployment is readable by everyone who can read the deployment; a Kubernetes secret referenced with `valueFrom` keeps it out of the manifest. Measured on 2026-10-11 for the two variables of the vector store: taken from a Kubernetes secret and set on every container of the deployment with `kubectl set env deployment/sas-microanalytic-score --containers=* --from=secret/<name>`, after which the pod restarted, they reached the retrieval model — a call with `{}` as its `options` returned `retrieval_status` *ok* and three hits. That was every container at once, so it does not say which of them needs the variables. Check it like the mount, with `env` in place of `ls`:

```bash
kubectl -n <namespace> exec deploy/sas-microanalytic-score -c <prefix>-mas-python-runner -- env
```

The output has to hold the variable with your value (pipe it through `grep LLMCONTAINER`, or `Select-String LLMCONTAINER` in PowerShell, to shorten it). A variable set this way holds for every model published to that MAS — *LLMCONTAINERSSLVERIFY=false*, for one, also for the models manifested with verification on.