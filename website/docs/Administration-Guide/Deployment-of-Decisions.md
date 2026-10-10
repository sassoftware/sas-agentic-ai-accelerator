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

The option reaches the models that make an HTTPS call themselves: a prompt template with the included LLM call, a decision template, and the retrieval model of a RAG setup, which reads two variables of its own — see [RAG Ingestion and Retrieval](./RAG-Ingestion-and-Retrieval.md#tls-verification-in-the-retrieval-model). It does not change a prompt manifested for the *Call LLM* node, where the node makes the call, nor anything the Prompt Builder page does in the browser, nor the ingestion job of a RAG setup.

### Finding the models that do not verify {#finding-the-models-that-do-not-verify}

A model manifested while the option was not ticked carries the tag **TLS-Verification-Off** in SAS Model Manager, and the step that manifests it — *Finalize* for a prompt or a decision template, *Manifest setup* for a RAG setup — shows a warning before the button is clicked. The tag stands with the model's other tags, such as *LLM-Call-Included*. To list every such model, filter the models on that tag in SAS Model Manager, or ask the model repository:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://<sas-viya-host>/modelRepository/models?filter=contains(tags,'TLS-Verification-Off')&limit=100" \
  | jq -r '.items[] | [.name, .projectName, .modifiedBy] | @tsv'
```

The tag says what the model was manifested with, not what a destination does: *LLMCONTAINERSSLVERIFY=true* on a destination makes a tagged model verify there, and *LLMCONTAINERSSLVERIFY=false* stops an untagged one from verifying. Manifesting the model again with the option ticked removes the tag.

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

This transformer adds the variable to the first container of the pod. For a model that makes the call itself, read [the variable and the Python runner](#the-variable-and-the-python-runner) below.

## Publishing to SAS Micro Analytic Service: the certificate bundle {#mas-certificate-bundle}

A decision that works in the in-app test of SAS Intelligent Decisioning can fail with a certificate error once it is published to SAS Micro Analytic Service (MAS). The in-app test runs in CAS, where the CA bundle is mounted. MAS can run Python in a container where it is not.

What follows was measured on one deployment (SAS Viya on Kubernetes, namespace `viya4`). Container and volume names can differ on yours, so the steps start with a command that shows what you have.

### What the deployment looked like

- The deployment `sas-microanalytic-score` has two containers: `sas-microanalytic-score` and a Python runner, there named `viya4-mas-python-runner` — the name carries the deployment's name prefix.
- The pod has a volume named `security`, an `emptyDir` that the init container `sas-certframe` fills with the trust bundle.
- The main container mounts that volume at `/security` and has `SSLCALISTLOC=/security/trustedcerts.pem`.
- The Python runner mounts only `/tmp`, `/pylog`, `/opt/sas/viya/home/sas-pyconfig` and `/models/resources/viya`, and has no certificate environment variable. Inside it `/security/trustedcerts.pem` does not exist.

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
  kubectl -n <namespace> get deployment sas-microanalytic-score \
    -o jsonpath='{.spec.template.spec.containers[*].name}'
  kubectl -n <namespace> exec deploy/sas-microanalytic-score \
    -c <prefix>-mas-python-runner -- ls -l /security/trustedcerts.pem
  ```

  `No such file or directory` is this case. If the file is listed, the cause is another one: the certificate of the endpoint is not signed by a CA in that bundle — point *LLMCONTAINERCABUNDLE* at a bundle that covers it.

### Two ways out

1. **Mount the bundle into the Python runner.** Verification stays on, and no model has to be manifested again. This is the fix for production; the steps are in the next section.
2. **Switch verification off** for the models concerned: clear **Verify TLS certificates in manifested models** in the Options pane of the Prompt Builder and manifest the model again, or set *LLMCONTAINERSSLVERIFY=false* on the destination (see [the variable and the Python runner](#the-variable-and-the-python-runner)). Either way the model no longer checks that the answer comes from the real model container: whatever answers on that address inside the network is believed, and the prompt or the state is sent to it. That is acceptable on a test system whose cluster network is closed to others. It is not a setting for production — there, mount the bundle.

### Mounting the bundle into the Python runner

The volume already exists in the pod; the runner only lacks the mount. Replace `<prefix>-mas-python-runner` with the container name that the first command above printed.

**For a test.** One command. It starts a rollout of MAS, so scoring is interrupted while the pods are replaced, and it is lost at the next update of the deployment:

```bash
kubectl -n <namespace> patch deployment sas-microanalytic-score --type=strategic \
  -p '{"spec":{"template":{"spec":{"containers":[{"name":"<prefix>-mas-python-runner","volumeMounts":[{"name":"security","mountPath":"/security","readOnly":true}]}]}}}}'
```

**To keep it.** Add a patch file to the kustomize site configuration of the deployment, for example `site-config/sas-microanalytic-score/mas-python-runner-trust.yaml`:

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
              mountPath: /security
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

**Check the result** after either form, once the rollout has ended:

```bash
kubectl -n <namespace> rollout status deployment sas-microanalytic-score
kubectl -n <namespace> exec deploy/sas-microanalytic-score \
  -c <prefix>-mas-python-runner -- ls -l /security/trustedcerts.pem
```

The file has to be listed. Then run the publishing validation of the decision again: the rows have to carry answers instead of the error. If the file is listed and an error stays, read its text again — a different text is a different cause.

### The variable and the Python runner {#the-variable-and-the-python-runner}

A Python model reads the environment of the container its code runs in. The PatchTransformer in [MAS vs SCR](#mas-vs-scr) adds a variable to the first container of the pod, which on the deployment described here is the main one. Where MAS runs Python in a separate runner, add *LLMCONTAINERSSLVERIFY* — and every other *LLMCONTAINER...* variable the Python code of a model is to read — to the runner as well, by its name:

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
            - name: LLMCONTAINERSSLVERIFY
              value: "false"
```

It is listed under `patches:` like the mount above. Check it the same way, with `env` in place of `ls`:

```bash
kubectl -n <namespace> exec deploy/sas-microanalytic-score \
  -c <prefix>-mas-python-runner -- env | grep LLMCONTAINER
```

A variable set this way holds for every model published to that MAS, the ones manifested with verification on included.