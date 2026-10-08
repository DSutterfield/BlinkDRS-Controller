# Dog identity refinement — controller 1.2.1, October 8, 2026

Dog suggestions use the existing DINO household classifier and human-confirmed dog references. Cat and person matching are unchanged. Dog naming no longer bypasses its safeguards through the generic nearest-image matcher.

A single canonical image requires score >= 0.8, a >= 0.4 gap over the runner-up, nearest named-reference cosine >= 0.65, and cosine >= 0.60 to confirmed images from at least three other clips. Multiple quality-qualified views can instead agree across >= 0.5 seconds with score >= 0.8, gap >= 0.3, nearest >= 0.65 and support from two other clips per view. Conflicting supported names abstain. Matching explicitly confirmed unknown-dog evidence can veto a suggestion. Scores are not probabilities. Suggestions never confirm themselves or become training references without a human confirmation. The current clip is excluded from ranking and support.

Development replay: 145 confirmed dog crops, Duke and Stubby, excluding each test clip from references. The classifier's first choice was correct on 145/145, but ranking alone is insufficient for naming. The final conservative rule accepted 96 correctly and abstained on 49. In a chronological split reserving the latest 23 clips (36 appearances), it accepted eight correctly and abstained on 28. No accepted errors were observed in these development replays. Thresholds were explored using these historical data, so these are not independent prospective accuracy estimates. Fresh clips are needed to measure field performance. Quality, lighting, background, camera and new dogs can still defeat the model.

The four saved unconfirmed Driveway appearances from clips 22927 and 23232 remained unnamed. Dan clarified that Duke and Stubby normally stay behind the gate but can enter the Driveway if it is open. Clip 20210 correctly shows Dan and Duke after Duke followed him through the gate. Its human-confirmed label is valid. Camera location must not veto either dog; recognizing such escapes is a useful future goal. There are currently no explicitly confirmed unknown dog crops in this snapshot, so unknown-veto behavior is regression-tested but has no field evaluation yet.

New analyses use the new policy. Existing labels, confirmations, crops, profiles, recordings and candidate indexes are preserved; there is no bulk historical relabeling. The Sightings page displays permanent Clip # headings and numbered Play buttons. Windows 1.1.29 shows permanent catalog clip numbers separately from changing list row/position numbers.

Validation: 93 isolated controller regression tests passed. Windows Release built with zero warnings/errors; WPF fixture verified permanent clip IDs, forward/back navigation, list position and installed About 1.1.29 dated 2026-10-08.

## First Duke refresh — October 8, 2026

Reviewed 150 unnamed dog appearances; 41 passed the existing Duke suggestion policy. Started with the 50 most recent eligible appearances, adding seven unconfirmed Duke suggestions across clips 23227, 22931, 22583, 22578 and 22324. Existing confirmations and crop pixels were verified unchanged; clip 23232 stayed unnamed. All five clips were verified through the identity and suggested-sightings APIs. A catalog backup was saved before applying. The maintenance utility defaults to dry-run, updates only unnamed/unconfirmed dogs and skips concurrent evidence/gallery changes. It was executed from staging; the running recognition policy and service versions were unchanged. 98 regression tests passed. Remaining candidates await a later small batch after human review.


## Final status — controller 1.2.6, October 8, 2026

All seven Duke suggestions from the first refresh were withdrawn after species errors were found. They are not active suggestions or training evidence. The earlier refresh results above describe a historical experiment, not the current catalog. Do not refresh another naming batch until species evidence is reviewed.

Species checks now preserve uncertain animal crops as Type Unknown, without a named identity or training reference. Controller 1.2.6 keeps uncertain animal frames in their spatial track and retains supported species views within the four-view budget. See SPECIES_CHECK.md for thresholds, replay caveats, human corrections and the rejected closer-search experiment.

The six-clip paired replay withheld 63 clips and duplicate reviewed images from references. Stubby's 11.5–21.1 second sequence in 23257 now stays Dog; two isolated appearances remain Unknown. Clip 23318 was corrected by Dan to an uncertain probable Cat/Gus, so Unknown is appropriate and the original Dog label is not a definitive evaluation answer. Person/Vehicle results were unchanged. Distant cats and the distant dark Dog remain missed; species recognition is not yet reliable enough to claim complete recovery. 120 regressions passed, all 605 existing confirmed records/crop images were preserved, and installed About verified Windows 1.1.33 with controller 1.2.6.
