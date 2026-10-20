# v0.8 trusted CI gate and repository rules

`SKILL.md`, local hooks and project-local verifier code are not security boundaries. A protected repository must invoke `.github/workflows/mcu-policy.yml` (preferably copied to or called from a separately protected organization repository). Configure the immutable v0.8 wheel location and independently trusted digest as protected repository/organization variables `MCU_VERIFIER_WHEEL_URL` and `MCU_VERIFIER_WHEEL_SHA256`; they are deliberately not caller-controlled workflow inputs.

## Required repository rules

Configure the default branch so that:

1. direct pushes and force pushes are denied;
2. pull requests are mandatory;
3. `verify-mcu-policy` is a required check;
4. the Agent token can push feature branches but cannot approve or merge;
5. the Agent token cannot modify rulesets or required workflows;
6. `.github/workflows/**`, `.mcu-agent/project-contract.json`, `.mcu-agent/policy.lock`, `.mcu-agent/config.yaml`, and CODEOWNERS changes require a human owner;
7. `.mcu-agent/artifact-manifest.yaml` and canonical user-input/evidence roots require CODEOWNER approval, because local `user:artifact-register` metadata is not authenticated identity;
8. administrators cannot silently bypass the required check, or bypass is limited and audited.

Do not install the verifier from the pull request checkout. Publish the clean wheel first, record its URL and SHA-256 in protected organization/repository variables, and deny the Agent permission to modify those variables. The reusable workflow fails closed when either variable is absent or malformed.

## Verification contract

`mcu-agent verify-repository --strict --base <sha> --head <sha>` independently checks the tracked project contract and policy lock, every CURRENT Artifact hash, promotion audits loaded from and created by the governed commit, exact changed-file coverage, full before/after Artifact-manifest digests and constrained manifest deltas, claim/policy/verification trailers, base commit, task patch digest, and target/HIL structure. Host/build source changes are rerun by the trusted wheel against the protected verification profile; target audits also require a trusted build rerun and either revalidated manual evidence or a trusted HIL rerun. Source, hardware or generated evidence changes without a matching promotion audit fail.

Promotion audits and `user:artifact-register` fields are currently unsigned local provenance; neither is authenticated identity or, by itself, proof that a human selected an input or that an Agent ran a build. Trusted CI tightly validates registration/manifest deltas, while mandatory CODEOWNER rules authenticate acceptance of canonical inputs. CI reruns host/build verification in a disposable clean worktree and revalidates manual target evidence hashes. Environments requiring stronger identity/non-repudiation must add a protected external promotion signer or remote attestation service; v0.8 does not misrepresent same-user local JSON as a cryptographic boundary.

The first contract/layout commit is allowed only when it contains setup metadata and canonical `.gitkeep` files. Existing contract changes require a future explicit migration audit and are rejected by the initial v0.8 verifier. Strict mode also requires a non-empty commit range, a clean tracked checkout, and `product-root` equal to the Git top-level; v0.8 intentionally rejects ambiguous monorepo subroots rather than under-matching Git paths.

## Scope and limitations

The CI gate prevents merge/release of nonconforming commits. It does not prevent a same-user local Agent process from editing files. `claim-task --workspace`, canonical tree snapshots and `promote-task` reduce that risk; OS/container isolation and an external signer remain later hardening layers.

This repository does not configure remotes or GitHub rules automatically. Apply the rules only after reviewing the workflow and authorizing remote administration.
