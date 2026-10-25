# catchrr-catalog-job

The small job that builds the Catchrr fingerprint catalog on a GitHub Mac runner, because Apple's `shazam` tool and ShazamKit exist only on a Mac.
It asks the Catchrr backend which adverts are live, builds one catalog, listens to clips of each advert through simulated room noise, uploads the
catalog and reports the check. It holds no secrets and no customer data (the backend address and the job's key are Actions secrets).

- `probe.yml`: one-minute check that the runner has Apple's tools (it did: 25 Oct 2026).
- `build-catalog.yml`: the real job (needs the secrets `CATCHRR_URL` and `CATCHRR_JOB_TOKEN`).
