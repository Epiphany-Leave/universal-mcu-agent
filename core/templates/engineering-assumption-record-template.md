# Engineering Assumption Record v2: <EAR-ID>

- Mode: `prototype` or `exploration`
- State: `PROPOSED / APPROVED / EXPIRED / REVOKED / VALIDATED`
- Task IDs: <exact Tasks this EAR may unlock>
- Scope: <bounded component or experiment>
- Assumption: <value that remains unconfirmed>
- Risks: <known failure and safety risks>
- Validation method: <how evidence will replace or reject it>
- Owner: <responsible role/person>
- Expires: <date or milestone>
- Rollback: <how to remove the assumption safely>
- Approved by: <explicit owner approval>

The Capability Gate accepts this record only when it is approved, unexpired, mode-compatible, and the current Task ID is listed. It never satisfies a Production release gate or changes an UNKNOWN fact to CONFIRMED.
