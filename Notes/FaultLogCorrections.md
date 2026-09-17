# Fault log corrections — September 17, 2026

- New entries use America/Chicago timestamps (CDT UTC-05 in summer, CST UTC-06 in winter). Existing entries retain their original bytes and UTC timestamps. Retention still compares absolute instants across mixed offsets.
- Replace arbitrary three-digit extraction with explicitly labeled HTTP and reported-code extraction. Ports, camera IDs, durations, and URL query values are not error codes.
- Classify DNS, timeout, TLS, refused-connection and interrupted-connection messages with fixed, credential-safe descriptions. Point to blink_dvr.log for full diagnostics.
- Apply the same classifier to Live View failures.
- 34 isolated Pi tests passed, including the reported port-443 DNS case, redaction, Central summer/winter time, repeated DST hour, mixed-retention behavior, delayed Windows events, API integration, and network recovery.
- Verified live startup timestamp, healthy API, and byte-for-byte preservation of earlier fault entries after deployment.
- No Windows application rebuild is needed; Refresh retrieves the updated Pi file.
