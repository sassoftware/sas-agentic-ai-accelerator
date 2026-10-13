# Contributing
Maintainers are accepting patches and contributions to this project.
Please note the following contribution guidelines.

## Contributor License Agreement
Contributions to this project must be accompanied by a signed [Contributor Agreement](ContributorAgreement.txt).
You (or your employer) retain the copyright to your contribution.
This simply permits the project maintainers to use and redistribute your contributions as part of the project.

## Update Documentation
Project documentation is stored in the `website` directory.
This project welcomes pull requests suggesting updates to this documentation.
See the documentation [README](./website/README.md) for details.

## Code Reviews
All submissions, including submissions by project members, require review.
GitHub pull requests are used for this purpose.
Consult [GitHub Help](https://help.github.com/articles/about-pull-requests/) for more information about using pull requests.

## The Transfer Package and Your Change
`SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json` is the content folder of the accelerator as one SAS Viya transfer package.
Almost all of it is a second copy of files of this repository: the custom steps, the programs, `rag_core`, the built Prompt Builder page, the fact sheets, and the release table that is made from `CHANGELOG.md`.
The package is exported from a SAS Viya system by a maintainer, so between two exports it holds the content of an earlier commit.

That means a change to one of those files makes the package differ from your checkout, and **you are not expected to do anything about it**:

- The workflow `Verify generated model definitions` runs `mdb package-check` on your pull request.
  A difference in content - a file, a custom step, a job definition, the release table or `release.json` - is printed as a line that starts with `drift:` and does **not** fail the check.
  You do not need access to a SAS Viya system, and you do not edit the package file.
- The check **does fail** for what a package must never be, whatever the checkout holds: an object that `SAS-Viya-Integrations/package-manifest.yaml` does not list, an entry of the manifest that is not in the package, an authorization rule or a share, a host name of somebody's system in a report or in what an object says about itself (its summary, its description, its links - a program, a form or a file that names a host is content, compared as content), a connector or a substitution an export does not give such an object, a user id in `createdBy`, `modifiedBy` or `userName`, a per-user report state, a report default that is not neutral, a `release.json` whose commit is no commit id in full (an abbreviation is none), a path that never ships (`Showcases`, `Example`, a folder whose name holds `generated`, a name starting with `ZZ_VERIFY`).
- If your change **adds, moves or removes an object of the content folder**, list it in `package-manifest.yaml`.
  The check then fails with `... is in the manifest but not in the package` until a maintainer has exported the package again; say so in the pull request.
- If your change **replaces the package file itself**, the workflow runs the strict form below on it: a package is only replaced by an export of the checkout.
- You can run the check yourself: `pip install -e "./Model-Definition-Builder/cli[dev]"`, then `mdb package-check`.

For a release the gate is strict: a maintainer brings the content folder to the checkout (`mdb content-deploy`), exports it (`mdb package-export`) and runs `mdb package-check --release`, for which every difference is an error.
There the commit `release.json` names has to be the commit of the tag or one it descends from: a commit of a branch that is squash-merged afterwards is not, and the fresh clone of the tag's workflow does not even hold it.
So the release work is merged first, and the content folder is deployed and exported from the merged commit; the commit that adds the package file sits on top of it.
The workflow runs the strict form when a release is tagged.
The procedure is described under *Releasing the package* in the [Model Definition Builder guide](https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Model-Definition-Builder).
