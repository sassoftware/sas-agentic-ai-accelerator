---
sidebar_position: 9
title: Serving Open-Weight Models
---

Open-weight models can get their weights in one of two ways. This page describes both, and explains when the second one is worth the extra setup.

## Two ways to get weights into a container

**Baked in (the default).** The container build downloads the weights from Hugging Face and stores them inside the image. Simple, self-contained, and fine for small models.

**Staged on a shared volume.** The weights are downloaded once into a shared Kubernetes volume, and every model container reads them from there at run time. The image contains only the score code and its Python dependencies.

Staging is worth it as soon as any of the following apply:

- **Container size.** A 7B model in `float16` is roughly 14 GB. Baked in, that 14 GB is in the image, in your registry, and on every node that pulls it. Staged, the image stays in the tens of megabytes.
- **The same weights serve several containers.** Model variants, replicas of one model, and a redeployment after a score-code change all read the same staged copy. The data does not have to exist in more than one place — with a `ReadWriteMany` volume, many pods mount the same directory at once.
- **Publish time.** Downloading many gigabytes during the build is the slowest part of publishing, and it happens again on every republish. Staged weights are downloaded once.
- **Gated models.** Models with a license you must accept — see the [LLM Definitions page](LLM-Definitions.md) — need a Hugging Face token. Staging is the supported way to provide one, because the token is used by a normal Kubernetes Job you control rather than by the container build.

:::note Superseding the pre-2025.10 setup

Earlier releases documented mounting a Hugging Face token into the Build Kit pod so the container build could authenticate. That mount no longer reaches the build, so definitions carrying a `hf login --token $(cat /etc/secret-volume/huggingfacetoken)` step in their `requirements.json` should move to the staged-weights approach below. `mdb generate` no longer emits that step.

:::

## Create the shared weight volume

Apply this once per namespace. Copy `SCR-LLM-Deployment-YAML/llm-weights-pvc-template.yaml`, replace the two placeholders, and apply it:

```bash
kubectl apply -f llm-weights-pvc.yaml -n llm
```

`ReadWriteMany` is required, because several model pods mount the claim at the same time. Storage classes that only support `ReadWriteOnce` — Azure `managed-csi-premium`, for example — will not work; use Azure Files, NFS or an equivalent.

Size it for the total of every model you intend to stage, plus headroom.

## Stage the weights

Run a one-off Job that downloads a model into the volume. Only this Job needs a Hugging Face token, and only for gated repositories.

For a gated model, create the token secret first:

```bash
kubectl create secret generic huggingface-token \
  --from-literal=huggingfacetoken='*yourToken*' -n llm
```

Then apply `SCR-LLM-Deployment-YAML/stage-weights-job-template.yaml` with the model repository and target directory filled in. The target directory **must** be named after the `model_id` of the definition, since that is where the scorer looks:

```
/mnt/weights/<model_id>
```

Check that it landed:

```bash
kubectl -n llm exec deploy/<any-model> -- ls -la /pybox/model/mount
```

Weights for a public model need no token — delete the `env` block referencing the secret before applying.

## Point a definition at the staged weights

Set `weights_source` in the model's `definition.yaml`:

```yaml
runtime:
  template: hf_transformers
  requirements_profile: hf-transformers
  weights_source: mounted
```

Then regenerate, register and publish:

```bash
mdb generate <model_id>
mdb register <model_id> --update
mdb publish <model_id> --wait
```

`mdb generate` drops the weight download from `requirements.json` and points the score code at `/pybox/model/mount/<model_id>`. The publish therefore builds a small image containing only the score code and its dependencies.

Omitting `weights_source` — or setting it to `baked` — keeps the previous behaviour, so existing definitions are unaffected until you change them.

## Deploy the model

`mdb deploy` selects the persistent-volume deployment variant automatically for self-hosted models:

```bash
mdb deploy <model_id> --registry <your-registry> --host <your-ingress-host>
kubectl apply -f deploy-<model_id>.yaml -n llm
```

The rendered Deployment mounts the `llm-weights` claim read-only at `/pybox/model/mount`. Because the mount path is the same for every model, one staged copy serves all of them.

:::tip Verify before publishing

`kubectl apply --dry-run=server` validates a rendered manifest against the API server, including whether the volume a container mounts actually exists. The client-side `--dry-run` only checks the schema and will not catch a missing volume.

:::

## Gated models

Gated repositories cannot be downloaded during a container build, so they must use `weights_source: mounted`. `mdb generate` reports an error rather than producing a definition whose build would fail at the download step:

```
<model_id>: '<repo>' is a gated Hugging Face repository, which cannot be
downloaded during the container build. Set 'runtime.weights_source: mounted'
in definition.yaml and stage the weights on the shared llm-weights volume
instead.
```

Accept the model's license on Hugging Face first, otherwise the staging Job fails with a 403 even with a valid token.
